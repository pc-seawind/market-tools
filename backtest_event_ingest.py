"""backtest_event_ingest.py — 历史 narrative event 多里程碑一次性录入工具。

与实时 radar 的区别:
  实时 event: narrative_track.verify 每天为每个 (open event, ticker) append 一行,
              里程碑随时间慢慢长出来 (今天 T+3, 明天 T+4...)。
  历史 event: 所有里程碑 (5/10/14/21/28/40 自然日) 都已发生, 一次性把 6 行 perf
              全算出来写入。

为保证历史回测数据与实时数据 *完全可比*, 本脚本复用 narrative_track 的:
  - 数据源:  tushare (_ts_csv → tushare.py, 带 akshare fallback + 永久缓存)
  - benchmark: CSI300 (000300.SH) / HK=HSI   —— narrative_track._fetch_benchmark_close
  - sector:    申万/THS L1 行业等权平均        —— narrative_sector_bench.compute_sector_perf
  - hit 判定:  与 verify_event_ticker 逐字一致 (side=+ → excess>0; hit_strict = hit AND hit_vs_sector)
  - 里程碑:    narrative_track.MILESTONE_DAYS = [5,10,14,21,28,40] 自然日, baseline=pub_date

唯一新增的逻辑: "close on-or-before 某自然日" 的 snapping — 历史里程碑日可能落在
周末/节假日, 需要 fallback 到 ≤该日的最近交易日 close (实时 verify 用 today 不会遇到)。

录入的 event 的 features 字段留空 (None), 交给 narrative_label_event.py 人工打 8 维。
ticker_layer 缺失会 warning (复用 narrative_radar 的检查思路)。

幂等: 同 (event_ts, code, days_since_event) 已存在则跳过 (复用 _existing_perf_keys)。

CLI:
  # 单条录入 (命令行)
  backtest_event_ingest.py add \\
      --pub-date 20250812 \\
      --title "网传寒武纪在某厂商预定大量载板订单 (后被公司辟谣)" \\
      --track AI --subdomain ai__compute_chip --score 3 \\
      --ticker 688256.SH:寒武纪:+ \\
      --rationale "..." --source-url "https://..." \\
      [--narrative-id cambricon_substrate_rumor_202508] [--dry-run]

  # 批量录入 (从 JSON 文件, 每行一个 event spec)
  backtest_event_ingest.py batch path/to/events_spec.jsonl [--dry-run]

  # 重算某 event 的全部里程碑 perf (删旧 + 重写)
  backtest_event_ingest.py recompute --event-ts <ts> [--dry-run]
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

# 复用 narrative_track 的全部底层算法 —— 保证历史/实时数据同源同算法
import narrative_track as nt  # noqa: E402

try:
    import narrative_sector_bench as nsb  # noqa: E402
except Exception:
    nsb = None

# ticker_layer 检查 (复用 narrative_radar 的 helper, 软依赖)
try:
    import narrative_radar as nr  # noqa: E402
except Exception:
    nr = None

_EVENTS_PATH = nt._EVENTS_PATH
_PERF_PATH = nt._PERF_PATH
CN_TZ = nt.CN_TZ
MILESTONE_DAYS = nt.MILESTONE_DAYS  # [5, 10, 14, 21, 28, 40]


# ─── base price resolution — 委托给 narrative_track 的权威实现 ───────────────
# 这些函数现已下沉到 narrative_track (两条管道共享口径), 此处保留薄别名以兼容
# 本模块内既有调用点。
_close_on_or_before = nt._price_on_or_before
_first_trade_on_or_after = nt._price_on_or_after
_index_first_on_or_after = nt._index_price_on_or_after
_index_close_on_or_before = nt._index_close_on_or_before
_resolve_base = nt.resolve_base
_resolve_bench_base = nt.resolve_bench_base


# ─── single milestone perf row ────────────────────────────────────────────

def _compute_milestone_perf(event: dict, ticker: dict, days: int,
                            baseline_cache: dict) -> Optional[dict]:
    """算 (event, ticker) 在 baseline + `days` 自然日的一行 perf。

    与 narrative_track.verify_event_ticker 的字段/算法逐字对齐, 区别仅在:
      - verify_date 不是 today, 而是 baseline_date + days (snap 到最近交易日)
      - baseline 和 current 都用 _close_on_or_before (历史日必须 snap)
    """
    event_ts = event.get("ts", "")
    event_trade_date = event.get("trade_date", "")
    pub_date = nt._baseline_date_of(event)
    event_pub_date = event.get("pub_date", "")
    code = ticker.get("code", "")
    # session: 消息到达时点, 决定 base 取价口径。默认 post(盘后→次日open)
    session = (event.get("session") or "post").lower()
    if not event_ts or not pub_date or not code:
        return None

    market = nt._market_of(code)
    side = ticker.get("side", "+")

    # ── base price (session-aware): 返回真实落地交易日 actual_base_date ──
    bkey = ("stock", code, pub_date, session)
    if bkey in baseline_cache:
        base_resolved = baseline_cache[bkey]
    else:
        base_resolved = _resolve_base(code, pub_date, session)
        baseline_cache[bkey] = base_resolved
    if base_resolved is None:
        return None
    baseline_date, baseline = base_resolved  # actual_base_date, base_price

    # milestone 窗口从 actual_base_date 起算 (不是 pub_date), 保证 D14 是
    # "买入后 14 自然日" 而非 "消息后 14 自然日"
    try:
        baseline_d = dt.datetime.strptime(baseline_date, "%Y%m%d").date()
    except ValueError:
        return None
    target_d = baseline_d + dt.timedelta(days=days)
    if target_d > dt.date.today():
        return None  # 里程碑还没到 (event 太新)
    target_date = target_d.strftime("%Y%m%d")

    # current close (snap to ≤target_date)
    rc = _close_on_or_before(code, target_date)
    if rc is None:
        return None
    actual_verify_date, current = rc

    # benchmark
    if market == "HK":
        bench, bapi = nt.BENCHMARK_HK, "index_global"
    else:
        bench, bapi = nt.BENCHMARK_A, "index_daily"
    bbk = ("bench", bench, baseline_date, session)
    if bbk in baseline_cache:
        bench_baseline = baseline_cache[bbk]
    else:
        bench_baseline = _resolve_bench_base(bench, bapi, baseline_date, session)
        baseline_cache[bbk] = bench_baseline
    bench_current = _index_close_on_or_before(bench, bapi, target_date)

    abs_pct = (current / baseline - 1) * 100 if baseline else 0.0
    bench_pct = ((bench_current / bench_baseline - 1) * 100) \
        if (bench_baseline and bench_current) else None
    excess_pct = (abs_pct - bench_pct) if bench_pct is not None else None

    # sector (申万/THS L1 等权) — 仅 A 股
    sector_name = sector_pct = excess_vs_sector = sector_n_members = None
    if market == "A" and nsb is not None:
        try:
            sp = nsb.compute_sector_perf(code, baseline_date, actual_verify_date)
            if sp:
                sector_name = sp.get("sector_name")
                sector_pct = sp.get("sector_pct")
                sector_n_members = sp.get("n_members")
                if sector_pct is not None:
                    excess_vs_sector = abs_pct - sector_pct
        except Exception:
            pass

    # hit 判定 (逐字对齐 verify_event_ticker)
    if excess_pct is not None:
        hit = (side == "+" and excess_pct > 0) or (side == "-" and excess_pct < 0)
    else:
        hit = (side == "+" and abs_pct > 0) or (side == "-" and abs_pct < 0)

    hit_vs_sector = None
    if excess_vs_sector is not None:
        hit_vs_sector = (side == "+" and excess_vs_sector > 0) or \
                        (side == "-" and excess_vs_sector < 0)

    if hit_vs_sector is not None and excess_pct is not None:
        hit_strict = bool(hit) and bool(hit_vs_sector)
    else:
        hit_strict = None

    return {
        "event_ts":           event_ts,
        "event_trade_date":   event_trade_date,
        "event_pub_date":     event_pub_date,
        "baseline_date":      baseline_date,
        "event_score":        event.get("score"),
        "event_track":        event.get("track"),
        "event_subdomain":    event.get("subdomain"),
        "event_title":        nt._truncate(event.get("title", ""), 80),
        "code":               code,
        "name":               ticker.get("name", ""),
        "side":               side,
        "verify_ts":          dt.datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "verify_date":        actual_verify_date,
        "days_since_event":   days,
        "baseline_price":     round(baseline, 4),
        "current_price":      round(current, 4),
        "absolute_pct":       round(abs_pct, 2),
        "benchmark":          bench,
        "benchmark_baseline": round(bench_baseline, 4) if bench_baseline else None,
        "benchmark_current":  round(bench_current, 4) if bench_current else None,
        "benchmark_pct":      round(bench_pct, 2) if bench_pct is not None else None,
        "excess_pct":         round(excess_pct, 2) if excess_pct is not None else None,
        "hit":                hit,
        "sector_name":        sector_name,
        "sector_pct":         round(sector_pct, 2) if sector_pct is not None else None,
        "sector_n_members":   sector_n_members,
        "excess_vs_sector":   round(excess_vs_sector, 2) if excess_vs_sector is not None else None,
        "hit_vs_sector":      hit_vs_sector,
        "hit_strict":         hit_strict,
        "_backtest":          True,   # 标记历史回测行, 便于后续筛选
    }


# ─── event ingestion ──────────────────────────────────────────────────────

def _make_event_ts(pub_date: str, narrative_id: Optional[str]) -> str:
    """历史 event 用 pub_date 09:30 (开盘) 做 ts 锚, narrative_id 拼进去防撞。

    实时 event 用真实抓取时刻 ISO ts。历史 event 没有"抓取时刻", 用 pub_date
    的开盘时间 + narrative_id 后缀, 保证同一天多条 event 不撞 key。
    """
    base = dt.datetime.strptime(pub_date, "%Y%m%d").replace(
        hour=9, minute=30, tzinfo=CN_TZ)
    iso = base.isoformat(timespec="seconds")
    if narrative_id:
        return f"{iso}#{narrative_id}"
    return iso


def build_event(pub_date: str, title: str, track: str, subdomain: str,
                score: int, tickers: list[dict], rationale: str = "",
                source_url: str = "", thesis_seed: str = "",
                narrative_id: Optional[str] = None) -> dict:
    """组装一条历史 event (schema 对齐 narrative_events.jsonl)。features 留 None。"""
    event_ts = _make_event_ts(pub_date, narrative_id)
    return {
        "ts":             event_ts,
        "trade_date":     pub_date,       # 历史 event: trade_date = pub_date (无"收集日"概念)
        "pub_date":       pub_date,       # baseline 锚
        "track":          track,
        "subdomain":      subdomain,
        "score":          score,
        "title":          title,
        "url":            source_url,
        "source":         "backtest/manual",
        "rationale":      rationale,
        "thesis_seed":    thesis_seed,
        "tickers":        tickers,
        "event_type":     "other",
        "late_stage":     False,
        "score_penalty":  0,
        "effective_score": score,
        "features":       None,           # 待 narrative_label_event.py 人工打 8 维
        "narrative_id":   narrative_id,
        "_backtest":      True,
    }


def ingest_event(event: dict, dry_run: bool = False) -> dict:
    """录入一条 event: 写 events.jsonl + 算全部里程碑 perf 写 perf.jsonl。

    返回 stats dict。
    """
    event_ts = event.get("ts", "")
    stats = {"event_ts": event_ts, "perf_rows": 0, "skipped_dup": 0,
             "milestones_pending": 0, "fetch_failed": 0}

    # 幂等: event 已存在?
    existing_events = {e.get("ts") for e in nt._read_jsonl(_EVENTS_PATH)}
    event_exists = event_ts in existing_events

    seen = nt._existing_perf_keys()
    baseline_cache: dict = {}
    perf_rows: list[dict] = []

    for ticker in event.get("tickers", []):
        code = ticker.get("code", "")
        for days in MILESTONE_DAYS:
            key = (event_ts, code, days)
            if key in seen:
                stats["skipped_dup"] += 1
                continue
            perf = _compute_milestone_perf(event, ticker, days, baseline_cache)
            if perf is None:
                # 区分: 里程碑未到 (event 太新) vs 拉数据失败
                baseline_d = dt.datetime.strptime(
                    nt._baseline_date_of(event), "%Y%m%d").date()
                if baseline_d + dt.timedelta(days=days) > dt.date.today():
                    stats["milestones_pending"] += 1
                else:
                    stats["fetch_failed"] += 1
                continue
            perf_rows.append(perf)
            seen.add(key)
            stats["perf_rows"] += 1

    if dry_run:
        stats["_dry_run"] = True
        stats["_event_would_write"] = not event_exists
        stats["_perf_preview"] = perf_rows
        return stats

    if not event_exists:
        nt._append_jsonl(_EVENTS_PATH, event)
        stats["event_written"] = True
    else:
        stats["event_written"] = False

    for perf in perf_rows:
        nt._append_jsonl(_PERF_PATH, perf)

    # ticker_layer 检查
    if nr is not None and hasattr(nr, "check_ticker_layer_coverage"):
        try:
            missing = nr.check_ticker_layer_coverage(event.get("subdomain", ""),
                                                     event.get("tickers", []))
            if missing:
                stats["ticker_layer_missing"] = [t.get("code") for t in missing]
        except Exception:
            pass

    return stats


# ─── ticker spec parsing ──────────────────────────────────────────────────

def _parse_ticker(spec: str) -> dict:
    """'688256.SH:寒武纪:+' → {code, name, side}。side 默认 '+'。"""
    parts = spec.split(":")
    code = parts[0].strip()
    name = parts[1].strip() if len(parts) > 1 else ""
    side = parts[2].strip() if len(parts) > 2 else "+"
    return {"code": code, "name": name, "side": side}


# ─── CLI ──────────────────────────────────────────────────────────────────

def _print_stats(stats: dict, title: str = "") -> None:
    if title:
        print(f"\n=== {title} ===")
    if stats.get("_dry_run"):
        print(f"[DRY-RUN] event_ts={stats['event_ts']}")
        print(f"  event would write: {stats['_event_would_write']}")
        print(f"  perf rows computed: {stats['perf_rows']}  "
              f"(dup skipped: {stats['skipped_dup']}, "
              f"pending: {stats['milestones_pending']}, "
              f"fetch_failed: {stats['fetch_failed']})")
        for p in stats.get("_perf_preview", []):
            print(f"    T+{p['days_since_event']:>2}d  {p['code']:<12} {p['name']:<8} "
                  f"abs={p['absolute_pct']:+6.2f}%  "
                  f"exMkt={p['excess_pct'] if p['excess_pct'] is not None else 'NA':>7}  "
                  f"sec={p['sector_name'] or 'NA'} {p['sector_pct'] if p['sector_pct'] is not None else 'NA'}  "
                  f"exSec={p['excess_vs_sector'] if p['excess_vs_sector'] is not None else 'NA':>7}  "
                  f"hit={p['hit']} strict={p['hit_strict']}")
    else:
        print(f"event_ts={stats['event_ts']}")
        print(f"  event_written: {stats.get('event_written')}")
        print(f"  perf rows: {stats['perf_rows']}  "
              f"(dup: {stats['skipped_dup']}, pending: {stats['milestones_pending']}, "
              f"failed: {stats['fetch_failed']})")
        if stats.get("ticker_layer_missing"):
            print(f"  ⚠️  ticker_layer 缺失: {stats['ticker_layer_missing']}")
            print(f"      → 编辑 ticker_layer.yaml 补 layer 标签")


def cmd_add(args) -> int:
    tickers = [_parse_ticker(s) for s in args.ticker]
    event = build_event(
        pub_date=args.pub_date, title=args.title, track=args.track,
        subdomain=args.subdomain, score=args.score, tickers=tickers,
        rationale=args.rationale or "", source_url=args.source_url or "",
        thesis_seed=args.thesis_seed or "", narrative_id=args.narrative_id,
    )
    stats = ingest_event(event, dry_run=args.dry_run)
    _print_stats(stats, title=f"ADD {args.title[:40]}")
    if not args.dry_run and not stats.get("_dry_run"):
        print(f"\n下一步: 人工打 8 维特征 →")
        print(f"  python3 narrative_label_event.py --event-ts '{event['ts']}'")
    return 0


def cmd_batch(args) -> int:
    specs = nt._read_jsonl(Path(args.spec_file))
    print(f"读入 {len(specs)} 条 event spec from {args.spec_file}")
    total_perf = 0
    for spec in specs:
        tickers = spec.get("tickers", [])
        # 允许 spec 里 tickers 是 'code:name:side' 字符串列表 或 dict 列表
        norm_tickers = []
        for t in tickers:
            if isinstance(t, str):
                norm_tickers.append(_parse_ticker(t))
            else:
                norm_tickers.append(t)
        event = build_event(
            pub_date=spec["pub_date"], title=spec["title"],
            track=spec.get("track", ""), subdomain=spec.get("subdomain", ""),
            score=int(spec.get("score", 0)), tickers=norm_tickers,
            rationale=spec.get("rationale", ""),
            source_url=spec.get("source_url", spec.get("url", "")),
            thesis_seed=spec.get("thesis_seed", ""),
            narrative_id=spec.get("narrative_id"),
        )
        stats = ingest_event(event, dry_run=args.dry_run)
        _print_stats(stats, title=spec["title"][:40])
        total_perf += stats["perf_rows"]
    print(f"\n=== 批量完成: {len(specs)} events, {total_perf} perf rows ===")
    if not args.dry_run:
        print("下一步: 逐条人工打标 (narrative_label_event.py) 或批量 _human_labels.py")
    return 0


def cmd_recompute(args) -> int:
    """删旧 perf + 重算某 event 的全部里程碑 (修 bug 后重跑用)。"""
    all_perf = nt._read_jsonl(_PERF_PATH)
    kept = [p for p in all_perf if p.get("event_ts") != args.event_ts]
    removed = len(all_perf) - len(kept)
    events = {e.get("ts"): e for e in nt._read_jsonl(_EVENTS_PATH)}
    event = events.get(args.event_ts)
    if not event:
        print(f"ERROR: event_ts {args.event_ts} 不在 events.jsonl")
        return 1
    print(f"将删除 {removed} 行旧 perf, 重算 event: {event.get('title', '')[:50]}")
    if args.dry_run:
        print("[DRY-RUN] 不写文件")
        baseline_cache: dict = {}
        for ticker in event.get("tickers", []):
            for days in MILESTONE_DAYS:
                perf = _compute_milestone_perf(event, ticker, days, baseline_cache)
                if perf:
                    print(f"  T+{days}d {ticker['code']} abs={perf['absolute_pct']:+.2f}% "
                          f"hit={perf['hit']} strict={perf['hit_strict']}")
        return 0
    # 重写 perf 文件 (kept + 新算)
    with _PERF_PATH.open("w", encoding="utf-8") as f:
        for p in kept:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    baseline_cache = {}
    n = 0
    for ticker in event.get("tickers", []):
        for days in MILESTONE_DAYS:
            perf = _compute_milestone_perf(event, ticker, days, baseline_cache)
            if perf:
                nt._append_jsonl(_PERF_PATH, perf)
                n += 1
    print(f"重算完成: 写入 {n} 行 perf")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="历史 narrative event 录入工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("add", help="录入单条历史 event")
    pa.add_argument("--pub-date", required=True, help="YYYYMMDD 新闻发布日 (baseline 锚)")
    pa.add_argument("--title", required=True)
    pa.add_argument("--track", required=True, help="e.g. AI / 家居 / AI×家居")
    pa.add_argument("--subdomain", required=True, help="e.g. ai__compute_chip")
    pa.add_argument("--score", type=int, required=True, help="0-3")
    pa.add_argument("--ticker", action="append", required=True,
                    help="code:name:side, 可多次. e.g. 688256.SH:寒武纪:+")
    pa.add_argument("--rationale", default="")
    pa.add_argument("--thesis-seed", default="")
    pa.add_argument("--source-url", default="")
    pa.add_argument("--narrative-id", default=None)
    pa.add_argument("--dry-run", action="store_true")
    pa.set_defaults(func=cmd_add)

    pb = sub.add_parser("batch", help="从 jsonl spec 批量录入")
    pb.add_argument("spec_file")
    pb.add_argument("--dry-run", action="store_true")
    pb.set_defaults(func=cmd_batch)

    pr = sub.add_parser("recompute", help="重算某 event 全部里程碑 perf")
    pr.add_argument("--event-ts", required=True)
    pr.add_argument("--dry-run", action="store_true")
    pr.set_defaults(func=cmd_recompute)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
