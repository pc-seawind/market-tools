"""narrative_track.py — narrative_radar 后期追踪 (T+N 验证).

镜像 rec_log.py 的设计, 但每条 event 可能挂多个 ticker, 所以是
(event_ts, ticker_code) 二元组级别的 perf 记录.

文件:
  narrative_events.jsonl       agent 推 events (radar 写, 这里只读)
  narrative_perf.jsonl         本脚本 append. 每天为每个 (open event, ticker) append 一行

每条 perf record schema:
  {
    "event_ts":          "2026-05-23T19:38:51+08:00",   # 锚定原 event (radar 写入时刻)
    "event_trade_date":  "20260523",                     # radar 收集日 (我加进 events 那天)
    "event_pub_date":    "20260424",                     # 新闻原始发布日 (baseline 锚)
    "baseline_date":     "20260424",                     # 实际用作 baseline 的日期 (=pub_date or trade_date)
    "event_score":       3,
    "event_track":       "AI",
    "event_subdomain":   "ai__compute_chip",
    "event_title":       "...",                          # 80 字截断, 方便人看
    "code":              "688256.SH",
    "name":              "寒武纪",
    "side":              "+" / "-",
    "verify_ts":         "2026-05-24T18:45:00+08:00",
    "verify_date":       "20260524",
    "days_since_event":  30,                             # 自然日, 从 baseline_date 起算 (不是 trade_date)
    "baseline_price":    510.20,                         # baseline_date 当日收盘 (T+0)
    "current_price":     525.30,
    "absolute_pct":      +2.96,                          # vs baseline
    "benchmark":         "000300.SH" / "HSI",
    "benchmark_baseline": 4845.10,
    "benchmark_current":  4870.20,
    "benchmark_pct":     +0.52,
    "excess_pct":        +2.44,                          # absolute - benchmark, 主判定指标
    "hit":               True,                           # side=+ → excess>0; side=- → excess<0
  }

baseline 锚定原则:
  baseline_date = event.pub_date (如果 backfill 过) 否则 event.trade_date
  这样真实新闻发布日做 anchor, 不被"我什么时候 cron 抓到"扰动.

T+N 里程碑: 5/10/20/40 自然日. 超过 60 自然日的 event 不再 verify (close).
注: 阈值放宽至 60 (原 45) — backfill 后部分 event pub_date 较老 (如 4/22 / 4/24).

CLI:
  narrative_track.py verify [--all | --event-ts X] [--date YYYYMMDD]
  narrative_track.py report [--weeks N]
  narrative_track.py doc [--weeks N]
  narrative_track.py event_report --event-ts X
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_EVENTS_PATH = _HERE / "narrative_events.jsonl"
_PERF_PATH = _HERE / "narrative_perf.jsonl"
_TUSHARE = _HERE / "tushare.py"

CN_TZ = dt.timezone(dt.timedelta(hours=8))

MAX_HORIZON_DAYS = 60  # 到 T+60 自然日 (~T+40 交易日) 停止验证 (放宽以容纳 backfill 的老 event)
MILESTONE_DAYS = [5, 10, 20, 40]  # 报告固定窗口


def _baseline_date_of(event: dict) -> str:
    """统一从 event 取 baseline 锚定日期: pub_date > trade_date.

    backfill 过的 event 有 pub_date (新闻原始发布日), 是更准的市场反应起点;
    没 backfill 的回退到 trade_date (radar 收集日, 旧逻辑).
    """
    pd = event.get("pub_date")
    if pd and isinstance(pd, str) and len(pd) == 8 and pd.isdigit():
        return pd
    return event.get("trade_date", "")

BENCHMARK_A = "000300.SH"  # CSI300
BENCHMARK_HK = "HSI"       # 恒指 (走 index_global)


# ─── I/O ─────────────────────────────────────────────────────────────────

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


# ─── price fetchers ─────────────────────────────────────────────────────

def _fetch_stock_close(ts_code: str, trade_date: Optional[str] = None) -> Optional[float]:
    """单只股票的指定日期 close. trade_date=None 取最新."""
    is_hk = ts_code.upper().endswith(".HK")
    api = "hk_daily" if is_hk else "daily"

    rows = _ts_csv(api, ts_code=ts_code, trade_date=trade_date) if trade_date \
        else _ts_csv(api, ts_code=ts_code)

    if rows:
        rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)
        try:
            return float(rows[0]["close"])
        except (KeyError, ValueError):
            pass

    # fallback: quote_sources daily_bars
    try:
        from quote_sources import daily_bars
        bars = daily_bars(ts_code, days=10)
        if not bars:
            return None
        if trade_date:
            for b in bars:
                if str(b.get("trade_date", "")).replace("-", "") == trade_date:
                    return float(b["close"])
            # 找不到精确日期, 返回 ≤trade_date 最近的
            cands = [b for b in bars if str(b.get("trade_date", "")).replace("-", "") <= trade_date]
            if cands:
                return float(cands[-1]["close"])
            return None
        return float(bars[-1]["close"])
    except Exception:
        return None


def _fetch_benchmark_close(market: str, trade_date: Optional[str] = None) -> tuple[str, Optional[float]]:
    """benchmark close. market='A' → CSI300 via index_daily; 'HK' → HSI via index_global.

    trade_date 找不到 (周末/节假日) 时, fallback 到 ≤trade_date 的最近交易日.
    """
    if market == "HK":
        bench = BENCHMARK_HK
        api = "index_global"
    else:
        bench = BENCHMARK_A
        api = "index_daily"

    # 1) 精确 trade_date
    if trade_date:
        rows = _ts_csv(api, ts_code=bench, trade_date=trade_date)
        if rows:
            try:
                return bench, float(rows[0]["close"])
            except (KeyError, ValueError):
                pass
        # 2) fallback: 拉最近 7 天, 取 ≤trade_date 的最近一条
        start = (dt.datetime.strptime(trade_date, "%Y%m%d").date() - dt.timedelta(days=10)).strftime("%Y%m%d")
        rows = _ts_csv(api, ts_code=bench, start_date=start, end_date=trade_date)
        if rows:
            rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)
            try:
                return bench, float(rows[0]["close"])
            except (KeyError, ValueError):
                pass
        return bench, None

    # 无 trade_date: 取最新
    rows = _ts_csv(api, ts_code=bench)
    if not rows:
        return bench, None
    rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)
    try:
        return bench, float(rows[0]["close"])
    except (KeyError, ValueError):
        return bench, None


def _market_of(ts_code: str) -> str:
    return "HK" if ts_code.upper().endswith(".HK") else "A"


# ─── core verify ────────────────────────────────────────────────────────

def _existing_perf_keys() -> set[tuple[str, str, int]]:
    """避免重复 append 同一个 (event_ts, code, days_since_event)."""
    seen = set()
    for p in _read_jsonl(_PERF_PATH):
        seen.add((p.get("event_ts", ""), p.get("code", ""), p.get("days_since_event", -1)))
    return seen


def _baseline_for(event_ts: str, code: str) -> Optional[float]:
    """从 perf 历史里捞已经存的 baseline (avoid 重复拉 tushare)."""
    for p in _read_jsonl(_PERF_PATH):
        if p.get("event_ts") == event_ts and p.get("code") == code:
            v = p.get("baseline_price")
            if v is not None:
                return float(v)
    return None


def _benchmark_baseline_for(event_ts: str, code: str) -> Optional[float]:
    for p in _read_jsonl(_PERF_PATH):
        if p.get("event_ts") == event_ts and p.get("code") == code:
            v = p.get("benchmark_baseline")
            if v is not None:
                return float(v)
    return None


def _truncate(s: str, n: int = 80) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + "…"


def verify_event_ticker(event: dict, ticker: dict, verify_date: Optional[str] = None,
                        cached_baseline: dict | None = None,
                        seen_keys: set | None = None) -> Optional[dict]:
    """计算 (event, ticker) 一对的 T+N perf, append 到 narrative_perf.jsonl.

    幂等: 同 (event_ts, code, days_since_event) 已存在则跳过.
    cached_baseline: dict[(event_ts, code)] -> baseline_price 提前缓存避免重复拉.
    """
    event_ts = event.get("ts", "")
    event_trade_date = event.get("trade_date", "")
    baseline_date = _baseline_date_of(event)
    event_pub_date = event.get("pub_date", "")  # 可能为空 (未 backfill)
    code = ticker.get("code", "")
    if not event_ts or not baseline_date or not code:
        return None

    # 计算 days_since_event (自然日, 从 baseline_date 起算)
    try:
        baseline_d = dt.datetime.strptime(baseline_date, "%Y%m%d").date()
    except ValueError:
        return None
    today = dt.date.today() if not verify_date else dt.datetime.strptime(verify_date, "%Y%m%d").date()
    days_since = (today - baseline_d).days
    if days_since < 0 or days_since > MAX_HORIZON_DAYS:
        return None  # 未来 / 已过期

    # 幂等检查
    key = (event_ts, code, days_since)
    if seen_keys is not None and key in seen_keys:
        return None

    market = _market_of(code)

    # baseline = baseline_date 当日 close (从缓存 / perf 历史 / 拉取)
    cache_key = (event_ts, code)
    baseline = None
    if cached_baseline and cache_key in cached_baseline:
        baseline = cached_baseline[cache_key]
    if baseline is None:
        baseline = _baseline_for(event_ts, code)
    if baseline is None:
        baseline = _fetch_stock_close(code, trade_date=baseline_date)
        if baseline is None:
            return None
        if cached_baseline is not None:
            cached_baseline[cache_key] = baseline

    # current = verify_date close (None = 最新)
    current = _fetch_stock_close(code, trade_date=verify_date)
    if current is None:
        return None

    # benchmark
    bench_baseline = _benchmark_baseline_for(event_ts, code)
    if bench_baseline is None:
        _, bench_baseline = _fetch_benchmark_close(market, trade_date=baseline_date)
    bench_name, bench_current = _fetch_benchmark_close(market, trade_date=verify_date)

    abs_pct = (current / baseline - 1) * 100 if baseline else 0.0
    bench_pct = ((bench_current / bench_baseline - 1) * 100) if (bench_baseline and bench_current) else None
    excess_pct = (abs_pct - bench_pct) if bench_pct is not None else None

    # hit 判定
    side = ticker.get("side", "+")
    if excess_pct is not None:
        hit = (side == "+" and excess_pct > 0) or (side == "-" and excess_pct < 0)
    else:
        hit = (side == "+" and abs_pct > 0) or (side == "-" and abs_pct < 0)

    perf = {
        "event_ts":          event_ts,
        "event_trade_date":  event_trade_date,
        "event_pub_date":    event_pub_date,
        "baseline_date":     baseline_date,
        "event_score":       event.get("score"),
        "event_track":       event.get("track"),
        "event_subdomain":   event.get("subdomain"),
        "event_title":       _truncate(event.get("title", ""), 80),
        "code":              code,
        "name":              ticker.get("name", ""),
        "side":              side,
        "verify_ts":         dt.datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "verify_date":       today.strftime("%Y%m%d"),
        "days_since_event":  days_since,
        "baseline_price":    round(baseline, 4),
        "current_price":     round(current, 4),
        "absolute_pct":      round(abs_pct, 2),
        "benchmark":         bench_name,
        "benchmark_baseline": round(bench_baseline, 4) if bench_baseline else None,
        "benchmark_current":  round(bench_current, 4) if bench_current else None,
        "benchmark_pct":     round(bench_pct, 2) if bench_pct is not None else None,
        "excess_pct":        round(excess_pct, 2) if excess_pct is not None else None,
        "hit":               hit,
    }
    _append_jsonl(_PERF_PATH, perf)
    if seen_keys is not None:
        seen_keys.add(key)
    return perf


def verify_all(verify_date: Optional[str] = None) -> dict[str, int]:
    """跑所有 open event × ticker. 返回统计."""
    events = _read_jsonl(_EVENTS_PATH)
    seen = _existing_perf_keys()
    cached_baseline: dict[tuple[str, str], float] = {}
    stats = {
        "events_total": len(events),
        "events_open": 0,
        "events_expired": 0,
        "ticker_pairs_total": 0,
        "verified": 0,
        "skipped_dup": 0,
        "fetch_failed": 0,
    }

    today = dt.date.today() if not verify_date else dt.datetime.strptime(verify_date, "%Y%m%d").date()

    for event in events:
        baseline_date = _baseline_date_of(event)
        try:
            baseline_d = dt.datetime.strptime(baseline_date, "%Y%m%d").date()
        except ValueError:
            continue
        days = (today - baseline_d).days
        if days > MAX_HORIZON_DAYS:
            stats["events_expired"] += 1
            continue
        if days < 0:
            continue
        stats["events_open"] += 1

        for ticker in event.get("tickers", []):
            stats["ticker_pairs_total"] += 1
            key = (event.get("ts", ""), ticker.get("code", ""), days)
            if key in seen:
                stats["skipped_dup"] += 1
                continue
            perf = verify_event_ticker(event, ticker, verify_date=verify_date,
                                       cached_baseline=cached_baseline, seen_keys=seen)
            if perf:
                stats["verified"] += 1
            else:
                stats["fetch_failed"] += 1
    return stats


# ─── report aggregation ────────────────────────────────────────────────

def _milestone_perfs_by_ticker(perfs: list[dict]) -> dict:
    """对每个 (event_ts, code) 聚合各 milestone 的最优 perf record."""
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in perfs:
        by_pair[(p["event_ts"], p["code"])].append(p)

    out = {}
    for pair, plist in by_pair.items():
        plist.sort(key=lambda x: x["days_since_event"])
        per_milestone = {}
        for ms in MILESTONE_DAYS:
            # 取 days_since_event >= ms 的最早一条 (T+5 = >=5 第一天的快照)
            cands = [p for p in plist if p["days_since_event"] >= ms]
            if cands:
                per_milestone[f"T+{ms}"] = cands[0]
        out[pair] = {"latest": plist[-1], "by_milestone": per_milestone, "all": plist}
    return out


def report(weeks: int = 4) -> dict[str, Any]:
    """聚合 hit rate / 中位 excess / 失败案例.

    since 过滤用 baseline_date (pub_date 优先), 这样 backfill 后真新闻发布日老
    但 cron 收集日近的 event 也会被正确归类.
    """
    perfs = _read_jsonl(_PERF_PATH)
    since = dt.date.today() - dt.timedelta(weeks=weeks)

    def _filter_date(p):
        # baseline_date 优先, fallback event_trade_date (兼容老数据)
        d = p.get("baseline_date") or p.get("event_trade_date")
        try:
            return dt.datetime.strptime(d, "%Y%m%d").date() >= since
        except (ValueError, TypeError):
            return False

    perfs = [p for p in perfs if _filter_date(p)]

    by_pair = _milestone_perfs_by_ticker(perfs)

    # group by milestone
    out: dict = {
        "since": since.isoformat(),
        "ticker_pairs": len(by_pair),
        "events_covered": len(set(p[0] for p in by_pair)),
        "by_milestone": {},
        "by_score": {},
        "by_track": {},
        "by_subdomain": {},
        "top_winners": [],
        "top_losers": [],
    }

    for ms in MILESTONE_DAYS:
        key = f"T+{ms}"
        rows = []
        for pair, agg in by_pair.items():
            if key in agg["by_milestone"]:
                rows.append(agg["by_milestone"][key])
        if not rows:
            continue
        hits = sum(1 for r in rows if r.get("hit"))
        excesses = [r["excess_pct"] for r in rows if r.get("excess_pct") is not None]
        absolutes = [r["absolute_pct"] for r in rows if r.get("absolute_pct") is not None]
        out["by_milestone"][key] = {
            "samples": len(rows),
            "hits": hits,
            "hit_rate": round(hits / len(rows) * 100, 1),
            "median_excess_pct": round(sorted(excesses)[len(excesses) // 2], 2) if excesses else None,
            "avg_absolute_pct": round(sum(absolutes) / len(absolutes), 2) if absolutes else None,
        }

    # by_score: 用 latest (最近 verify) 作为 ticker 当前状态
    for pair, agg in by_pair.items():
        latest = agg["latest"]
        score_k = f"score={latest.get('event_score')}"
        out["by_score"].setdefault(score_k, {"samples": 0, "hits": 0, "excesses": []})
        out["by_score"][score_k]["samples"] += 1
        if latest.get("hit"):
            out["by_score"][score_k]["hits"] += 1
        if latest.get("excess_pct") is not None:
            out["by_score"][score_k]["excesses"].append(latest["excess_pct"])

        track_k = latest.get("event_track", "?")
        out["by_track"].setdefault(track_k, {"samples": 0, "hits": 0, "excesses": []})
        out["by_track"][track_k]["samples"] += 1
        if latest.get("hit"):
            out["by_track"][track_k]["hits"] += 1
        if latest.get("excess_pct") is not None:
            out["by_track"][track_k]["excesses"].append(latest["excess_pct"])

        sd_k = latest.get("event_subdomain", "?")
        out["by_subdomain"].setdefault(sd_k, {"samples": 0, "hits": 0, "excesses": []})
        out["by_subdomain"][sd_k]["samples"] += 1
        if latest.get("hit"):
            out["by_subdomain"][sd_k]["hits"] += 1
        if latest.get("excess_pct") is not None:
            out["by_subdomain"][sd_k]["excesses"].append(latest["excess_pct"])

    for bucket in ["by_score", "by_track", "by_subdomain"]:
        for k, d in out[bucket].items():
            d["hit_rate"] = round(d["hits"] / d["samples"] * 100, 1) if d["samples"] else 0
            ex = d.pop("excesses")
            d["median_excess_pct"] = round(sorted(ex)[len(ex) // 2], 2) if ex else None

    # top winners / losers (latest excess)
    latest_rows = [agg["latest"] for agg in by_pair.values()
                   if agg["latest"].get("excess_pct") is not None]
    latest_rows.sort(key=lambda r: r["excess_pct"], reverse=True)
    out["top_winners"] = latest_rows[:5]
    out["top_losers"] = latest_rows[-5:][::-1]

    return out


# ─── markdown doc ──────────────────────────────────────────────────────

def doc_markdown(weeks: int = 4) -> str:
    rep = report(weeks=weeks)
    md = []
    today = dt.datetime.now(CN_TZ).strftime("%Y-%m-%d")
    md.append(f"# 叙事雷达 · 推演验证 ({today})")
    md.append("")
    md.append(f"**回看窗口**: 过去 {weeks} 周 · 自 {rep['since']} 起")
    md.append(f"**覆盖 events**: {rep['events_covered']} 条 · **ticker pair**: {rep['ticker_pairs']} 个")
    md.append("")
    md.append("> hit 判定: side=+ → excess_pct > 0; side=- → excess_pct < 0. "
              "excess_pct = ticker 涨跌% - benchmark 涨跌% (CSI300 / HSI).")
    md.append(">")
    md.append("> baseline 锚定: 新闻**原始发布日 (pub_date)** 收盘价, 不是 radar 收集日 — "
              "确保 T+N 测的是真实市场反应窗口而不是 cron 抓取延迟.")
    md.append("")

    # by_milestone 表
    md.append("## 各窗口命中率")
    md.append("")
    md.append("| 窗口 | n | hit_rate | 中位 excess% | 平均 absolute% |")
    md.append("|------|---|----------|--------------|----------------|")
    for ms in MILESTONE_DAYS:
        key = f"T+{ms}"
        if key not in rep["by_milestone"]:
            md.append(f"| {key} | 0 | — | — | — |")
            continue
        d = rep["by_milestone"][key]
        med = f"{d['median_excess_pct']:+.2f}" if d['median_excess_pct'] is not None else "—"
        avg = f"{d['avg_absolute_pct']:+.2f}" if d['avg_absolute_pct'] is not None else "—"
        md.append(f"| {key} | {d['samples']} | {d['hit_rate']}% | {med}% | {avg}% |")
    md.append("")

    # by_score
    md.append("## 按 event score 拆解 (latest)")
    md.append("")
    md.append("| score | n | hit_rate | 中位 excess% |")
    md.append("|-------|---|----------|--------------|")
    for k in sorted(rep["by_score"].keys()):
        d = rep["by_score"][k]
        med = f"{d['median_excess_pct']:+.2f}" if d['median_excess_pct'] is not None else "—"
        md.append(f"| {k} | {d['samples']} | {d['hit_rate']}% | {med}% |")
    md.append("")

    # by_track
    md.append("## 按 track 拆解")
    md.append("")
    md.append("| track | n | hit_rate | 中位 excess% |")
    md.append("|-------|---|----------|--------------|")
    for k in sorted(rep["by_track"].keys()):
        d = rep["by_track"][k]
        med = f"{d['median_excess_pct']:+.2f}" if d['median_excess_pct'] is not None else "—"
        md.append(f"| {k} | {d['samples']} | {d['hit_rate']}% | {med}% |")
    md.append("")

    # by_subdomain
    md.append("## 按 subdomain 拆解")
    md.append("")
    md.append("| subdomain | n | hit_rate | 中位 excess% |")
    md.append("|-----------|---|----------|--------------|")
    sd_items = sorted(rep["by_subdomain"].items(),
                      key=lambda kv: -kv[1]["samples"])
    for k, d in sd_items:
        med = f"{d['median_excess_pct']:+.2f}" if d['median_excess_pct'] is not None else "—"
        md.append(f"| {k} | {d['samples']} | {d['hit_rate']}% | {med}% |")
    md.append("")

    # winners / losers
    if rep["top_winners"]:
        md.append("## TOP 5 命中 (latest excess%)")
        md.append("")
        md.append("| ticker | name | side | event_score | event 标题 | days | excess% |")
        md.append("|--------|------|------|-------------|-------------|------|---------|")
        for r in rep["top_winners"]:
            md.append(f"| {r['code']} | {r['name']} | {r['side']} | {r['event_score']} "
                      f"| {_truncate(r['event_title'], 40)} | T+{r['days_since_event']} "
                      f"| {r['excess_pct']:+.2f}% |")
        md.append("")

    if rep["top_losers"]:
        md.append("## TOP 5 失败 (latest excess%)")
        md.append("")
        md.append("| ticker | name | side | event_score | event 标题 | days | excess% |")
        md.append("|--------|------|------|-------------|-------------|------|---------|")
        for r in rep["top_losers"]:
            md.append(f"| {r['code']} | {r['name']} | {r['side']} | {r['event_score']} "
                      f"| {_truncate(r['event_title'], 40)} | T+{r['days_since_event']} "
                      f"| {r['excess_pct']:+.2f}% |")
        md.append("")

    md.append("---")
    md.append("")
    md.append("**解读**:")
    md.append("- score=3 (重磅) 应该 hit_rate 显著高于 score=2 (叙事级), 否则雷达打分校准有问题")
    md.append("- 单只 ticker 在多个 window 都失败 → 推演方向可能错, 进 thesis 反思")
    md.append("- 整体 hit_rate < 50% (excess) → 雷达无 alpha, 跟大盘 / 板块 beta 同步, 需要重做筛选")
    md.append("")
    md.append(f"*narrative_track / cron: 每日 18:45 工作日 · 报告由 `narrative_track.py doc --weeks {weeks}` 生成*")
    return "\n".join(md)


def event_report(event_ts: str) -> str:
    """单 event 的所有 ticker × milestone 详情."""
    perfs = [p for p in _read_jsonl(_PERF_PATH) if p.get("event_ts") == event_ts]
    if not perfs:
        return f"event {event_ts} 无 perf 记录."

    e_meta = perfs[0]
    by_code: dict[str, list[dict]] = defaultdict(list)
    for p in perfs:
        by_code[p["code"]].append(p)
    for v in by_code.values():
        v.sort(key=lambda x: x["days_since_event"])

    md = []
    md.append(f"# Event {e_meta['event_trade_date']} · score={e_meta['event_score']}")
    md.append(f"**{e_meta['event_track']} / {e_meta['event_subdomain']}**: {e_meta['event_title']}")
    md.append("")
    md.append("| code | name | side | T+0 | latest | days | abs% | bench% | excess% | hit |")
    md.append("|------|------|------|-----|--------|------|------|--------|---------|-----|")
    for code, plist in by_code.items():
        latest = plist[-1]
        hit_str = "✅" if latest.get("hit") else "❌"
        bench = f"{latest['benchmark_pct']:+.2f}" if latest.get("benchmark_pct") is not None else "—"
        excess = f"{latest['excess_pct']:+.2f}" if latest.get("excess_pct") is not None else "—"
        md.append(f"| {code} | {latest['name']} | {latest['side']} "
                  f"| {latest['baseline_price']} | {latest['current_price']} "
                  f"| T+{latest['days_since_event']} "
                  f"| {latest['absolute_pct']:+.2f}% | {bench}% | {excess}% | {hit_str} |")
    return "\n".join(md)


# ─── CLI ────────────────────────────────────────────────────────────────

def _cmd_verify(args):
    if args.event_ts:
        events = [e for e in _read_jsonl(_EVENTS_PATH) if e.get("ts") == args.event_ts]
        if not events:
            print(f"event_ts {args.event_ts} not found")
            sys.exit(1)
        seen = _existing_perf_keys()
        cached: dict = {}
        for e in events:
            for t in e.get("tickers", []):
                perf = verify_event_ticker(e, t, verify_date=args.date,
                                            cached_baseline=cached, seen_keys=seen)
                print(json.dumps(perf, ensure_ascii=False) if perf else
                      f"skip {e.get('subdomain')} / {t.get('code')}")
        return
    stats = verify_all(verify_date=args.date)
    print(f"verify_all: events_total={stats['events_total']} "
          f"open={stats['events_open']} expired={stats['events_expired']} "
          f"ticker_pairs={stats['ticker_pairs_total']} verified={stats['verified']} "
          f"skipped={stats['skipped_dup']} fetch_failed={stats['fetch_failed']}")


def _cmd_report(args):
    rep = report(weeks=args.weeks)
    print(json.dumps(rep, ensure_ascii=False, indent=2))


def _cmd_doc(args):
    print(doc_markdown(weeks=args.weeks))


def _cmd_event_report(args):
    print(event_report(args.event_ts))


def main():
    ap = argparse.ArgumentParser(description="Narrative event T+N tracking.")
    sp = ap.add_subparsers(dest="cmd", required=True)

    v = sp.add_parser("verify")
    v.add_argument("--event-ts", help="单 event 验证 (默认全部)")
    v.add_argument("--date", help="verify against YYYYMMDD close (默认最新)")
    v.add_argument("--all", action="store_true", help="(default behavior)")
    v.set_defaults(func=_cmd_verify)

    r = sp.add_parser("report")
    r.add_argument("--weeks", type=int, default=4)
    r.set_defaults(func=_cmd_report)

    d = sp.add_parser("doc")
    d.add_argument("--weeks", type=int, default=4)
    d.set_defaults(func=_cmd_doc)

    er = sp.add_parser("event_report")
    er.add_argument("--event-ts", required=True)
    er.set_defaults(func=_cmd_event_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
