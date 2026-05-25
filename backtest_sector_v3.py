"""backtest_sector_v3.py — sector_score v3 滚动 panel 回测.

目的: 验证 v3 fund_flow 子分 (基于 BK 大单/散户 z-score) 在历史上的
预测力, 并量化 user 提出的"非黑即白"问题:
  1. v3 score 在 cross-section 上分布形态: 是顶部堆 + 尾部孤立, 还是较均匀?
  2. IC (Information Coefficient): score 排名 vs 实际 fwd return 排名
  3. 极端档命中率: top quintile 表现是否真的优于 bottom quintile
  4. 时间一致性: 2025 vs 2026 IC 是否稳定

方法:
  - 对每个 mapped concept, 在每个评估日 t 用 ≤ t-1 的数据算 fund_flow_score_v3
  - 用 BK 自己的 close (moneyflow_ind_dc 表里就有) 算 fwd_1d / fwd_5d / fwd_20d
    收益 (concept 多 BK 时取均值)
  - 评估期: 2025-07 ~ 2026-04 (扣 burn-in + fwd 窗口)
  - 评估频率: 每周一 (减少自相关), 或每日 (更稠密)

输出:
  panel.jsonl: 一行一个 (date, concept, score_v3, fwd_1d, fwd_5d, fwd_20d, raw_z_main, raw_z_rate)
  summary 终端: IC by horizon / 分位收益 / 月份分组 / 分布统计
"""
from __future__ import annotations
import csv, json, statistics, subprocess, sys, math
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import bk_moneyflow as bf  # type: ignore
import yaml

OUT_PANEL = _HERE / ".cache/backtest_v3_panel.jsonl"
OUT_PANEL.parent.mkdir(exist_ok=True)


# ─── 数据预拉 ──────────────────────────────────────────────────────────────

def prefetch_all_bks(cmap: dict) -> dict[str, list[dict]]:
    """一次性拉所有 mapped BK 的全历史, 返回 {bk_code: rows ASC}."""
    all_bks = set()
    for c, v in cmap.items():
        for b in v.get("bks") or []:
            all_bks.add(b["code"])
    print(f"📥 预拉 {len(all_bks)} 个 BK 的历史数据...", file=sys.stderr)
    cache: dict[str, list[dict]] = {}
    for code in sorted(all_bks):
        rows = bf.fetch_bk_recent(code)
        cache[code] = rows
        print(f"  {code}: {len(rows)} rows ({rows[0]['date']} ~ {rows[-1]['date']})",
              file=sys.stderr)
    return cache


# ─── 单点 score 计算 (rows 已截断到 ≤ t-1) ─────────────────────────────────

def _bk_5d_pct_chg(rows: list[dict]) -> float | None:
    """BK 自身 5d close 涨幅 % (raw price momentum)."""
    if len(rows) < 6: return None
    c0 = rows[-6]["close"]; c1 = rows[-1]["close"]
    return (c1 - c0) / c0 * 100 if c0 > 0 else None


def _bk_nav20d(rows: list[dict]) -> float | None:
    """BK 20d 累计收益 % (close[-1] / close[-21] - 1)."""
    if len(rows) < 21: return None
    c0 = rows[-21]["close"]; c1 = rows[-1]["close"]
    return (c1 - c0) / c0 * 100 if c0 > 0 else None


def _bk_pos60(rows: list[dict]) -> float | None:
    """BK 当前 close 在最近 60 日里的百分位 [0..1] (1=新高).

    用于"末期"标识: pos60>0.9 = 离 60d 高点不到 10% 距离。
    """
    if len(rows) < 60: return None
    win = [r["close"] for r in rows[-60:]]
    cur = win[-1]
    rank = sum(1 for c in win if c <= cur)
    return rank / len(win)


def score_concept_at(concept: str, cmap: dict, prefetched: dict[str, list[dict]],
                     as_of: str, strategy: str = "v3") -> tuple[float, dict[str, Any]] | None:
    """用 ≤ as_of (含) 的数据算分.

    strategy:
      v3        -- 当前: clip ±2σ→±1, main*15 + rate*5
      v3_softer -- clip ±1σ→±1, main*10 + rate*3 (更紧分布)
      v3_revert -- v3 + |z|>1.5σ 反转拉回 30%
      v3_momo   -- v3 + BK 5d 价格动量 (±5)
      v3_xsec   -- 截面排名 (这次 batch 不算, 需要全 concept 一起)
    """
    bks = cmap.get(concept, {}).get("bks") or []
    if not bks: return None
    sigs = []
    bk_5d = []
    bk_nav20 = []
    bk_pos60 = []
    for entry in bks:
        full = prefetched.get(entry["code"]) or []
        rows = [r for r in full if r["date"] <= as_of]
        if len(rows) < 80: continue
        sig = bf.compute_bk_signal(rows)
        if sig is None: continue
        sig.bk_code = entry["code"]; sig.bk_name = entry["name"]
        sigs.append(sig)
        m5 = _bk_5d_pct_chg(rows)
        if m5 is not None: bk_5d.append(m5)
        n20 = _bk_nav20d(rows)
        if n20 is not None: bk_nav20.append(n20)
        p60 = _bk_pos60(rows)
        if p60 is not None: bk_pos60.append(p60)
    if not sigs: return None

    z_main = [s.main_minus_sm_z for s in sigs if s.main_minus_sm_z is not None]
    z_rate = [s.rate_z for s in sigs if s.rate_z is not None]
    avg_z_main = statistics.mean(z_main) if z_main else None
    avg_z_rate = statistics.mean(z_rate) if z_rate else None
    avg_5d_pct = statistics.mean(bk_5d) if bk_5d else None
    avg_nav20 = statistics.mean(bk_nav20) if bk_nav20 else None  # %, e.g. 9.6
    avg_pos60 = statistics.mean(bk_pos60) if bk_pos60 else None  # 0..1

    if strategy == "v3":
        cm = max(-2.0, min(2.0, avg_z_main))/2.0 if avg_z_main is not None else 0
        cr = max(-2.0, min(2.0, avg_z_rate))/2.0 if avg_z_rate is not None else 0
        score = 20.0 + cm*15.0 + cr*5.0
    elif strategy == "v3_softer":
        # clip ±1σ (而非 ±2σ): 把极值都按 ±1σ 截掉, 让中段更鼓
        cm = max(-1.0, min(1.0, avg_z_main)) if avg_z_main is not None else 0
        cr = max(-1.0, min(1.0, avg_z_rate)) if avg_z_rate is not None else 0
        score = 20.0 + cm*10.0 + cr*3.0  # ±13 swing
    elif strategy == "v3_revert":
        # 基础 v3, 但当 |z|>1.5σ 时, 把 score 往 mid (20) 拉回 30%
        # → 反映"极端 z 是反转点而非延续"的实证
        cm = max(-2.0, min(2.0, avg_z_main))/2.0 if avg_z_main is not None else 0
        cr = max(-2.0, min(2.0, avg_z_rate))/2.0 if avg_z_rate is not None else 0
        score = 20.0 + cm*15.0 + cr*5.0
        if avg_z_main is not None and abs(avg_z_main) > 1.5:
            score = 0.7*score + 0.3*20.0  # 反转拉回
    elif strategy == "v3_momo":
        # v3 + BK 5d 价格动量 (±5σ → ±5 pts)
        cm = max(-2.0, min(2.0, avg_z_main))/2.0 if avg_z_main is not None else 0
        cr = max(-2.0, min(2.0, avg_z_rate))/2.0 if avg_z_rate is not None else 0
        # 动量 ±5%-> ±1 (clip)
        mo = max(-1.0, min(1.0, (avg_5d_pct or 0)/5.0))
        score = 20.0 + cm*12.0 + cr*4.0 + mo*5.0
    elif strategy == "v3_soft_revert":
        # softer + 极端反转: 已经很紧的范围里, 当 |z|>1.5σ 时进一步压缩 30%
        cm = max(-1.0, min(1.0, avg_z_main)) if avg_z_main is not None else 0
        cr = max(-1.0, min(1.0, avg_z_rate)) if avg_z_rate is not None else 0
        score = 20.0 + cm*10.0 + cr*3.0
        if avg_z_main is not None and abs(avg_z_main) > 1.5:
            score = 0.7*score + 0.3*20.0
    elif strategy in ("v3_ob_hard03", "v3_ob_hard05", "v3_ob_smooth", "v3_ob_smart",
                       "v3_ob_hard05_hi_only"):
        # v3.1 (soft_revert) baseline + 末期反转 penalty.
        # 末期定义: nav20d > 15% AND pos60 > 0.9
        # 三档 penalty 强度:
        #   v3_ob_hard03 — 命中 = score 拉回中位 30% (温和)
        #   v3_ob_hard05 — 命中 = score 拉回中位 50% (强)
        #   v3_ob_smooth — 用 nav20 / pos60 连续函数: penalty = 0.6 * sigmoid * pos60_clip
        cm = max(-1.0, min(1.0, avg_z_main)) if avg_z_main is not None else 0
        cr = max(-1.0, min(1.0, avg_z_rate)) if avg_z_rate is not None else 0
        score = 20.0 + cm*10.0 + cr*3.0
        if avg_z_main is not None and abs(avg_z_main) > 1.5:
            score = 0.7*score + 0.3*20.0  # v3.1 反转拉回保留
        # 末期 penalty
        n20 = avg_nav20 if avg_nav20 is not None else 0.0
        p60 = avg_pos60 if avg_pos60 is not None else 0.0
        if strategy == "v3_ob_hard03":
            if n20 > 15.0 and p60 > 0.9:
                score = 0.7*score + 0.3*20.0
        elif strategy == "v3_ob_hard05":
            if n20 > 15.0 and p60 > 0.9:
                score = 0.5*score + 0.5*20.0
        elif strategy == "v3_ob_smooth":
            # sigmoid((nav20-10)/5): nav20=10→0.5, nav20=20→0.88, nav20=5→0.12
            try:
                sig_n = 1.0 / (1.0 + math.exp(-(n20 - 10.0) / 5.0))
            except OverflowError:
                sig_n = 1.0 if n20 > 10 else 0.0
            # pos60 0.7 以下不惩罚, 0.9 以上满惩罚
            p60_pen = max(0.0, min(1.0, (p60 - 0.7) / 0.2))
            penalty_strength = 0.6 * sig_n * p60_pen  # 0..0.6
            if score > 20:  # 只惩罚向上偏离的 (避免拉低本来就低分的)
                score = (1 - penalty_strength) * score + penalty_strength * 20.0
        elif strategy == "v3_ob_smart":
            # 基于 prereq 实证: nav20 ∈ [15, 30) 是末期反转, ≥30 是趋势龙头主升浪.
            # 在 [15, 30) 区间用 hard 50% 拉回, [30, ∞) 不动 ("主升浪豁免").
            # 进一步: pos60 必须 ≥ 0.9 才生效 (排除"高位但低 pos60" 的回调买点).
            if 15.0 <= n20 < 30.0 and p60 >= 0.9:
                score = 0.5*score + 0.5*20.0
        elif strategy == "v3_ob_hard05_hi_only":
            # v3_ob_hard05 改进: 只罚高分 (score > 20), 不奖低分.
            # 实战时发现 v3_ob_hard05 把低分(12.6)往中位 20 拉变成 16.3, 等于错误奖励.
            if n20 >= 15.0 and p60 >= 0.9 and score > 20:
                score = 0.5*score + 0.5*20.0
    else:
        raise ValueError(f"unknown strategy {strategy}")

    score = max(0.0, min(40.0, round(score, 2)))
    return score, {"z_main": avg_z_main, "z_rate": avg_z_rate,
                   "bk_5d_pct": avg_5d_pct, "nav20": avg_nav20,
                   "pos60": avg_pos60, "n_bks": len(sigs)}


def fwd_return(concept: str, cmap: dict, prefetched: dict[str, list[dict]],
               as_of: str, horizon_days: int) -> float | None:
    """T+horizon 收益: 多 BK 取 close 涨幅均值. as_of 是预测发出日 (用 t close).

    用 trade-day 计数: 找到 as_of 的 idx, 取 idx+horizon close.
    """
    bks = cmap.get(concept, {}).get("bks") or []
    if not bks:
        return None
    rets = []
    for entry in bks:
        rows = prefetched.get(entry["code"]) or []
        # 找 as_of 在 rows 里的 idx
        idx = next((i for i, r in enumerate(rows) if r["date"] == as_of), None)
        if idx is None or idx + horizon_days >= len(rows):
            continue
        c0 = rows[idx]["close"]; c1 = rows[idx + horizon_days]["close"]
        if c0 <= 0:
            continue
        rets.append((c1 - c0) / c0 * 100)
    return statistics.mean(rets) if rets else None


# ─── 主回测 loop ──────────────────────────────────────────────────────────

def run_backtest(weekly: bool = False, strategy: str = "v3") -> list[dict]:
    cmap = bf.load_concept_bk_map()
    mapped_concepts = [c for c, v in cmap.items() if v.get("bks")]
    print(f"🎯 {len(mapped_concepts)} mapped concepts: {mapped_concepts}", file=sys.stderr)
    prefetched = prefetch_all_bks(cmap)

    # 每个 concept 独立 eligible_dates: 用 concept 自己 BK 的公共日期 (intersection
    # of just its own BKs, 多 BK concept 只取共有的). 排除短历史 BK 拖累全部.
    panel = []
    union_dates = sorted({r["date"] for v in prefetched.values() for r in v})

    for c in mapped_concepts:
        bks = cmap[c].get("bks") or []
        bk_codes = [b["code"] for b in bks]
        if not bk_codes: continue
        bk_date_sets = [set(r["date"] for r in prefetched[code]) for code in bk_codes
                        if prefetched.get(code)]
        if not bk_date_sets: continue
        c_dates = sorted(set.intersection(*bk_date_sets))
        # burn-in 80d + fwd 20d
        c_eligible = c_dates[80:-20] if len(c_dates) > 100 else []
        if weekly:
            c_eligible = [d for d in c_eligible if datetime.strptime(d, "%Y%m%d").weekday() == 0]
        if not c_eligible:
            print(f"  ⚠️ {c}: 数据不足 ({len(c_dates)} dates → 0 eligible)", file=sys.stderr)
            continue
        print(f"  ✓ {c}: {len(c_eligible)} eligible dates ({c_eligible[0]} ~ {c_eligible[-1]})",
              file=sys.stderr)

        for d in c_eligible:
            res = score_concept_at(c, cmap, prefetched, d, strategy=strategy)
            if res is None:
                continue
            score, diag = res
            r1  = fwd_return(c, cmap, prefetched, d, 1)
            r5  = fwd_return(c, cmap, prefetched, d, 5)
            r20 = fwd_return(c, cmap, prefetched, d, 20)
            panel.append({
                "date": d, "concept": c, "score": score,
                "z_main": diag.get("z_main"), "z_rate": diag.get("z_rate"),
                "nav20": diag.get("nav20"), "pos60": diag.get("pos60"),
                "fwd_1d": r1, "fwd_5d": r5, "fwd_20d": r20,
            })
    return panel


# ─── 评估指标 ───────────────────────────────────────────────────────────

def spearman(xs: list[float], ys: list[float]) -> float | None:
    """rank-Spearman correlation."""
    if len(xs) < 5: return None
    rx = _ranks(xs); ry = _ranks(ys)
    return _pearson(rx, ry)

def _pearson(xs, ys):
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    num = sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
    dx = math.sqrt(sum((x-mx)**2 for x in xs))
    dy = math.sqrt(sum((y-my)**2 for y in ys))
    return num/(dx*dy) if dx>0 and dy>0 else None

def _ranks(xs):
    sorted_pairs = sorted(enumerate(xs), key=lambda t: t[1])
    ranks = [0.0]*len(xs)
    for r, (i, _) in enumerate(sorted_pairs):
        ranks[i] = r + 1
    return ranks


def summarize(panel: list[dict]):
    print("\n" + "=" * 88)
    print(f"📊 v3 滚动回测 — n={len(panel)} obs")
    print("=" * 88)

    # ─── overall IC ───
    print("\n━━━ Overall IC (score vs fwd return, Spearman rank corr) ━━━")
    for h in ("fwd_1d", "fwd_5d", "fwd_20d"):
        clean = [(p["score"], p[h]) for p in panel if p[h] is not None]
        if len(clean) >= 30:
            xs, ys = zip(*clean)
            ic = spearman(list(xs), list(ys))
            print(f"  {h:<10}  n={len(clean):>4}   IC={ic:+.3f}" if ic else f"  {h:<10}  IC=NaN")

    # ─── 分位收益 (top vs bottom quintile) ───
    print("\n━━━ Quintile Returns (avg fwd return by score quintile) ━━━")
    for h in ("fwd_5d", "fwd_20d"):
        clean = [(p["score"], p[h]) for p in panel if p[h] is not None]
        if len(clean) < 50: continue
        clean.sort(key=lambda t: t[0])
        n = len(clean); q = n // 5
        bins = [clean[i*q:(i+1)*q] for i in range(5)]
        print(f"  {h}:")
        for i, b in enumerate(bins):
            scores = [t[0] for t in b]; rets = [t[1] for t in b]
            print(f"    Q{i+1} (score {min(scores):.1f}-{max(scores):.1f}): "
                  f"avg ret={statistics.mean(rets):+.2f}%  n={len(b)}")
        ls = statistics.mean([t[1] for t in bins[-1]]) - statistics.mean([t[1] for t in bins[0]])
        print(f"    L/S spread Q5-Q1: {ls:+.2f}%")

    # ─── score 分布 (cross-sectional 每日) ───
    print("\n━━━ Score 分布 (cross-sectional, 每日 11 concepts) ━━━")
    by_date = defaultdict(list)
    for p in panel:
        by_date[p["date"]].append(p["score"])
    daily_stats = []
    for d, scores in by_date.items():
        if len(scores) < 5: continue
        daily_stats.append({
            "date": d, "n": len(scores),
            "min": min(scores), "max": max(scores),
            "range": max(scores) - min(scores),
            "std": statistics.stdev(scores) if len(scores) > 1 else 0,
            "p_extreme": sum(1 for s in scores if s <= 5 or s >= 35) / len(scores),
        })
    if daily_stats:
        avg_range = statistics.mean(d["range"] for d in daily_stats)
        avg_std = statistics.mean(d["std"] for d in daily_stats)
        avg_extreme = statistics.mean(d["p_extreme"] for d in daily_stats)
        print(f"  日均 score range (max-min): {avg_range:.1f} (40 满分)")
        print(f"  日均 std:                   {avg_std:.2f}")
        print(f"  日均极端档比例 (<=5 或 >=35): {avg_extreme*100:.1f}%")
        # 中段空虚度: 多少 score 落在 15-25 中段
        mid_count = sum(1 for p in panel if 15 <= p["score"] <= 25)
        print(f"  中段 (15-25) 占比:           {mid_count/len(panel)*100:.1f}% "
              f"(健康分布应 > 40%)")

    # ─── 月份切片 (近期 vs 早期) ───
    print("\n━━━ 月份切片 IC ━━━")
    by_month = defaultdict(list)
    for p in panel:
        ym = p["date"][:6]
        by_month[ym].append(p)
    for ym in sorted(by_month):
        ms = by_month[ym]
        clean = [(x["score"], x["fwd_5d"]) for x in ms if x["fwd_5d"] is not None]
        if len(clean) < 20: continue
        ic5 = spearman([t[0] for t in clean], [t[1] for t in clean])
        clean20 = [(x["score"], x["fwd_20d"]) for x in ms if x["fwd_20d"] is not None]
        ic20 = spearman([t[0] for t in clean20], [t[1] for t in clean20]) if len(clean20) >= 20 else None
        ic5s = f"{ic5:+.3f}" if ic5 is not None else "  n/a"
        ic20s = f"{ic20:+.3f}" if ic20 is not None else "  n/a"
        print(f"  {ym}: n={len(ms):>3}  IC5d={ic5s}  IC20d={ic20s}")


def compare_strategies(strategies: list[str]):
    """跑多个 strategy 共用 prefetch, 输出对比表."""
    cmap = bf.load_concept_bk_map()
    mapped_concepts = [c for c, v in cmap.items() if v.get("bks")]
    prefetched = prefetch_all_bks(cmap)
    union_dates = sorted({r["date"] for v in prefetched.values() for r in v})

    # 每个 concept 的 eligible_dates 一次性算
    concept_dates: dict[str, list[str]] = {}
    for c in mapped_concepts:
        bks = cmap[c].get("bks") or []
        bk_codes = [b["code"] for b in bks]
        bk_date_sets = [set(r["date"] for r in prefetched[code]) for code in bk_codes
                        if prefetched.get(code)]
        if not bk_date_sets: continue
        c_dates = sorted(set.intersection(*bk_date_sets))
        concept_dates[c] = c_dates[80:-20] if len(c_dates) > 100 else []

    print("\n" + "=" * 100)
    print(f"⚔️  Strategy Comparison ({len(strategies)} variants)")
    print("=" * 100)
    print(f"{'strategy':<14} {'n':>5} {'IC_1d':>8} {'IC_5d':>8} {'IC_20d':>8}  "
          f"{'Q5-Q1_5d':>10} {'Q5-Q1_20d':>10}  {'range':>6} {'std':>6} {'ext%':>6} {'mid%':>6} "
          f"{'IC_std':>8}")
    print("-" * 100)

    summaries = []
    for strat in strategies:
        panel = []
        for c, dates in concept_dates.items():
            for d in dates:
                res = score_concept_at(c, cmap, prefetched, d, strategy=strat)
                if res is None: continue
                score, diag = res
                r1  = fwd_return(c, cmap, prefetched, d, 1)
                r5  = fwd_return(c, cmap, prefetched, d, 5)
                r20 = fwd_return(c, cmap, prefetched, d, 20)
                panel.append({"date": d, "concept": c, "score": score,
                              "fwd_1d": r1, "fwd_5d": r5, "fwd_20d": r20})

        # IC
        def ic_h(h):
            clean = [(p["score"], p[h]) for p in panel if p[h] is not None]
            if len(clean) < 30: return None
            return spearman([t[0] for t in clean], [t[1] for t in clean])

        ic1 = ic_h("fwd_1d"); ic5 = ic_h("fwd_5d"); ic20 = ic_h("fwd_20d")

        # quintile L/S
        def ls(h):
            clean = sorted([(p["score"], p[h]) for p in panel if p[h] is not None],
                           key=lambda t: t[0])
            if len(clean) < 50: return None
            n = len(clean); q = n // 5
            r_q1 = statistics.mean([t[1] for t in clean[:q]])
            r_q5 = statistics.mean([t[1] for t in clean[-q:]])
            return r_q5 - r_q1

        # 分布
        by_date: dict[str, list[float]] = defaultdict(list)
        for p in panel:
            by_date[p["date"]].append(p["score"])
        ranges = [max(s)-min(s) for s in by_date.values() if len(s)>=5]
        stds = [statistics.stdev(s) for s in by_date.values() if len(s)>=5]
        avg_range = statistics.mean(ranges) if ranges else 0
        avg_std = statistics.mean(stds) if stds else 0
        ext_pct = sum(1 for p in panel if p["score"]<=5 or p["score"]>=35)/len(panel)*100
        mid_pct = sum(1 for p in panel if 15<=p["score"]<=25)/len(panel)*100

        # 月度 IC stability
        by_m: dict[str, list] = defaultdict(list)
        for p in panel:
            if p["fwd_5d"] is not None:
                by_m[p["date"][:6]].append((p["score"], p["fwd_5d"]))
        m_ics = []
        for ym, lst in by_m.items():
            if len(lst) >= 30:
                ic = spearman([t[0] for t in lst], [t[1] for t in lst])
                if ic is not None: m_ics.append(ic)
        ic_std = statistics.stdev(m_ics) if len(m_ics)>=2 else None

        def _fmt(v, pat=">+8.3f"): return format(v, pat) if v is not None else " " * 8

        print(f"{strat:<14} {len(panel):>5} "
              f"{_fmt(ic1):>8} {_fmt(ic5):>8} {_fmt(ic20):>8}  "
              f"{_fmt(ls('fwd_5d'),'>+10.2f'):>10} {_fmt(ls('fwd_20d'),'>+10.2f'):>10}  "
              f"{avg_range:>6.1f} {avg_std:>6.2f} {ext_pct:>5.1f}% {mid_pct:>5.1f}% "
              f"{_fmt(ic_std,'>8.3f'):>8}")
        summaries.append({"strategy": strat, "panel": panel, "ic5": ic5, "ic20": ic20,
                          "ic_std": ic_std, "range": avg_range, "ext": ext_pct, "mid": mid_pct})
    return summaries


def cross_section_diagnose():
    """诊断当前 score 在每日 cross-section 的并列严重度.

    对每个评估日, 看:
      - n_unique_score / n_concept (区分度, 1=全不同)
      - 最大并列簇大小 (max cluster, 1=没并列)
      - 日均 std / range
      - 排名变动稳定性 (跨日 Spearman)
    """
    cmap = bf.load_concept_bk_map()
    mapped_concepts = [c for c, v in cmap.items() if v.get("bks")]
    prefetched = prefetch_all_bks(cmap)

    rows = []
    for c in mapped_concepts:
        bks = cmap[c].get("bks") or []
        bk_codes = [b["code"] for b in bks]
        bk_date_sets = [set(r["date"] for r in prefetched[code]) for code in bk_codes
                        if prefetched.get(code)]
        if not bk_date_sets: continue
        c_dates = sorted(set.intersection(*bk_date_sets))
        c_eligible = c_dates[80:-20] if len(c_dates) > 100 else []

        for d in c_eligible:
            res = score_concept_at(c, cmap, prefetched, d, strategy="v3_ob_hard05_hi_only")
            if res is None: continue
            score, _ = res
            rows.append({"date": d, "concept": c, "score": score})

    by_date = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append((r["concept"], r["score"]))

    print("\n" + "=" * 88)
    print(f"🩺 v3.2 cross-section 并列诊断 — n_dates={len(by_date)}")
    print("=" * 88)

    # 统计每日并列严重度
    cluster_stats = []
    for d, items in by_date.items():
        if len(items) < 3: continue
        scores = [s for _, s in items]
        unique_scores = len(set(round(s, 1) for s in scores))
        # 最大同分簇
        from collections import Counter
        clusters = Counter(round(s, 1) for s in scores)
        max_cluster = max(clusters.values())
        cluster_stats.append({
            "date": d, "n": len(items),
            "unique": unique_scores,
            "max_cluster": max_cluster,
            "p_dup": (len(items) - unique_scores) / len(items),
        })

    avg_unique = statistics.mean(s["unique"] for s in cluster_stats)
    avg_n = statistics.mean(s["n"] for s in cluster_stats)
    avg_pdup = statistics.mean(s["p_dup"] for s in cluster_stats)
    p_3plus_cluster = sum(1 for s in cluster_stats if s["max_cluster"] >= 3) / len(cluster_stats)
    p_4plus_cluster = sum(1 for s in cluster_stats if s["max_cluster"] >= 4) / len(cluster_stats)
    print(f"  日均: {avg_n:.1f} 板块 / {avg_unique:.1f} 不同分 → 重复率 {avg_pdup*100:.1f}%")
    print(f"  最大并列簇 ≥3 板块的天数: {p_3plus_cluster*100:.1f}%")
    print(f"  最大并列簇 ≥4 板块的天数: {p_4plus_cluster*100:.1f}%")

    # 跨日排名稳定性 (Spearman of t vs t+5)
    sorted_dates = sorted(by_date.keys())
    spear_5 = []
    for i in range(len(sorted_dates) - 5):
        d0 = sorted_dates[i]; d5 = sorted_dates[i + 5]
        m0 = dict(by_date[d0]); m5 = dict(by_date[d5])
        common = set(m0) & set(m5)
        if len(common) < 5: continue
        xs = [m0[c] for c in common]; ys = [m5[c] for c in common]
        s = spearman(xs, ys)
        if s is not None: spear_5.append(s)
    if spear_5:
        print(f"  跨 5 天排名 Spearman 均值: {statistics.mean(spear_5):+.3f} (1=完全稳, 0=随机)")
        print(f"    std: {statistics.stdev(spear_5):.3f}")


def compare_xsec_strategies():
    """对比横截面排名变体 vs v3.2 baseline.

    所有变体都基于 v3_ob_hard05_hi_only 的原始分数, 然后:
      v3.2_baseline -- 不动
      xs_rank_pure  -- 完全用横截面 rank 替换 (0..40 线性)
      xs_hybrid_30  -- 0.7 * 原分 + 0.3 * (rank_pct * 40)
      xs_hybrid_50  -- 0.5 * 原分 + 0.5 * (rank_pct * 40)
    """
    cmap = bf.load_concept_bk_map()
    mapped_concepts = [c for c, v in cmap.items() if v.get("bks")]
    prefetched = prefetch_all_bks(cmap)
    union_dates = sorted({r["date"] for v in prefetched.values() for r in v})

    concept_dates: dict[str, list[str]] = {}
    for c in mapped_concepts:
        bks = cmap[c].get("bks") or []
        bk_codes = [b["code"] for b in bks]
        bk_date_sets = [set(r["date"] for r in prefetched[code]) for code in bk_codes
                        if prefetched.get(code)]
        if not bk_date_sets: continue
        c_dates = sorted(set.intersection(*bk_date_sets))
        concept_dates[c] = c_dates[80:-20] if len(c_dates) > 100 else []

    # 一次性算原分 (v3.2 hi_only) + fwd return
    base_panel = []
    for c, dates in concept_dates.items():
        for d in dates:
            res = score_concept_at(c, cmap, prefetched, d, strategy="v3_ob_hard05_hi_only")
            if res is None: continue
            score, diag = res
            r5 = fwd_return(c, cmap, prefetched, d, 5)
            r20 = fwd_return(c, cmap, prefetched, d, 20)
            base_panel.append({"date": d, "concept": c, "score_orig": score,
                               "fwd_5d": r5, "fwd_20d": r20})

    # 算每日 rank
    by_date_orig: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for p in base_panel:
        by_date_orig[p["date"]].append((p["concept"], p["score_orig"]))

    rank_lookup: dict[tuple[str, str], float] = {}  # (date, concept) -> pct rank [0..1]
    for d, items in by_date_orig.items():
        if len(items) < 2: continue
        sorted_items = sorted(items, key=lambda t: t[1])
        n = len(sorted_items)
        # average rank for ties
        for rank_pos, (c, _) in enumerate(sorted_items):
            rank_lookup[(d, c)] = rank_pos / max(1, n - 1)  # 0..1

    # 算每个 strategy 的最终 score + IC + diversity
    strategies = {
        "v3.2_baseline": lambda p: p["score_orig"],
        "xs_rank_pure":  lambda p: rank_lookup.get((p["date"], p["concept"]), 0.5) * 40.0,
        "xs_hybrid_30":  lambda p: 0.7 * p["score_orig"]
                                   + 0.3 * rank_lookup.get((p["date"], p["concept"]), 0.5) * 40.0,
        "xs_hybrid_50":  lambda p: 0.5 * p["score_orig"]
                                   + 0.5 * rank_lookup.get((p["date"], p["concept"]), 0.5) * 40.0,
    }

    print("\n" + "=" * 110)
    print(f"⚔️  Cross-section Strategy Comparison (n={len(base_panel)})")
    print("=" * 110)
    print(f"{'strategy':<16} {'IC_5d':>8} {'IC_20d':>8} {'Q5-Q1_5d':>10} {'Q5-Q1_20d':>11}  "
          f"{'avg_unique':>12} {'p_dup':>8} {'p_clust4':>10} {'IC_std':>8}")
    print("-" * 110)

    for label, scorer in strategies.items():
        panel = []
        for p in base_panel:
            new_score = scorer(p)
            panel.append({**p, "score": new_score})

        def ic_h(h):
            clean = [(p["score"], p[h]) for p in panel if p[h] is not None]
            if len(clean) < 30: return None
            return spearman([t[0] for t in clean], [t[1] for t in clean])

        def ls(h):
            clean = sorted([(p["score"], p[h]) for p in panel if p[h] is not None],
                           key=lambda t: t[0])
            if len(clean) < 50: return None
            n = len(clean); q = n // 5
            r_q1 = statistics.mean([t[1] for t in clean[:q]])
            r_q5 = statistics.mean([t[1] for t in clean[-q:]])
            return r_q5 - r_q1

        # diversity stats
        by_date_ext = defaultdict(list)
        for p in panel: by_date_ext[p["date"]].append(p["score"])
        uniques = []
        p_dups = []
        cluster4 = 0
        for d, sc in by_date_ext.items():
            if len(sc) < 3: continue
            from collections import Counter
            cnt = Counter(round(x, 1) for x in sc)
            uniques.append(len(cnt))
            p_dups.append((len(sc) - len(cnt)) / len(sc))
            if max(cnt.values()) >= 4: cluster4 += 1

        # 月度 IC stability
        by_m = defaultdict(list)
        for p in panel:
            if p["fwd_5d"] is not None:
                by_m[p["date"][:6]].append((p["score"], p["fwd_5d"]))
        m_ics = []
        for ym, lst in by_m.items():
            if len(lst) >= 30:
                ic = spearman([t[0] for t in lst], [t[1] for t in lst])
                if ic is not None: m_ics.append(ic)
        ic_std = statistics.stdev(m_ics) if len(m_ics) >= 2 else None

        ic5 = ic_h("fwd_5d"); ic20 = ic_h("fwd_20d")
        ls5 = ls("fwd_5d"); ls20 = ls("fwd_20d")
        avg_uniq = statistics.mean(uniques) if uniques else 0
        avg_pdup = statistics.mean(p_dups) if p_dups else 0
        cluster4_pct = cluster4 / len(by_date_ext) * 100

        def _f(v, p=">+8.3f"):
            return format(v, p) if v is not None else " " * 8

        print(f"{label:<16} {_f(ic5):>8} {_f(ic20):>8} "
              f"{_f(ls5,'>+10.2f'):>10} {_f(ls20,'>+11.2f'):>11}  "
              f"{avg_uniq:>12.1f} {avg_pdup*100:>7.1f}% {cluster4_pct:>9.1f}% "
              f"{_f(ic_std,'>8.3f'):>8}")


def overbought_prereq_test():
    """先验证: '末期' 状态在历史上是否真的对应 fwd_5d 衰减?

    如果不衰减 (甚至更高), penalty 就是错的, 直接放弃。
    """
    cmap = bf.load_concept_bk_map()
    mapped_concepts = [c for c, v in cmap.items() if v.get("bks")]
    prefetched = prefetch_all_bks(cmap)

    rows_out = []
    for c in mapped_concepts:
        bks = cmap[c].get("bks") or []
        bk_codes = [b["code"] for b in bks]
        bk_date_sets = [set(r["date"] for r in prefetched[code]) for code in bk_codes
                        if prefetched.get(code)]
        if not bk_date_sets: continue
        c_dates = sorted(set.intersection(*bk_date_sets))
        c_eligible = c_dates[80:-20] if len(c_dates) > 100 else []

        for d in c_eligible:
            res = score_concept_at(c, cmap, prefetched, d, strategy="v3_soft_revert")
            if res is None: continue
            score, diag = res
            r5 = fwd_return(c, cmap, prefetched, d, 5)
            r20 = fwd_return(c, cmap, prefetched, d, 20)
            rows_out.append({
                "date": d, "concept": c, "score": score,
                "nav20": diag.get("nav20"), "pos60": diag.get("pos60"),
                "fwd_5d": r5, "fwd_20d": r20,
            })

    print("\n" + "=" * 100)
    print(f"🩺 末期反转 prereq — n={len(rows_out)} obs")
    print("=" * 100)

    # bucket by (nav20 high?) × (pos60 high?)
    buckets = {
        "正常 (nav20<10 OR pos60<0.7)":  lambda r: (r["nav20"] or 0) < 10 or (r["pos60"] or 0) < 0.7,
        "轻热 (nav20≥10, pos60≥0.7)":    lambda r: 10 <= (r["nav20"] or 0) < 15 and (r["pos60"] or 0) >= 0.7,
        "末期 A (nav20≥15, pos60≥0.9)":  lambda r: (r["nav20"] or 0) >= 15 and (r["pos60"] or 0) >= 0.9,
        "末期 B (nav20≥20, pos60≥0.95)": lambda r: (r["nav20"] or 0) >= 20 and (r["pos60"] or 0) >= 0.95,
        "末期 C (nav20≥25, pos60≥0.95)": lambda r: (r["nav20"] or 0) >= 25 and (r["pos60"] or 0) >= 0.95,
    }
    print(f"\n{'bucket':<35} {'n':>5} {'avg_score':>10} {'avg_fwd5d':>12} {'avg_fwd20d':>12}")
    print("-" * 80)
    for label, pred in buckets.items():
        sub = [r for r in rows_out if pred(r)]
        if not sub: continue
        n = len(sub)
        s5 = [r["fwd_5d"] for r in sub if r["fwd_5d"] is not None]
        s20 = [r["fwd_20d"] for r in sub if r["fwd_20d"] is not None]
        sc = [r["score"] for r in sub]
        print(f"{label:<35} {n:>5} "
              f"{statistics.mean(sc):>+10.2f} "
              f"{statistics.mean(s5):>+11.2f}% "
              f"{statistics.mean(s20) if s20 else 0:>+11.2f}%")

    # 再切: 在末期 A 内, 高分(>=25) vs 低分 fwd return
    print("\n--- 末期 A (nav20≥15 & pos60≥0.9) 内, 按 score 切 ---")
    sub = [r for r in rows_out if (r["nav20"] or 0) >= 15 and (r["pos60"] or 0) >= 0.9]
    print(f"  样本 n={len(sub)}")
    if sub:
        for thr in (15, 20, 25, 30):
            hi = [r for r in sub if r["score"] >= thr]
            lo = [r for r in sub if r["score"] < thr]
            if not hi or not lo: continue
            hi5 = statistics.mean([r["fwd_5d"] for r in hi if r["fwd_5d"] is not None])
            lo5 = statistics.mean([r["fwd_5d"] for r in lo if r["fwd_5d"] is not None])
            hi20 = statistics.mean([r["fwd_20d"] for r in hi if r["fwd_20d"] is not None])
            lo20 = statistics.mean([r["fwd_20d"] for r in lo if r["fwd_20d"] is not None])
            print(f"  score≥{thr}: n_hi={len(hi):>3}  fwd5={hi5:+.2f}%  fwd20={hi20:+.2f}%   |   "
                  f"score<{thr}: n_lo={len(lo):>3}  fwd5={lo5:+.2f}%  fwd20={lo20:+.2f}%")

    # nav20 单变量切片 (不限定 pos60)
    print("\n--- 单变量 nav20d 分桶 ---")
    print(f"  {'bucket':<25} {'n':>5} {'avg_fwd5d':>12} {'avg_fwd20d':>12}")
    nav_buckets = [(-999, 0), (0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 999)]
    for lo, hi in nav_buckets:
        sub = [r for r in rows_out if r["nav20"] is not None and lo <= r["nav20"] < hi]
        if len(sub) < 5: continue
        s5 = [r["fwd_5d"] for r in sub if r["fwd_5d"] is not None]
        s20 = [r["fwd_20d"] for r in sub if r["fwd_20d"] is not None]
        if not s5 or not s20: continue
        label = f"nav20 ∈ [{lo},{hi})" if hi != 999 else f"nav20 ≥ {lo}"
        print(f"  {label:<25} {len(sub):>5} {statistics.mean(s5):>+11.2f}% {statistics.mean(s20):>+11.2f}%")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true", help="只取每周一作评估日")
    ap.add_argument("--save-panel", action="store_true", help="保存 panel jsonl")
    ap.add_argument("--strategy", default="v3", help="v3 | v3_softer | v3_revert | v3_momo")
    ap.add_argument("--compare", action="store_true", help="跑 4 个变体对比")
    ap.add_argument("--ob-prereq", action="store_true", help="末期反转 prereq 测试")
    ap.add_argument("--ob-compare", action="store_true", help="跑 v3.1 vs ob_hard03/05/smooth 对比")
    ap.add_argument("--xs-diagnose", action="store_true", help="cross-section 并列诊断")
    ap.add_argument("--xs-compare", action="store_true", help="rank 变体对比")
    args = ap.parse_args()

    if args.ob_prereq:
        overbought_prereq_test()
        return
    if args.xs_diagnose:
        cross_section_diagnose()
        return
    if args.xs_compare:
        compare_xsec_strategies()
        return
    if args.ob_compare:
        compare_strategies(["v3_soft_revert", "v3_ob_hard05",
                            "v3_ob_hard05_hi_only", "v3_ob_smooth"])
        return
    if args.compare:
        compare_strategies(["v3", "v3_softer", "v3_revert", "v3_momo", "v3_soft_revert"])
        return

    panel = run_backtest(weekly=args.weekly, strategy=args.strategy)
    if args.save_panel:
        with open(OUT_PANEL, "w") as f:
            for r in panel:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\n💾 saved panel → {OUT_PANEL} ({len(panel)} rows)", file=sys.stderr)
    summarize(panel)


if __name__ == "__main__":
    main()
