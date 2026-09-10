#!/usr/bin/env python3
"""backtest_component_audit.py — 非推荐组件的前瞻收益审计.

动机 (2026-09-10 用户指令):
  "其他不产生推荐的那些策略最后都用来干什么了, 价值在哪里, 应该如何使用,
   还是其实用不上应该去掉 —— 把这些结果做下回测, 有用的接入, 没用的取消"

被审计对象 (全部是"看起来像推荐、但不产推荐"的组件):

  A. signals.py 的买卖信号
     - BUY_EARLY      (量比 >= 2.0 且 1W ∈ [-3, +5])
     - BUY_BREAKOUT   (pos >= 85 且 量比 >= 1.5 且 1W ∈ (0, 10])
     - SELL_EXHAUSTION / SELL_CONFIRMED / SELL_EXTREME (pos > 85 且 1W > 15/25/35)
     - SELL_TOP       (3M > 100 且 1W < -10 且 量比 > 1.5)
     两个口径都测:
       [proxy] 生产口径 — daily.sh 实际传入的 pos = position_proxy(r1m)
       [real]  语义口径 — 真实 pos120 百分位 (规则字面意思)
     这两个口径差别很大 (proxy: pos>85 ⟺ r1m > +28%), 必须分开看.

  B. grading.py 的综合分级 A/B/C/D
     被 funnel.sh / momentum.sh / screen.sh 共享, 但 16 个 investment cron
     没有一个调用它们 → 分级从未进入任何用户可见报告.
     重建口径 (尽量忠实于 sector_health.build_index + grading.compute_grade):
       adjusted = heat_score(concept 排名 / industry 1M 均涨 取 max)
                  - sell_penalty + buy_bonus + relative_strength_adj
       style: balanced (funnel) / momentum
     然后按 grade 分桶看前瞻超额收益 —— 若 D 跑赢 A, 分级是反的.

  C. grading.py 的单因子
     - heat_score 1/2/3/4 (板块热度; "追热板块" 假设)
     - relative_strength bins (+2/+1/0/-1/-2; "跑赢板块=真龙头" 假设)

方法:
  复用 backtest_leftside_reversal.py 的取数/前复权/特征口径 (同一引擎,
  保证与左向回测可比). 评估日 2025-01-02 ~ 2026-07-16 每 5 个交易日采样.
  全样本 214 只 A 股 (concept 成分股并集).

  超额收益 = 同日横截面去均值 (与左向回测同口径, 数字可直接对比).
  板块热度 = 该日 concept 内成员 pct_1m 均值排名 (top3→4 / top7→3 /
             中段→2 / 末4→1); industry 通道用全市场 daily 快照重建
             (申万 L1 1M 均涨: >15%→4 / >0→3 / >-5→2 / else 1), 取 max.

已知局限 (报告里必须写明):
  - 股票池是**当前** concept 成分股 → 幸存者/选择偏差.
  - concept 成员在该日缺失行情时被剔除, 与生产口径 (全市场成员) 有细微差别.
  - 未叠加估值乖离 + 未叠加 fundamentals_adj (Phase 3 未接入执行路径).
  - sell_penalty / buy_bonus 在 grading 里是"点数", 本脚本沿用同一映射.
  - 超额收益基准 = 本股票池等权, 不是全市场; 全市场基准会略微不同.

输出:
  /tmp/component_audit_<YYYYmmdd-HHMM>.json   完整分桶统计
  data/backtest_samples/audit_samples_step5.jsonl   逐样本缓存 (供后续复用)
  终端: 人类可读 summary

Usage:
  python3 backtest_component_audit.py [--out /tmp/xxx.json] [--step 5]
                                      [--eval-start 20250101] [--end 20260831]
                                      [--limit N] [--no-cache]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from backtest_leftside_reversal import (  # noqa: E402
    _features, _ts, fetch_stock, load_universe,
)

FWDS = [5, 20, 40]


# ─── 特征扩展: 补 r3m (signals.SELL_TOP 需要) ─────────────────────────────

def features2(closes: list[float], vols: list[float]) -> dict | None:
    f = _features(closes, vols)
    if not f:
        return None
    n = len(closes)
    c = closes[-1]
    f["pct_3m"] = (c / closes[-64] - 1) * 100 if n >= 64 and closes[-64] > 0 else 0.0
    return f


# ─── signals.py 的两个口径 ────────────────────────────────────────────────

def position_proxy(r1m: float | None) -> float | None:
    """signals.position_proxy 的副本 (保持与生产完全一致)."""
    if r1m is None:
        return None
    if r1m >= 40:
        return 90
    if r1m >= 20:
        return 75 + (r1m - 20) * 0.75
    if r1m >= 0:
        return 50 + r1m * 1.25
    if r1m >= -20:
        return 50 + r1m * 1.5
    return 20


def signal_tags(f: dict) -> list[str]:
    """返回命中的信号标签, 同时带 [proxy] / [real] 标记."""
    tags: list[str] = []
    r1w = f["pct_5d"]
    r1m = f["pct_1m"]
    r3m = f["pct_3m"]
    vr1 = f["vol_1d"]
    pos_proxy = position_proxy(r1m)
    pos_real = f["pos120"]

    for tag, pos in (("proxy", pos_proxy), ("real", pos_real)):
        # BUY
        if vr1 >= 2.0 and -3 <= r1w <= 5:
            tags.append(f"SIG_BUY_EARLY@{tag}")
        if pos is not None and pos >= 85 and vr1 >= 1.5 and 0 < r1w <= 10:
            tags.append(f"SIG_BUY_BREAKOUT@{tag}")
        # SELL
        if pos is not None and pos > 85:
            if r1w > 35:
                tags.append(f"SIG_SELL_EXTREME@{tag}")
            elif r1w > 25:
                tags.append(f"SIG_SELL_CONFIRMED@{tag}")
            elif r1w > 15:
                tags.append(f"SIG_SELL_EXHAUSTION@{tag}")
        if r3m > 100 and r1w < -10 and vr1 > 1.5:
            tags.append(f"SIG_SELL_TOP@{tag}")

    # 合并口径 (任一口径命中) —— grading 用的是 proxy 口径, 这里只做对照
    tags.append("SIG_ANY_BUY@" + ("hit" if any(t.startswith("SIG_BUY") for t in tags) else "miss"))
    return tags


def sell_buy_sets_proxy(f: dict) -> tuple[set[str], set[str]]:
    """复刻 grading.detect_sell_buy_signals (生产口径 = proxy pos)."""
    r1w, r1m, r3m = f["pct_5d"], f["pct_1m"], f["pct_3m"]
    vr1, pos = f["vol_1d"], position_proxy(f["pct_1m"])
    sell, buy = set(), set()
    if vr1 >= 2.0 and -3 <= r1w <= 5:
        buy.add("BUY_EARLY")
    if pos is not None and pos >= 85 and vr1 >= 1.5 and 0 < r1w <= 10:
        buy.add("BUY_BREAKOUT")
    if pos is not None and pos > 85:
        if r1w > 35:
            sell.add("SELL_EXTREME")
        elif r1w > 25:
            sell.add("SELL_CONFIRMED")
        elif r1w > 15:
            sell.add("SELL_EXHAUSTION")
    if r3m > 100 and r1w < -10 and vr1 > 1.5:
        sell.add("SELL_TOP")
    return sell, buy


def sell_penalty(sell: set[str]) -> int:
    if "SELL_EXTREME" in sell:
        return 3
    if "SELL_CONFIRMED" in sell or "SELL_TOP" in sell:
        return 2
    if "SELL_EXHAUSTION" in sell:
        return 1
    return 0


def rel_adj(delta: float | None) -> int:
    """grading.relative_strength_adj 的副本."""
    if delta is None:
        return 0
    if delta > 20:
        return 2
    if delta > 10:
        return 1
    if delta > -5:
        return 0
    if delta > -15:
        return -1
    return -2


# ─── 全市场快照 (industry 通道 + 参照) ────────────────────────────────────

def market_close(date: str) -> dict[str, float]:
    rows = _ts("daily", trade_date=date, fields="ts_code,close")
    out = {}
    for r in rows:
        try:
            out[r["ts_code"]] = float(r["close"])
        except (ValueError, KeyError, TypeError):
            continue
    return out


def industry_map() -> dict[str, str]:
    rows = _ts("stock_basic", list_status="L", fields="ts_code,name,industry")
    return {r["ts_code"]: (r.get("industry") or "") for r in rows if r.get("ts_code")}


# ─── 主流程 ──────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--start", default="20240601")
    ap.add_argument("--eval-start", default="20250101")
    ap.add_argument("--end", default="20260831")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--samples-cache", default=str(
        _ROOT / "data" / "backtest_samples" / "audit_samples_step5.jsonl"))
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    mapping = load_universe(args.limit or None)
    code2concepts: dict[str, list[str]] = defaultdict(list)
    for concept, codes in mapping.items():
        for c in codes:
            code2concepts[c].append(concept)
    universe = sorted(code2concepts)

    # 交易日历
    cal_rows = _ts("daily", ts_code="000001.SZ", start_date=args.start,
                   fields="trade_date")
    calendar = sorted(r["trade_date"] for r in cal_rows) if cal_rows else []
    if not calendar:
        print("ERROR: 无法获取交易日历", file=sys.stderr)
        return 2
    idx = {d: i for i, d in enumerate(calendar)}
    eval_dates = [d for d in calendar if args.eval_start <= d <= args.end][::args.step]
    eval_dates = [d for d in eval_dates if idx[d] + max(FWDS) < len(calendar)]
    print(f"[cal] {len(calendar)} days, {len(eval_dates)} eval dates, "
          f"{len(universe)} stocks", flush=True)

    cache_path = Path(args.samples_cache)
    samples: list[dict] = []
    if cache_path.exists() and not args.no_cache:
        for line in cache_path.read_text().splitlines():
            if line.strip():
                samples.append(json.loads(line))
        print(f"[cache] loaded {len(samples)} samples from {cache_path}", flush=True)
    else:
        for i, code in enumerate(universe, 1):
            got = fetch_stock(code, args.start)
            if not got:
                continue
            dates, closes, vols = got
            didx = {d: k for k, d in enumerate(dates)}
            for d in eval_dates:
                k = didx.get(d)
                if k is None or k < 130:
                    continue
                f = features2(closes[:k + 1], vols[:k + 1])
                if not f:
                    continue
                fwd = {}
                for h in FWDS:
                    gi = idx[d] + h
                    j = didx.get(calendar[gi]) if gi < len(calendar) else None
                    fwd[str(h)] = None if j is None else (closes[j] / closes[k] - 1) * 100
                samples.append({"code": code, "date": d, "f": f, "fwd": fwd,
                                "concepts": code2concepts[code]})
            if i % 25 == 0:
                print(f"  [{i}/{len(universe)}] {len(samples)} samples "
                      f"({time.time()-t0:.0f}s)", flush=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("\n".join(json.dumps(s, ensure_ascii=False)
                                        for s in samples))
    print(f"[samples] {len(samples)}  ({time.time()-t0:.0f}s)", flush=True)

    by_date: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        by_date[s["date"]].append(s)

    # ── 超额收益 (同日横截面去均值) ──
    for d, rows in by_date.items():
        for h in FWDS:
            vals = [r["fwd"][str(h)] for r in rows if r["fwd"][str(h)] is not None]
            m = statistics.fmean(vals) if len(vals) >= 10 else None
            for r in rows:
                v = r["fwd"][str(h)]
                r.setdefault("ex", {})[str(h)] = None if (v is None or m is None) else v - m

    # ── 市场快照: 只为 industry 通道 + 参照 ──
    snap_dates = set()
    for d in eval_dates:
        i = idx[d]
        if i - 20 >= 0:
            snap_dates.add(calendar[i - 20])
        snap_dates.add(d)
    imap = industry_map()
    print(f"[snapshots] fetching {len(snap_dates)} market dates for industry "
          f"channel...", flush=True)
    snaps: dict[str, dict[str, float]] = {}
    for n, d in enumerate(sorted(snap_dates), 1):
        snaps[d] = market_close(d)
        if n % 20 == 0:
            print(f"  [{n}/{len(snap_dates)}] ({time.time()-t0:.0f}s)", flush=True)

    ind_r1m: dict[str, dict[str, float]] = {}
    for d in eval_dates:
        i = idx[d]
        if i - 20 < 0:
            continue
        cur, old = snaps.get(d) or {}, snaps.get(calendar[i - 20]) or {}
        acc: dict[str, list[float]] = defaultdict(list)
        for code, c in cur.items():
            o = old.get(code)
            ind = imap.get(code)
            if not o or not ind or o <= 0:
                continue
            acc[ind].append((c / o - 1) * 100)
        ind_r1m[d] = {k: statistics.fmean(v) for k, v in acc.items() if v}

    # ── concept 统计 (热度排序 + 相对强度基准) ──
    concept_mom: dict[tuple[str, str], float] = {}
    concept_rank: dict[tuple[str, str], int] = {}
    concept_total: dict[str, int] = {}
    for d, rows in by_date.items():
        acc: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            for c in r["concepts"]:
                acc[c].append(r["f"]["pct_1m"])
        ranked = sorted(((c, statistics.fmean(v)) for c, v in acc.items() if v),
                        key=lambda kv: kv[1], reverse=True)
        concept_total[d] = len(ranked)
        for rank, (c, m) in enumerate(ranked):
            concept_mom[(d, c)] = m
            concept_rank[(d, c)] = rank

    def industry_score(code: str, d: str) -> tuple[int | None, float | None]:
        """sector_health.industry_score 副本."""
        ind = imap.get(code)
        if not ind:
            return None, None
        r1m = (ind_r1m.get(d) or {}).get(ind)
        if r1m is None:
            return None, None
        if r1m > 15:
            return 4, r1m
        if r1m > 0:
            return 3, r1m
        if r1m > -5:
            return 2, r1m
        return 1, r1m

    def heat_block(s: dict) -> tuple[int, float | None]:
        """sector_health.build_index 的 max(concept, industry) 语义."""
        d, code = s["date"], s["code"]
        total = concept_total.get(d, 0)
        best = None
        for c in s["concepts"]:
            rk = concept_rank.get((d, c))
            if rk is None or total == 0:
                continue
            if rk < 3:
                sc = 4
            elif rk < 7:
                sc = 3
            elif rk < total - 4:
                sc = 2
            else:
                sc = 1
            if best is None or sc > best:
                best = sc
        cand = []
        if best is not None:
            cand.append((best, concept_mom.get((d, s["concepts"][0]))))
        isc, ir1m = industry_score(code, d)
        if isc is not None:
            cand.append((isc, ir1m))
        if not cand:
            return 2, None
        sc, r1m = max(cand, key=lambda x: x[0])
        return sc, r1m

    # ── 逐样本标标签 ──
    buckets: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        f, d = s["f"], s["date"]
        tags = signal_tags(f)
        s["tags"] = tags
        # 板块热度
        heat, heat_r1m = heat_block(s)
        s["heat"] = heat
        # 相对强度 (个股 1M - 所属最热 concept 的 1M 均值)
        deltas = [f["pct_1m"] - concept_mom[(d, c)]
                  for c in s["concepts"] if (d, c) in concept_mom]
        delta = max(deltas) if deltas else None
        s["rel_delta"] = delta
        ra = rel_adj(delta)

        sell, buy = sell_buy_sets_proxy(f)
        sp = sell_penalty(sell)
        bb = 1 if buy else 0

        base = heat - sp + bb + ra
        g_bal = "A" if base >= 5 else ("B" if base >= 3 else ("C" if base >= 2 else "D"))
        # momentum style
        adj_m = base
        if "SELL_EXTREME" in sell or "SELL_CONFIRMED" in sell or "SELL_TOP" in sell:
            adj_m = min(adj_m, 1)
        elif "SELL_EXHAUSTION" in sell:
            adj_m = min(adj_m, 2)
        g_mom = "A" if adj_m >= 5 else ("B" if adj_m >= 3 else ("C" if adj_m >= 2 else "D"))

        s["grade_bal"], s["grade_mom"] = g_bal, g_mom
        s["adj_bal"], s["adj_mom"] = base, adj_m

        for t in tags:
            buckets[t].append(s)
        buckets["BASELINE_ALL"].append(s)
        buckets[f"HEAT_{heat}"].append(s)
        buckets[f"GRADE_BAL_{g_bal}"].append(s)
        buckets[f"GRADE_MOM_{g_mom}"].append(s)
        buckets[f"REL_{ra:+d}"].append(s)
        if delta is not None:
            key = ("RELD_gt20" if delta > 20 else "RELD_10_20" if delta > 10
                   else "RELD_-5_10" if delta > -5 else "RELD_-15_-5"
                   if delta > -15 else "RELD_lt-15")
            buckets[key].append(s)
        if sell:
            buckets["HAS_SELL_ANY@" + "/".join(sorted(sell))].append(s)
        if buy:
            buckets["HAS_BUY_ANY@" + "/".join(sorted(buy))].append(s)

    def agg(rows: list[dict]) -> dict:
        out = {"n": len(rows)}
        for h in FWDS:
            a = [r["fwd"][str(h)] for r in rows if r["fwd"][str(h)] is not None]
            e = [r["ex"][str(h)] for r in rows if r.get("ex", {}).get(str(h)) is not None]
            out[f"abs_{h}d"] = round(statistics.fmean(a), 2) if a else None
            out[f"ex_{h}d"] = round(statistics.fmean(e), 2) if e else None
            out[f"win_{h}d"] = round(sum(1 for v in e if v > 0) / len(e) * 100, 1) if e else None
        return out

    result = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "universe_n": len(universe),
            "samples": len(samples),
            "eval_dates": len(eval_dates),
            "eval_range": [eval_dates[0], eval_dates[-1]] if eval_dates else None,
            "step": args.step,
            "caveats": [
                "股票池为当前 concept 成分股 → 幸存者/选择偏差",
                "concept 热度用成员 pct_1m 均值排名重建, 与生产全市场口径近似",
                "industry 通道用全市场 daily 快照重建 (申万 L1 1M 均涨)",
                "未叠加估值乖离 + fundamentals_adj (未接入执行路径)",
                "超额 = 同日股票池等权去均值",
                "signals 分 proxy (生产: pos=position_proxy(r1m)) / real (pos120) 两口径",
            ],
        },
        "buckets": {k: agg(v) for k, v in sorted(buckets.items())},
    }

    out_path = Path(args.out) if args.out else Path(
        f"/tmp/component_audit_{datetime.now():%Y%m%d-%H%M}.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    def show(name: str, st: dict) -> str:
        cells = [f"{name:<32}", f"n={st['n']:<6}"]
        for h in FWDS:
            a, e, w = st.get(f"abs_{h}d"), st.get(f"ex_{h}d"), st.get(f"win_{h}d")
            cells.append(f"{h}d ex={e if e is not None else '-':>7} win="
                         f"{w if w is not None else '-':>5}")
        return " | ".join(cells)

    order = ["BASELINE_ALL"]
    order += [f"SIG_{x}@{p}" for x in ("BUY_EARLY", "BUY_BREAKOUT")
              for p in ("proxy", "real")]
    order += [f"SIG_{x}@{p}" for x in ("SELL_EXHAUSTION", "SELL_CONFIRMED",
                                       "SELL_EXTREME", "SELL_TOP")
              for p in ("proxy", "real")]
    order += ["SIG_ANY_BUY@hit", "SIG_ANY_BUY@miss"]
    order += [f"HEAT_{i}" for i in (1, 2, 3, 4)]
    order += [f"REL_{v:+d}" for v in (2, 1, 0, -1, -2)]
    order += ["RELD_gt20", "RELD_10_20", "RELD_-5_10", "RELD_-15_-5", "RELD_lt-15"]
    order += [f"GRADE_BAL_{g}" for g in "ABCD"]
    order += [f"GRADE_MOM_{g}" for g in "ABCD"]

    print("\n=== 组件审计 (ex = 同日横截面超额%, win = 超额>0 比例%) ===")
    for name in order:
        if name in result["buckets"]:
            print(show(name, result["buckets"][name]))
    print("\n=== 组合命中 (HAS_*) ===")
    for name in sorted(result["buckets"]):
        if name.startswith("HAS_"):
            print(show(name, result["buckets"][name]))
    print(f"\n[done] {out_path}  ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
