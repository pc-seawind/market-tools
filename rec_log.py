"""rec_log.py — Tier 5 后验证: 推荐记录 + T+N 涨跌验证 (framework v2.3).

两个 append-only 文件:
  recommendations.jsonl         每条 rec 一行 (创建时 append)
  recommendations_performance.jsonl  每天为每条 open rec append 一行 T+N 数据

不会 overwrite 任何记录. 规则迭代通过新的 framework_version 标签追踪.

CLI:
  rec_log.py add --code X --name Y --action BUY --sector S --price P ...
     手工添加一条推荐 (通常 sector_picks.py 或 agent 自动调用)
  rec_log.py list [--since YYYY-MM-DD] [--action BUY]
     列出推荐
  rec_log.py verify [--all | --rec-id X] [--date YYYYMMDD]
     拉今日 close, 为每条 open rec (未满 T+20) append 一条 performance
  rec_log.py report [--weeks N]
     aggregate 命中率 / 失败模式 (给周报用)
"""
from __future__ import annotations
import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_REC_FILE = _HERE / "recommendations.jsonl"
_PERF_FILE = _HERE / "recommendations_performance.jsonl"
_TUSHARE = _HERE / "tushare.py"

MAX_VERIFY_HORIZON_DAYS = 20  # T+20 后停止 verify


# ─── I/O helpers ─────────────────────────────────────────────────────────

def _ts_csv(api: str, **params) -> list[dict[str, str]]:
    args = ["python3", str(_TUSHARE), api]
    for k, v in params.items():
        args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─── rec_id 生成 ──────────────────────────────────────────────────────────

def _gen_rec_id() -> str:
    """rec_YYYYMMDD_NNNN 递增."""
    today = date.today().strftime("%Y%m%d")
    existing = _read_jsonl(_REC_FILE)
    same_day = [r for r in existing if r.get("id", "").startswith(f"rec_{today}_")]
    next_n = len(same_day) + 1
    return f"rec_{today}_{next_n:04d}"


# ─── Public API ──────────────────────────────────────────────────────────

def append_rec(
    code: str, name: str, action: str, sector: str,
    *,
    framework_version: str = "v2.3",
    sector_tier1_score: float | None = None,
    fair_value: float | None = None,
    price_at_rec: float | None = None,
    deviation_pct: float | None = None,
    technical_snapshot: dict[str, Any] | None = None,
    reason: str = "",
    source_agent: str = "manual",
    max_position_pct: float | None = None,
) -> str:
    """Append a recommendation. Return rec_id."""
    rec_id = _gen_rec_id()
    rec = {
        "id": rec_id,
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "code": code,
        "name": name,
        "action": action.upper(),  # BUY / SELL / WATCH / EXIT
        "framework_version": framework_version,
        "sector": sector,
        "sector_tier1_score": sector_tier1_score,
        "fair_value": fair_value,
        "price_at_rec": price_at_rec,
        "deviation_pct": deviation_pct,
        "technical_snapshot": technical_snapshot or {},
        "reason": reason,
        "source_agent": source_agent,
        "max_position_pct": max_position_pct,
    }
    _append_jsonl(_REC_FILE, rec)
    return rec_id


def list_recs(since: date | None = None, action: str | None = None) -> list[dict[str, Any]]:
    recs = _read_jsonl(_REC_FILE)
    if since:
        recs = [r for r in recs if _parse_ts_date(r.get("ts", "")) >= since]
    if action:
        recs = [r for r in recs if r.get("action", "").upper() == action.upper()]
    return recs


def _parse_ts_date(ts: str) -> date:
    if not ts:
        return date(2000, 1, 1)
    try:
        return datetime.fromisoformat(ts).date()
    except ValueError:
        return date(2000, 1, 1)


def _fetch_close(ts_code: str, trade_date: str | None = None) -> float | None:
    """Get latest close (or specific trade_date close).

    Routes .HK codes to hk_daily API; A-share codes to daily.
    Falls back to quote_sources (Tencent kline) if tushare returns nothing.
    """
    is_hk = ts_code.upper().endswith(".HK")
    api = "hk_daily" if is_hk else "daily"

    if trade_date:
        rows = _ts_csv(api, ts_code=ts_code, trade_date=trade_date)
    else:
        rows = _ts_csv(api, ts_code=ts_code)

    if rows:
        rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)
        try:
            return float(rows[0]["close"])
        except (KeyError, ValueError):
            pass

    # Fallback: quote_sources daily_bars (tencent kline for HK, mootdx for A)
    try:
        from quote_sources import daily_bars
        bars = daily_bars(ts_code, days=5)
        if bars:
            return float(bars[-1]["close"])
    except Exception:
        pass

    return None


def verify_rec(rec: dict[str, Any], current_close: float | None = None,
               verify_date: str | None = None) -> dict[str, Any] | None:
    """Compute T+N performance for one rec, append to perf file. Return perf record."""
    code = rec.get("code")
    price_at_rec = rec.get("price_at_rec")
    if not code or not price_at_rec:
        return None

    rec_date = _parse_ts_date(rec.get("ts", ""))
    today = date.today()
    days_since = (today - rec_date).days
    if days_since > MAX_VERIFY_HORIZON_DAYS:
        # 已过 T+20 观察期
        return None

    if current_close is None:
        current_close = _fetch_close(code, verify_date)
    if current_close is None:
        return None

    pct_change = (current_close / price_at_rec - 1) * 100

    perf = {
        "rec_id": rec.get("id"),
        "code": code,
        "action": rec.get("action"),
        "verify_ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "verify_date": today.isoformat(),
        "days_since_rec": days_since,
        "price_at_rec": price_at_rec,
        "current_price": current_close,
        "pct_change": round(pct_change, 2),
    }
    _append_jsonl(_PERF_FILE, perf)
    return perf


def verify_all(verify_date: str | None = None) -> dict[str, int]:
    """Verify all open recs. Return counts."""
    recs = _read_jsonl(_REC_FILE)
    stats = {"total": 0, "verified": 0, "expired": 0, "fetch_failed": 0}

    # Group by code to reduce tushare calls (batch same-code recs)
    today = date.today()
    for rec in recs:
        stats["total"] += 1
        rec_date = _parse_ts_date(rec.get("ts", ""))
        if (today - rec_date).days > MAX_VERIFY_HORIZON_DAYS:
            stats["expired"] += 1
            continue
        perf = verify_rec(rec, verify_date=verify_date)
        if perf:
            stats["verified"] += 1
        else:
            stats["fetch_failed"] += 1
    return stats


# ─── Aggregate report ────────────────────────────────────────────────────

def report(weeks: int = 4) -> dict[str, Any]:
    """Aggregate hit rate / avg return / failure modes over last N weeks."""
    recs = _read_jsonl(_REC_FILE)
    perfs = _read_jsonl(_PERF_FILE)

    since = date.today() - timedelta(weeks=weeks)

    # Index perfs by rec_id
    perf_by_rec: dict[str, list[dict[str, Any]]] = {}
    for p in perfs:
        perf_by_rec.setdefault(p.get("rec_id", ""), []).append(p)

    report_data = {
        "since": since.isoformat(),
        "total_recs": 0,
        "by_action": {},
        "by_horizon": {},  # T+5 / T+10 / T+20
    }

    for rec in recs:
        if _parse_ts_date(rec.get("ts", "")) < since:
            continue
        report_data["total_recs"] += 1
        act = rec.get("action", "?").upper()
        report_data["by_action"].setdefault(act, {"count": 0, "codes": []})
        report_data["by_action"][act]["count"] += 1
        report_data["by_action"][act]["codes"].append(rec.get("code"))

        # 对每个 horizon 看命中
        rec_perfs = perf_by_rec.get(rec.get("id", ""), [])
        for horizon in [5, 10, 20]:
            key = f"T+{horizon}"
            matching = [p for p in rec_perfs if p.get("days_since_rec", 0) >= horizon]
            if matching:
                # 取最接近 horizon 的一条
                matching.sort(key=lambda x: abs(x.get("days_since_rec", 0) - horizon))
                p = matching[0]
                pct = p.get("pct_change", 0)
                report_data["by_horizon"].setdefault(key, {"samples": 0, "hits": 0, "avg_pct": 0, "pcts": []})
                report_data["by_horizon"][key]["samples"] += 1
                if act == "BUY" and pct > 0:
                    report_data["by_horizon"][key]["hits"] += 1
                elif act == "SELL" and pct < 0:
                    report_data["by_horizon"][key]["hits"] += 1
                report_data["by_horizon"][key]["pcts"].append(pct)

    # Compute hit rates + avg
    for key, d in report_data["by_horizon"].items():
        if d["samples"] > 0:
            d["hit_rate"] = round(d["hits"] / d["samples"] * 100, 1)
            d["avg_pct"] = round(sum(d["pcts"]) / len(d["pcts"]), 2)
        d.pop("pcts", None)  # drop detail from summary

    return report_data


# ─── CLI ─────────────────────────────────────────────────────────────────

def _cmd_add(args):
    rec_id = append_rec(
        code=args.code, name=args.name, action=args.action, sector=args.sector,
        sector_tier1_score=args.score, fair_value=args.fv, price_at_rec=args.price,
        deviation_pct=args.dev, reason=args.reason, source_agent=args.by,
        max_position_pct=args.max_pos,
    )
    print(f"added {rec_id}")


def _cmd_list(args):
    since = None
    if args.since:
        since = datetime.fromisoformat(args.since).date()
    recs = list_recs(since=since, action=args.action)
    for r in recs:
        dev = r.get("deviation_pct")
        dev_s = f"{dev:+.1f}%" if dev is not None else "-"
        score = r.get("sector_tier1_score")
        print(f"  {r['id']}  {r['ts'][:10]}  [{r['action']:<6}] {r['code']} {r.get('name','?')[:8]:<8}  "
              f"sector={r.get('sector','?')[:18]:<18}  tier1={score if score else '-':>4}  "
              f"price={r.get('price_at_rec','-')}  dev={dev_s}  {r.get('reason','')[:60]}")


def _cmd_verify(args):
    if args.rec_id:
        recs = [r for r in _read_jsonl(_REC_FILE) if r.get("id") == args.rec_id]
        if not recs:
            print(f"rec {args.rec_id} not found")
            sys.exit(1)
        for rec in recs:
            perf = verify_rec(rec, verify_date=args.date)
            print(json.dumps(perf, ensure_ascii=False, indent=2) if perf else "failed")
    else:
        stats = verify_all(verify_date=args.date)
        print(f"verify_all: total={stats['total']} verified={stats['verified']} "
              f"expired={stats['expired']} fetch_failed={stats['fetch_failed']}")


def _cmd_report(args):
    r = report(weeks=args.weeks)
    print(f"\n=== Rec Performance Report (since {r['since']}) ===")
    print(f"Total recs: {r['total_recs']}")
    print(f"\nBy action:")
    for act, d in r["by_action"].items():
        print(f"  {act:<6}  count={d['count']:<3}  codes={', '.join(d['codes'][:5])}{'...' if len(d['codes'])>5 else ''}")
    print(f"\nBy horizon (hit rate = BUY 涨 / SELL 跌):")
    for key in ["T+5", "T+10", "T+20"]:
        if key in r["by_horizon"]:
            d = r["by_horizon"][key]
            print(f"  {key}  samples={d['samples']:<3}  hit_rate={d.get('hit_rate','-')}%  avg_pct={d.get('avg_pct','-')}%")


def main():
    ap = argparse.ArgumentParser(description="Tier 5 recommendation log + performance verify.")
    sp = ap.add_subparsers(dest="cmd", required=True)

    a = sp.add_parser("add", help="Append one recommendation")
    a.add_argument("--code", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--action", required=True, choices=["BUY", "SELL", "WATCH", "EXIT", "HOLD"])
    a.add_argument("--sector", required=True)
    a.add_argument("--score", type=float, help="Tier 1 sector score")
    a.add_argument("--fv", type=float, help="fair_value")
    a.add_argument("--price", type=float, help="price_at_rec")
    a.add_argument("--dev", type=float, help="deviation_pct")
    a.add_argument("--max-pos", type=float, help="max_position_pct")
    a.add_argument("--reason", default="")
    a.add_argument("--by", default="manual", help="source_agent")

    l = sp.add_parser("list", help="List recommendations")
    l.add_argument("--since", help="YYYY-MM-DD")
    l.add_argument("--action")

    v = sp.add_parser("verify", help="Compute T+N for open recs")
    v.add_argument("--rec-id", help="verify a specific rec (default: all)")
    v.add_argument("--date", help="verify against specific YYYYMMDD close (default: latest)")
    v.add_argument("--all", action="store_true", help="verify all open recs (default behavior)")

    r = sp.add_parser("report", help="Aggregate hit rate / avg return")
    r.add_argument("--weeks", type=int, default=4)

    args = ap.parse_args()
    {"add": _cmd_add, "list": _cmd_list, "verify": _cmd_verify, "report": _cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
