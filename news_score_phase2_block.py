"""news_score_phase2_block.py — Phase 2 候选 B: 大宗交易折溢价 (Block Trade Premium).

⚠️ news_score 维度 REJECTED, sector_picks 短线层 PORTED 2026-05-25 ⚠️
  对 news_score (fwd_20d 目标): 全 panel IC=+0.027, 增量 IC vs fund +3.1% < +5% 门槛 ❌
  对 sector_picks 短线 (fwd_5d 目标): 全 panel IC=+0.123, 增量 IC vs fund +7.3% ✅
  → 已搬到 sector_picks 短线层 (T_window=20d, horizon=5d), 与 news_score 解耦.
  本文件保留作为 Phase 2 评估参考, sector_picks 实际使用代码在
    sector_picks_block_trade.py (P0-2 产出).
  详见: docs/news_score_phase2_design.md (Phase 2 B 决策章节)

设计文档: docs/news_score_phase2_design.md

信号定义 (实施细化版):
  对每笔 block_trade: premium = (block_price - close_on_trade_date) / close * 100
  按 amount 加权聚合到 (ts_code, t):
    block_premium_T_window(stock, t) = Σ_{trade in last_T_days} amount * premium / Σ amount
  concept-level: amount-加权 (跨 stock × 跨 trade)
  z_block_60d = (cur - mean_60d) / std_60d  per concept

  T_window = 20d (而非设计文档的 5d) — 经 probe 验证 5d 太稀疏
  amount 加权 — 大笔交易权重 > 散单
  期望: z_block > 0 (折价小/溢价多) → bull; z_block < 0 (折价深) → bear
        即 pts = clip(z_block, -2, +2) * 1.0

实证调整:
  A-shares block_trade 97% 是折价 (median -7.77%), 所以 baseline 已经是 -7.77% premium
  z_block 衡量的是"是否比 baseline 更折价/更温和". 不需要刻意取负.

cache:
  按 ts_code 拉, 永久 cache 到 .cache/news_block_trade_by_ts.jsonl.
  daily close 用 ~/.homespace/data/market-tools/daily/daily.parquet (DuckDB).
"""
from __future__ import annotations

import csv
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"
_CACHE_DIR = _HERE / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)
_CACHE_BY_TS = _CACHE_DIR / "news_block_trade_by_ts.jsonl"

_DAILY_PARQUET = Path("~/.homespace/data/market-tools/daily/daily.parquet").expanduser()


def _ts_csv(api: str, **params) -> list[dict[str, str]]:
    args = ["python3", str(_TUSHARE), api]
    for k, v in params.items():
        args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def _yyyymmdd(d: datetime) -> str:
    return d.strftime("%Y%m%d")


def fetch_block_trade_by_ts(ts_codes: list[str], verbose: bool = True,
                            ttl_days: int = 14) -> list[dict]:
    """按 ts_code 拉全量 block_trade. 增量 + TTL 14d."""
    today = datetime.now().strftime("%Y%m%d")
    cached_by_ts: dict[str, str] = {}
    if _CACHE_BY_TS.exists():
        with open(_CACHE_BY_TS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts = r.get("ts_code")
                    ca = r.get("_cached_at", "")
                    if ts and ca > cached_by_ts.get(ts, ""):
                        cached_by_ts[ts] = ca
                except Exception:
                    pass

    fresh_threshold = (datetime.now() - timedelta(days=ttl_days)).strftime("%Y%m%d")
    to_fetch = [ts for ts in ts_codes if cached_by_ts.get(ts, "") < fresh_threshold]

    if verbose and to_fetch:
        print(f"  📥 block_trade_by_ts: fetching {len(to_fetch)}/{len(ts_codes)} stocks "
              f"(ttl={ttl_days}d)", file=sys.stderr)

    new_rows: list[dict] = []
    for i, ts in enumerate(to_fetch, 1):
        rows = _ts_csv("block_trade", ts_code=ts)
        if not rows:
            new_rows.append({"ts_code": ts, "_cached_at": today, "_empty": True})
        else:
            for r in rows:
                r["_cached_at"] = today
                new_rows.append(r)
        if verbose and i % 50 == 0:
            print(f"    fetched {i}/{len(to_fetch)} stocks", file=sys.stderr)

    if new_rows:
        refetched_ts = set(to_fetch)
        if _CACHE_BY_TS.exists() and refetched_ts:
            kept: list[dict] = []
            with open(_CACHE_BY_TS) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if r.get("ts_code") not in refetched_ts:
                            kept.append(r)
                    except Exception:
                        pass
            with open(_CACHE_BY_TS, "w") as f:
                for r in kept:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

        with open(_CACHE_BY_TS, "a") as f:
            for r in new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if verbose:
            print(f"  💾 block_trade_by_ts: cached +{len(new_rows)} rows", file=sys.stderr)

    out: list[dict] = []
    want = set(ts_codes)
    if _CACHE_BY_TS.exists():
        with open(_CACHE_BY_TS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("_empty"):
                        continue
                    if r.get("ts_code") in want:
                        out.append(r)
                except Exception:
                    pass
    return out


def fetch_close_prices(pairs: list[tuple[str, str]],
                       verbose: bool = True) -> dict[tuple[str, str], float]:
    """批量取 (ts_code, trade_date) -> close. 用 DuckDB 读 daily.parquet."""
    if not pairs or not _DAILY_PARQUET.exists():
        return {}
    try:
        import duckdb
    except ImportError:
        return {}

    # Group by ts_code -> dates
    by_ts: dict[str, set[str]] = defaultdict(set)
    for ts, td in pairs:
        by_ts[ts].add(td)

    con = duckdb.connect()
    out: dict[tuple[str, str], float] = {}
    for ts, dates in by_ts.items():
        if not dates: continue
        date_str = ",".join(f"'{d}'" for d in dates)
        try:
            q = con.execute(f"""
                SELECT trade_date, close FROM read_parquet('{_DAILY_PARQUET}')
                WHERE ts_code = '{ts}' AND trade_date IN ({date_str})
            """).fetchall()
            for d, c in q:
                if c is not None:
                    try:
                        out[(ts, d)] = float(c)
                    except Exception:
                        pass
        except Exception as e:
            if verbose:
                print(f"    duckdb error for {ts}: {e}", file=sys.stderr)
    con.close()
    if verbose:
        print(f"  📥 fetched {len(out)} close prices for {len(pairs)} pairs "
              f"(coverage {100*len(out)/len(pairs):.0f}%)", file=sys.stderr)
    return out


# ─── 信号计算 ──────────────────────────────────────────────────────────────

def _to_f(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _build_premium_index(rows: list[dict],
                         close_map: dict[tuple[str, str], float],
                         ) -> dict[str, list[tuple[str, float, float]]]:
    """Build per-stock list of (trade_date, premium_pct, amount_wan).

    Returns: {ts_code: [(trade_date, premium, amount), ...]} sorted by trade_date.
    跳过没有 close (无法算 premium) 或 amount 为 0 的行.
    amount 单位是万元 (tushare 默认).
    """
    by_ts: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
    for r in rows:
        ts = r.get("ts_code")
        td = r.get("trade_date")
        if not ts or not td:
            continue
        try:
            p = float(r.get("price", 0))
            amt = float(r.get("amount", 0))
        except Exception:
            continue
        if p <= 0 or amt <= 0:
            continue
        c = close_map.get((ts, td))
        if c is None or c <= 0:
            continue
        prem = (p - c) / c * 100  # %
        by_ts[ts].append((td, prem, amt))

    for ts in by_ts:
        by_ts[ts].sort(key=lambda x: x[0])
    return dict(by_ts)


def compute_concept_block_premium(
    members: list[tuple[str, str]],
    as_of: str,
    by_ts: dict[str, list[tuple[str, float, float]]],
    window_days: int = 20,
) -> tuple[float | None, dict]:
    """concept 在 as_of 这天 amount-weighted block_premium over last window_days.

    Returns:
        (weighted_premium %, detail) or (None, ...) if 0 trades.
    """
    member_codes = [ts for ts, _ in members]
    as_dt = datetime.strptime(as_of, "%Y%m%d")
    start_dt = as_dt - timedelta(days=window_days)

    total_amt = 0.0
    total_pa = 0.0
    n_trades = 0
    n_stocks = 0
    discount_amt = 0.0
    premium_amt = 0.0
    for ts in member_codes:
        trades = by_ts.get(ts, [])
        stock_amt = 0.0
        for td, prem, amt in trades:
            try:
                tdt = datetime.strptime(td, "%Y%m%d")
            except Exception:
                continue
            if start_dt < tdt <= as_dt:
                total_amt += amt
                total_pa += amt * prem
                stock_amt += amt
                n_trades += 1
                if prem < -0.05:
                    discount_amt += amt
                elif prem > 0.05:
                    premium_amt += amt
        if stock_amt > 0:
            n_stocks += 1

    if total_amt == 0 or n_trades == 0:
        return None, {"n_trades": 0, "n_stocks": 0, "amt": 0,
                      "window_days": window_days}

    w_prem = total_pa / total_amt
    return w_prem, {
        "n_trades": n_trades, "n_stocks": n_stocks,
        "total_amt_wan": round(total_amt, 1),
        "discount_amt_wan": round(discount_amt, 1),
        "premium_amt_wan": round(premium_amt, 1),
        "weighted_prem_pct": round(w_prem, 3),
        "window_days": window_days,
    }


def z_block_60d(
    members: list[tuple[str, str]],
    as_of: str,
    by_ts: dict[str, list[tuple[str, float, float]]],
    hist_days: int = 60,
    window_days: int = 20,
) -> tuple[float | None, dict]:
    """对 concept 算 60d rolling z of weighted block_premium.

    Returns (z, diag). z is None if hist 样本 < 20 或 std=0.
    """
    as_dt = datetime.strptime(as_of, "%Y%m%d")
    series: list[float | None] = []
    for back in range(hist_days, 0, -1):
        t = _yyyymmdd(as_dt - timedelta(days=back))
        v, _ = compute_concept_block_premium(members, t, by_ts, window_days)
        series.append(v)

    cur, cur_d = compute_concept_block_premium(members, as_of, by_ts, window_days)
    if cur is None:
        return None, {"reason": "no_current_signal", **cur_d}

    nonNone = [v for v in series if v is not None]
    if len(nonNone) < 10:
        return None, {"reason": "insufficient_history", "n_hist_nonzero": len(nonNone),
                      "cur": cur, **cur_d}

    mu = statistics.mean(nonNone)
    sd = statistics.stdev(nonNone) if len(nonNone) > 1 else 0
    if sd == 0:
        return 0.0, {"cur": cur, "hist_mean": round(mu, 3), "hist_std": 0.0,
                     "n_hist_nonzero": len(nonNone), **cur_d}

    z = (cur - mu) / sd
    return z, {
        "cur": cur, "hist_mean": round(mu, 3), "hist_std": round(sd, 3),
        "n_hist_nonzero": len(nonNone), **cur_d,
    }


# ─── 主入口 ────────────────────────────────────────────────────────────────

def news_score_phase2_block(
    concept: str,
    as_of: str | None = None,
    preloaded_by_ts: dict | None = None,
    window_days: int = 20,
) -> tuple[float, str, dict]:
    """Phase 2 B unlock signal — return pts in [-2, +2] (swing ±1.0)."""
    from concepts_data import stocks_of

    as_of = as_of or datetime.now().strftime("%Y%m%d")
    members = stocks_of(concept) or []
    if not members:
        return 0.0, "(no members)", {"error": "no members", "concept": concept}

    if preloaded_by_ts is None:
        # On-demand fetch — slow path
        member_codes = [ts for ts, _ in members]
        rows = fetch_block_trade_by_ts(member_codes, verbose=False)
        # Need close prices
        pairs = [(r.get("ts_code"), r.get("trade_date")) for r in rows
                 if r.get("ts_code") and r.get("trade_date")]
        close_map = fetch_close_prices(pairs, verbose=False)
        preloaded_by_ts = _build_premium_index(rows, close_map)

    z, diag = z_block_60d(members, as_of, preloaded_by_ts, window_days=window_days)
    if z is None:
        return 0.0, f"insufficient signal ({diag.get('reason','?')})", {
            "concept": concept, "as_of": as_of, "z_block": None,
            "n_members": len(members), **diag,
        }

    z_clip = max(-2.0, min(2.0, z))
    pts = z_clip * 1.0  # 同向: +z (折价更轻 / 溢价) = bull = 加分

    note = (f"block_prem cur={diag['cur']:+.2f}% "
            f"(hist_mean={diag['hist_mean']:+.2f}, std={diag['hist_std']:.2f}) "
            f"z={z:+.2f}σ → pts={pts:+.2f}")
    return round(pts, 3), note, {
        "concept": concept, "as_of": as_of, "z_block": round(z, 3),
        "pts": round(pts, 3), "n_members": len(members),
        "version": "phase2_block_v1", **diag,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--concept", default=None)
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--prefetch-all-concepts", action="store_true")
    ap.add_argument("--prefetch-only", action="store_true")
    ap.add_argument("--window-days", type=int, default=20)
    ap.add_argument("--ttl-days", type=int, default=14)
    args = ap.parse_args()

    as_of = args.as_of or datetime.now().strftime("%Y%m%d")

    if args.prefetch_all_concepts:
        from concepts_data import CONCEPTS
        all_ts = sorted({ts for ms in CONCEPTS.values() for ts, _ in ms})
        print(f"📥 prefetch block_trade for {len(all_ts)} concept members",
              file=sys.stderr)
        rows = fetch_block_trade_by_ts(all_ts, ttl_days=args.ttl_days)
        print(f"  loaded {len(rows)} block_trade rows", file=sys.stderr)
        # Now fetch close prices
        pairs = [(r.get("ts_code"), r.get("trade_date")) for r in rows
                 if r.get("ts_code") and r.get("trade_date")]
        close_map = fetch_close_prices(pairs)
        # Build premium index — but we DON'T cache the index; re-build per call
        idx = _build_premium_index(rows, close_map)
        n_with_prem = sum(len(v) for v in idx.values())
        print(f"  built premium index: {n_with_prem} trades with valid premium",
              file=sys.stderr)

    if args.prefetch_only:
        return

    if args.concept:
        pts, note, diag = news_score_phase2_block(
            args.concept, as_of, window_days=args.window_days)
        print(f"pts: {pts:+.2f}")
        print(f"note: {note}")
        print(json.dumps(diag, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
