#!/usr/bin/env python3
"""Leakage-controlled P2 ablations on top of narrative_backtest exact-D14 rows.

All P2 features use bars at or before ``pre_bar_date``. The optional confirmation
strategy observes only the first tradable session OHLC, enters at that close,
and recomputes stock / CSI300 / sector returns from the shifted close anchor.
It never reuses the original open-anchored excess label after shifting entry.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import os
import statistics
import subprocess
from collections import defaultdict, OrderedDict
from pathlib import Path
from typing import Any, Iterable

import narrative_backtest as p1

HERE = Path(__file__).resolve().parent
TUSHARE = HERE / "tushare.py"
CACHE_ROOT = Path.home() / ".homespace/cache/market-tools/daily"
DATA_ROOT = Path.home() / ".homespace/data/market-tools"
P2_CACHE = HERE / ".cron_state/narrative_backtest_p2_features.jsonl"

_SNAPSHOTS: OrderedDict[str, dict[str, dict[str, float]]] = OrderedDict()
_SNAPSHOT_CAP = 12
_STOCK_BASIC: dict[str, dict[str, str]] | None = None
_TRADE_DATES: list[str] | None = None
_INDEX_BARS: dict[str, dict[str, dict[str, float]]] = {}


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _safe_float(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _cache_key(api: str, params: dict, fields: str = "") -> str:
    payload = {"api": api, "params": params, "fields": fields}
    return hashlib.md5(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _body_rows(body: dict) -> list[dict]:
    data = body.get("data") or {}
    fields = data.get("fields") or []
    return [dict(zip(fields, x)) for x in data.get("items") or []]


def daily_snapshot(trade_date: str) -> dict[str, dict[str, float]]:
    """Full-market historical snapshot for an exact open date; fail closed."""
    if trade_date in _SNAPSHOTS:
        value = _SNAPSHOTS.pop(trade_date)
        _SNAPSHOTS[trade_date] = value
        return value
    path = CACHE_ROOT / (_cache_key("daily", {"trade_date": trade_date}) + ".json")
    rows: list[dict] = []
    if path.exists():
        try:
            rows = _body_rows(json.loads(path.read_text()))
        except (OSError, ValueError):
            rows = []
    if not rows:
        if not os.environ.get("TUSHARE_TOKEN"):
            raise RuntimeError(f"missing full daily snapshot {trade_date} and no TUSHARE_TOKEN")
        cmd = ["python3", str(TUSHARE), "daily", f"trade_date={trade_date}", "--csv"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode:
            raise RuntimeError(f"daily snapshot {trade_date} failed: {proc.stderr.strip()[-300:]}")
        rows = list(csv.DictReader(proc.stdout.splitlines()))
    if not rows:
        raise RuntimeError(f"daily snapshot {trade_date} is empty")
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        code = (r.get("ts_code") or "").strip()
        if not code:
            continue
        bar = {k: v for k in ("open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount")
               if (v := _safe_float(r.get(k))) is not None}
        if bar.get("close") is not None:
            out[code] = bar
    if not out:
        raise RuntimeError(f"daily snapshot {trade_date} has no usable bars")
    _SNAPSHOTS[trade_date] = out
    while len(_SNAPSHOTS) > _SNAPSHOT_CAP:
        _SNAPSHOTS.popitem(last=False)
    return out


def stock_basic() -> dict[str, dict[str, str]]:
    global _STOCK_BASIC
    if _STOCK_BASIC is not None:
        return _STOCK_BASIC
    path = DATA_ROOT / "stock_basic/stock_basic.parquet"
    try:
        import duckdb
        rows = duckdb.connect().execute(
            "select ts_code,name,industry from read_parquet(?)", [str(path)]
        ).fetchall()
    except Exception as exc:
        raise RuntimeError(f"stock_basic parquet unavailable: {exc}") from exc
    _STOCK_BASIC = {str(code): {"name": str(name or ""), "industry": str(ind or "")}
                    for code, name, ind in rows}
    return _STOCK_BASIC


def industry_members(code: str) -> tuple[str, list[str]]:
    basic = stock_basic()
    industry = (basic.get(code) or {}).get("industry", "")
    members = [c for c, row in basic.items() if industry and row.get("industry") == industry]
    return industry, members


def trade_dates() -> list[str]:
    global _TRADE_DATES
    if _TRADE_DATES is not None:
        return _TRADE_DATES
    path = DATA_ROOT / "trade_cal/trade_cal.parquet"
    try:
        import duckdb
        rows = duckdb.connect().execute(
            "select distinct cal_date from read_parquet(?) where is_open=1 order by cal_date", [str(path)]
        ).fetchall()
    except Exception as exc:
        raise RuntimeError(f"trade calendar unavailable: {exc}") from exc
    _TRADE_DATES = [str(x[0]) for x in rows]
    return _TRADE_DATES


def date_window(end: str, n: int) -> list[str]:
    ds = trade_dates()
    eligible = [x for x in ds if x <= end]
    return eligible[-n:]


def on_or_before(day: str) -> str | None:
    xs = date_window(day, 1)
    return xs[-1] if xs else None


def index_bar(code: str, day: str) -> dict[str, float] | None:
    actual = on_or_before(day)
    if not actual:
        return None
    if code not in _INDEX_BARS:
        path = DATA_ROOT / "index_daily/index_daily.parquet"
        try:
            import duckdb
            rows = duckdb.connect().execute(
                "select trade_date,open,high,low,close,pre_close,pct_chg from read_parquet(?) where ts_code=?",
                [str(path), code],
            ).fetchall()
        except Exception as exc:
            raise RuntimeError(f"index parquet unavailable: {exc}") from exc
        keys = ("open", "high", "low", "close", "pre_close", "pct_chg")
        _INDEX_BARS[code] = {str(r[0]): {k: float(v) for k, v in zip(keys, r[1:]) if v is not None}
                             for r in rows}
    return _INDEX_BARS[code].get(actual)


def _member_returns(members: Iterable[str], start: dict, end: dict) -> list[float]:
    out = []
    for code in members:
        a = (start.get(code) or {}).get("close")
        b = (end.get(code) or {}).get("close")
        if a and b:
            out.append((b / a - 1) * 100)
    return out


def sector_features(code: str, pre_bar_date: str) -> dict[str, Any]:
    industry, members = industry_members(code)
    dates = date_window(pre_bar_date, 121)
    if not industry or len(dates) < 61:
        return {}
    # Stream snapshots to bound memory; retain only the two start anchors + end.
    index = [100.0]
    amounts: list[float] = []
    snap20 = snap60 = end = None
    for j, d in enumerate(dates):
        bars = daily_snapshot(d)
        if j == len(dates) - 21:
            snap20 = bars
        if j == len(dates) - 61:
            snap60 = bars
        if j == len(dates) - 1:
            end = bars
        day_rets = [bars[c].get("pct_chg") for c in members if c in bars and bars[c].get("pct_chg") is not None]
        if day_rets:
            index.append(index[-1] * (1 + statistics.mean(day_rets) / 100))
        amts = [bars[c].get("amount") for c in members if c in bars and bars[c].get("amount") is not None]
        amounts.append(sum(amts) if amts else 0.0)
    r20 = _member_returns(members, snap20 or {}, end or {})
    r60 = _member_returns(members, snap60 or {}, end or {})
    breadth20 = sum(x > 0 for x in r20) / len(r20) * 100 if r20 else None
    lo, hi = min(index[-121:]), max(index[-121:])
    position = (index[-1] - lo) / (hi - lo) * 100 if hi > lo else 50.0
    a5 = statistics.mean(amounts[-5:]) if len(amounts) >= 5 else None
    a20 = statistics.mean(amounts[-20:]) if len(amounts) >= 20 else None
    return {
        "industry_bt": industry,
        "sector_ret_20": statistics.mean(r20) if r20 else None,
        "sector_ret_60": statistics.mean(r60) if r60 else None,
        "sector_breadth_20": breadth20,
        "sector_position_120d": position,
        "sector_amount_ratio_5_20": a5 / a20 if a5 is not None and a20 else None,
        "stock_excess_sector_20": None,  # filled by caller from stock pre_ret_20
        "sector_n": len(r20),
    }


def stock_risk_features(code: str, pre_bar_date: str) -> dict[str, Any]:
    dates = date_window(pre_bar_date, 61)
    if len(dates) < 21:
        return {}
    bars = [(d, daily_snapshot(d).get(code)) for d in dates]
    bars = [(d, b) for d, b in bars if b and b.get("close")]
    if len(bars) < 21:
        return {}
    closes = [b["close"] for _, b in bars]
    day_rets = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes))]
    peak = closes[0]
    max_dd = 0.0
    for x in closes:
        peak = max(peak, x)
        max_dd = min(max_dd, (x / peak - 1) * 100)
    trs = []
    for _, b in bars[-20:]:
        h, l, pc = b.get("high"), b.get("low"), b.get("pre_close")
        if h is not None and l is not None and pc:
            trs.append(max(h - l, abs(h - pc), abs(l - pc)) / pc * 100)
    return {
        "pre_volatility_20": statistics.pstdev(day_rets[-20:]) if len(day_rets) >= 20 else None,
        "pre_max_drawdown_60": max_dd,
        "pre_atr_pct_20": statistics.mean(trs) if trs else None,
    }


def sector_return(code: str, start_date: str, end_date: str) -> float | None:
    _, members = industry_members(code)
    if not members:
        return None
    rs = _member_returns(members, daily_snapshot(start_date), daily_snapshot(end_date))
    return statistics.mean(rs) if rs else None


def confirmation(row: dict) -> dict[str, Any]:
    """Observe baseline session, enter at close, recompute exact endpoint returns."""
    base_date = row.get("baseline_date")
    verify_date = on_or_before(row.get("verify_date", ""))
    code = row["code"]
    if not base_date or not verify_date:
        return {}
    base_bar = daily_snapshot(base_date).get(code)
    end_bar = daily_snapshot(verify_date).get(code)
    if not base_bar or not end_bar:
        return {}
    o, h, l, c = (base_bar.get(k) for k in ("open", "high", "low", "close"))
    if not all(x is not None and x > 0 for x in (o, h, l, c)):
        return {}
    _, members = industry_members(code)
    day_rets = [daily_snapshot(base_date)[x].get("pct_chg") for x in members
                if x in daily_snapshot(base_date) and daily_snapshot(base_date)[x].get("pct_chg") is not None]
    sector_day = statistics.mean(day_rets) if day_rets else None
    stock_day = base_bar.get("pct_chg")
    gap = (o / base_bar["pre_close"] - 1) * 100 if base_bar.get("pre_close") else None
    close_location = (c - l) / (h - l) if h > l else 0.5

    stock_ret = (end_bar["close"] / c - 1) * 100
    ib0, ib1 = index_bar("000300.SH", base_date), index_bar("000300.SH", verify_date)
    benchmark_ret = (ib1["close"] / ib0["close"] - 1) * 100 if ib0 and ib1 else None
    sec_ret = sector_return(code, base_date, verify_date)
    side = row.get("side", "+")
    sign = 1 if side == "+" else -1
    signed_excess = sign * (stock_ret - benchmark_ret) if benchmark_ret is not None else None
    signed_sector = sign * (stock_ret - sec_ret) if sec_ret is not None else None
    confirmed = (
        c > o and stock_day is not None and sector_day is not None
        and stock_day > sector_day and close_location >= 0.50
        and gap is not None and gap <= 7.0
    )
    return {
        "confirm_trade_date": base_date,
        "confirm_close_gt_open": c > o,
        "confirm_close_location": close_location,
        "confirm_gap_pct": gap,
        "confirm_stock_day_pct": stock_day,
        "confirm_sector_day_pct": sector_day,
        "confirm_excess_sector_day": stock_day - sector_day if stock_day is not None and sector_day is not None else None,
        "confirm_pass": confirmed,
        "confirm_entry_close": c,
        "confirm_signed_excess": signed_excess,
        "confirm_signed_sector": signed_sector,
        "confirm_stock_return": sign * stock_ret,
    }


def enrich(rows: list[dict], refresh: bool = False) -> list[dict]:
    cached: dict[tuple[str, str], dict] = {}
    if P2_CACHE.exists() and not refresh:
        for x in p1.read_jsonl(P2_CACHE):
            cached[(x["event_ts"], x["code"])] = x
    out, changed = [], False
    for i, row in enumerate(rows, 1):
        key = (row["event_ts"], row["code"])
        feat = cached.get(key)
        if feat is None:
            # Expensive P2 history only matters after the fixed P1 + rank-1 gate.
            if base_filter(row, "p1_rank1"):
                sf = sector_features(row["code"], row["pre_bar_date"])
                if sf.get("sector_ret_20") is not None and row.get("pre_ret_20") is not None:
                    sf["stock_excess_sector_20"] = row["pre_ret_20"] - sf["sector_ret_20"]
                feat = {"event_ts": row["event_ts"], "code": row["code"],
                        **sf, **stock_risk_features(row["code"], row["pre_bar_date"]),
                        **confirmation(row)}
            else:
                feat = {"event_ts": row["event_ts"], "code": row["code"]}
            cached[key] = feat
            changed = True
        out.append({**row, **feat})
        if i % 20 == 0:
            print(f"P2 features {i}/{len(rows)}", flush=True)
    if changed:
        P2_CACHE.parent.mkdir(exist_ok=True)
        tmp = P2_CACHE.with_suffix(P2_CACHE.suffix + ".tmp")
        tmp.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in cached.values()) + "\n")
        tmp.replace(P2_CACHE)
    return out


def base_filter(r: dict, kind: str) -> bool:
    if kind == "mapping_rank1":
        return p1.filt(r, "mapping") and r.get("ticker_rank") == 1
    if kind == "p1_rank1":
        return p1.filt(r, "p1") and r.get("ticker_rank") == 1
    if kind == "p2_sector":
        # Experimental only: the historical sample does not support production use.
        return base_filter(r, "p1_rank1") and r.get("sector_position_120d", 101) <= 80 \
            and r.get("sector_ret_20", 999) <= 15 and r.get("sector_breadth_20", 101) <= 75
    if kind == "p2_volatility":
        # Candidate left-tail control; must stay shadow-only until forward sample grows.
        return base_filter(r, "p1_rank1") and r.get("pre_volatility_20", 999) <= 4.0
    if kind == "p2_risk":
        return base_filter(r, "p1_rank1") and r.get("pre_volatility_20", 999) <= 4.0 \
            and r.get("pre_atr_pct_20", 999) <= 5.0 and r.get("pre_max_drawdown_60", -999) >= -30
    if kind == "p2_confirm":
        return base_filter(r, "p1_rank1") and bool(r.get("confirm_pass"))
    return False


def metrics(rows: list[dict], confirmation_mode: bool = False) -> dict[str, Any]:
    ex_key = "confirm_signed_excess" if confirmation_mode else "signed_excess"
    sec_key = "confirm_signed_sector" if confirmation_mode else "signed_sector"
    xs = [r.get(ex_key) for r in rows if r.get(ex_key) is not None]
    ss = [r.get(sec_key) for r in rows if r.get(sec_key) is not None]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs), "events": len({r["event_ts"] for r in rows}),
        "hit_rate": sum(x > 0 for x in xs) / len(xs) * 100,
        "median_excess": _median(xs), "mean_excess": statistics.mean(xs),
        "strict_rate": (sum(x > 0 and s > 0 for x, s in zip(xs, ss)) / len(xs) * 100) if len(ss) == len(xs) else None,
        "median_sector": _median(ss), "p10": sorted(xs)[max(0, math.ceil(.1 * len(xs)) - 1)],
    }


def print_table(rows: list[dict]) -> None:
    print("| strategy | n | events | hit% | median excess | mean excess | strict% | median vs sector | p10 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for kind in ("mapping_rank1", "p1_rank1", "p2_sector", "p2_volatility", "p2_risk", "p2_confirm"):
        selected = [r for r in rows if base_filter(r, kind)]
        m = metrics(selected, confirmation_mode=kind == "p2_confirm")
        def f(k: str) -> str:
            v = m.get(k)
            return "NA" if v is None else f"{v:+.2f}%"
        print(f"| {kind} | {m.get('n', 0)} | {m.get('events', 0)} | {m.get('hit_rate', 0):.1f} | "
              f"{f('median_excess')} | {f('mean_excess')} | {m.get('strict_rate', 0) or 0:.1f} | "
              f"{f('median_sector')} | {f('p10')} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=14)
    ap.add_argument("--base-json", default="")
    ap.add_argument("--json-out", default="")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    if args.base_json:
        rows = p1.read_jsonl(Path(args.base_json))
    else:
        rows = p1.build(args.horizon)
    if not rows:
        raise RuntimeError("empty base dataset")
    # Backtest rows need these outcome anchor fields; older base JSON can be rehydrated.
    perf = {(x["event_ts"], x["code"]): x for x in p1.read_jsonl(p1.PERF)
            if x.get("days_since_event") == args.horizon}
    rows = [{**r,
             "baseline_date": perf[(r["event_ts"], r["code"])].get("baseline_date"),
             "verify_date": perf[(r["event_ts"], r["code"])].get("verify_date"),
             "side": perf[(r["event_ts"], r["code"])].get("side", "+")}
            for r in rows if (r["event_ts"], r["code"]) in perf]
    enriched = enrich(rows, args.refresh)
    print(f"dataset {len(enriched)} exact-D{args.horizon}")
    print_table(enriched)
    if args.json_out:
        Path(args.json_out).write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in enriched) + "\n")


if __name__ == "__main__":
    main()
