"""backtest_news_phase2_unlock.py — 验证 Phase 2 解禁压力 (z_unlock).

策略 (复用 phase1 backtest 框架):
  1. 用 backtest_sector_v3 BK 历史 + 11 mapped concepts + fwd return panel
  2. 对每个 (date, concept) 算 z_unlock_60d
  3. 衡量:
     a) 单变量 IC: z_unlock vs fwd_5d, fwd_20d
     b) 月度稳定性
     c) 加入 phase1 的 z_repur 后增量 IC
     d) 加入 fund_score 后增量 IC

验收 (Phase 2 设计文档):
  - 单变量 IC_20d > +0.10 (取负后, 因为是反指: clip(-z, -2, +2) * 1.0)
  - 增量 IC vs fund 单 ≥ +5%
  - 月度反指月份占比 ≤ 1/4

注意 sparsity: 大量 obs 的 z_unlock = 0 (concept 当月无解禁), 这些会稀释 IC.
报告分两组: (a) 全 panel IC, (b) 仅 z_unlock != 0 的 IC (信号子集).
"""
from __future__ import annotations

import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import bk_moneyflow as bf  # type: ignore
import news_score_phase1 as nsp1  # type: ignore
import news_score_phase2_unlock as nsu  # type: ignore
import backtest_sector_v3 as btv3  # type: ignore
from concepts_data import stocks_of, CONCEPTS  # type: ignore


# ─── stat helpers ──────────────────────────────────────────────────────────

def spearman(xs, ys):
    if len(xs) < 5: return None
    return _pearson(_ranks(xs), _ranks(ys))

def _pearson(xs, ys):
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    num = sum((xs[i]-mx)*(ys[i]-my) for i in range(n))
    dx = math.sqrt(sum((x-mx)**2 for x in xs))
    dy = math.sqrt(sum((y-my)**2 for y in ys))
    return num/(dx*dy) if dx>0 and dy>0 else None

def _ranks(xs):
    sp = sorted(enumerate(xs), key=lambda t: t[1])
    r = [0.0]*len(xs)
    for k, (i, _) in enumerate(sp): r[i] = k + 1
    return r


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--end-date", default="20260522")
    ap.add_argument("--prefetch-days", type=int, default=240,
                   help="历史窗口长度 (天). 解禁信号需要 60d hist + 30d fwd unlock window")
    ap.add_argument("--include-all-concepts", action="store_true",
                   help="不限于 BK-mapped 11 个 concept (但没 fwd_return), 仅 demo 用")
    args = ap.parse_args()

    # 1. Load BK panel + mapped concepts
    cmap = bf.load_concept_bk_map()
    mapped_concepts = [c for c, v in cmap.items() if v.get("bks")]
    print(f"🎯 {len(mapped_concepts)} BK-mapped concepts (回测限制)", file=sys.stderr)
    bk_prefetched = btv3.prefetch_all_bks(cmap)

    # 2. Prefetch share_float for ALL concept members (一次性, 永久 cache)
    all_ts = sorted({ts for c, ms in CONCEPTS.items() for ts, _ in ms})
    print(f"📥 prefetch share_float for {len(all_ts)} concept members...", file=sys.stderr)
    sf_rows = nsu.fetch_share_float_by_ts(all_ts, verbose=True)
    print(f"  loaded {len(sf_rows)} rows", file=sys.stderr)
    by_sd = nsu._index_by_stock_date(sf_rows)

    # 3. Prefetch repurchase (phase1) 用于增量对比
    end = args.end_date
    start_dt = datetime.strptime(end, "%Y%m%d") - timedelta(days=args.prefetch_days)
    start = start_dt.strftime("%Y%m%d")
    print(f"📥 loading phase1 repurchase cache {start} ~ {end}...", file=sys.stderr)
    repur = nsp1.fetch_repurchase_history(start, end)
    print(f"  repur n={len(repur)}", file=sys.stderr)

    # 4. 构建 panel
    eval_start_dt = datetime.strptime(start, "%Y%m%d") + timedelta(days=60)
    eval_end_dt = datetime.strptime(end, "%Y%m%d") - timedelta(days=20)
    eval_start = eval_start_dt.strftime("%Y%m%d")
    eval_end = eval_end_dt.strftime("%Y%m%d")
    print(f"📊 eval window: {eval_start} ~ {eval_end}", file=sys.stderr)

    panel = []
    for c in mapped_concepts:
        members = stocks_of(c) or []
        if not members: continue
        bks = cmap[c].get("bks") or []
        bk_codes = [b["code"] for b in bks]
        bk_date_sets = [set(r["date"] for r in bk_prefetched[code]) for code in bk_codes
                        if bk_prefetched.get(code)]
        if not bk_date_sets: continue
        c_dates = sorted(set.intersection(*bk_date_sets))
        c_eligible = [d for d in c_dates if eval_start <= d <= eval_end]

        for d in c_eligible:
            # z_unlock
            z_un, _ = nsu.z_unlock_60d(members, d, by_sd)

            # phase1 z_repur (诊断用对比 + 增量)
            sigs1 = nsp1.aggregate_concept_signals(
                members, [], [], repur, d, win_short=5, win_long=30, hist_days=60)
            z_repur = sigs1.get("repur_z")

            # fund_score (v3.2)
            fund_res = btv3.score_concept_at(c, cmap, bk_prefetched, d,
                                             strategy="v3_ob_hard05_hi_only")
            if fund_res is None: continue
            fund_score, _ = fund_res

            r5 = btv3.fwd_return(c, cmap, bk_prefetched, d, 5)
            r20 = btv3.fwd_return(c, cmap, bk_prefetched, d, 20)

            panel.append({
                "date": d, "concept": c,
                "z_unlock": z_un,
                "z_repur": z_repur,
                "fund_score": fund_score,
                "fwd_5d": r5, "fwd_20d": r20,
            })

    print(f"\n📊 panel n={len(panel)}", file=sys.stderr)

    # 5. 单变量 IC (全 panel + signal-active 子集)
    print("\n" + "=" * 80)
    print(f"📊 z_unlock 单变量 IC (n_total={len(panel)})")
    print("=" * 80)

    # 注意: z_unlock 是反指 → 相关方向 +z (高压) 应该对应 -fwd_return.
    #       Spearman 相关取负即可解释为"取 -z 后的 IC".
    for h in ("fwd_5d", "fwd_20d"):
        # (a) full panel — z_unlock=0 全包含
        full = [(r["z_unlock"], r[h]) for r in panel
                if r["z_unlock"] is not None and r[h] is not None]
        ic_full = spearman([t[0] for t in full], [t[1] for t in full])
        # (b) signal-active 子集 (|z| > 0.5, 真有信号)
        active = [(r["z_unlock"], r[h]) for r in panel
                  if r["z_unlock"] is not None and abs(r["z_unlock"]) > 0.5
                  and r[h] is not None]
        ic_active = spearman([t[0] for t in active], [t[1] for t in active])
        # (c) high-pressure 子集 (z > 1.0, 信号最硬)
        high = [(r["z_unlock"], r[h]) for r in panel
                if r["z_unlock"] is not None and r["z_unlock"] > 1.0
                and r[h] is not None]
        ic_high = spearman([t[0] for t in high], [t[1] for t in high])

        print(f"\n  {h}:")
        ic_str_full = f"{ic_full:+.3f}" if ic_full is not None else "n/a"
        ic_str_act = f"{ic_active:+.3f}" if ic_active is not None else "n/a"
        ic_str_hi = f"{ic_high:+.3f}" if ic_high is not None else "n/a"
        print(f"    full panel        n={len(full):>4}  IC={ic_str_full}  "
              f"(取负 → {(-ic_full if ic_full else 0):+.3f})")
        print(f"    |z|>0.5 active    n={len(active):>4}  IC={ic_str_act}  "
              f"(取负 → {(-ic_active if ic_active else 0):+.3f})")
        print(f"    z>1.0 high-press  n={len(high):>4}  IC={ic_str_hi}  "
              f"(取负 → {(-ic_high if ic_high else 0):+.3f})")

        # 高压组的实际 fwd 表现
        if high and len(high) >= 5:
            high_fwds = [t[1] for t in high]
            other_fwds = [r[h] for r in panel
                          if r["z_unlock"] is not None and r["z_unlock"] <= 1.0
                          and r[h] is not None]
            mu_h = statistics.mean(high_fwds)
            mu_o = statistics.mean(other_fwds) if other_fwds else 0
            print(f"    → high-press {h} mean = {mu_h:+.2f}%  vs others = {mu_o:+.2f}%  "
                  f"gap = {mu_h - mu_o:+.2f}pp")

    # 6. 月度稳定性
    print(f"\n📊 z_unlock 月度 IC 稳定性 (fwd_20d)")
    by_m = defaultdict(list)
    for r in panel:
        if r["z_unlock"] is not None and r["fwd_20d"] is not None:
            by_m[r["date"][:6]].append((r["z_unlock"], r["fwd_20d"]))
    m_ics = []
    n_inv = 0
    for ym in sorted(by_m):
        lst = by_m[ym]
        if len(lst) < 20:
            print(f"  {ym}: n={len(lst)} 不足")
            continue
        ic = spearman([t[0] for t in lst], [t[1] for t in lst])
        if ic is not None:
            m_ics.append(ic)
            if ic > 0:  # 反指方向: 正 IC = 反指失效 (期望应为负 IC)
                n_inv += 1
        ic_str = f"{ic:+.3f}" if ic is not None else "n/a"
        ic_neg = f"{-ic:+.3f}" if ic is not None else "n/a"
        n_active = sum(1 for v, _ in lst if abs(v) > 0.5)
        print(f"  {ym}: n={len(lst):>3} (active {n_active})  IC={ic_str}  -IC={ic_neg}")
    if m_ics:
        print(f"  IC mean={statistics.mean(m_ics):+.3f}  -IC mean={-statistics.mean(m_ics):+.3f}  "
              f"std={statistics.stdev(m_ics) if len(m_ics)>1 else 0:.3f}  "
              f"反指失效月={n_inv}/{len(m_ics)}")

    # 7. 加入 fund_score 增量 IC
    print(f"\n📊 fund vs fund + (-z_unlock) 增量 IC")
    for h in ("fwd_5d", "fwd_20d"):
        # fund alone
        clean = [(r["fund_score"], r["z_unlock"], r[h]) for r in panel
                 if r["z_unlock"] is not None and r[h] is not None]
        if len(clean) < 30:
            print(f"  {h}: n={len(clean)} 不足")
            continue
        ic_fund = spearman([t[0] for t in clean], [t[2] for t in clean])
        # fund + -z_unlock_clip2 * 1.0 (即 phase2 推荐打分公式)
        ic_comb_x = []
        ic_comb_y = []
        for fund, z, fwd in clean:
            zc = max(-2, min(2, z))
            comb = fund + (-zc * 1.0)  # +pts 越高 越利好
            ic_comb_x.append(comb)
            ic_comb_y.append(fwd)
        ic_comb = spearman(ic_comb_x, ic_comb_y)
        delta = (ic_comb - ic_fund) / abs(ic_fund) * 100 if ic_fund else 0
        print(f"  {h}: fund={ic_fund:+.3f}  fund+(-z_unlock)={ic_comb:+.3f}  Δ={delta:+.1f}%")

    # 8. 加入 z_repur (Phase1 baseline) 增量 IC
    print(f"\n📊 fund+z_repur (Phase1) vs fund+z_repur+(-z_unlock) 增量 IC")
    for h in ("fwd_5d", "fwd_20d"):
        clean = [(r["fund_score"], r["z_repur"], r["z_unlock"], r[h]) for r in panel
                 if r["z_repur"] is not None and r["z_unlock"] is not None
                 and r[h] is not None]
        if len(clean) < 30:
            print(f"  {h}: n={len(clean)} 不足")
            continue
        # baseline: fund + 1.5*clip(z_repur)
        x_base = []
        x_full = []
        ys = []
        for fund, zr, zu, fwd in clean:
            zr_c = max(-2, min(2, zr))
            zu_c = max(-2, min(2, zu))
            x_base.append(fund + 1.5 * zr_c)
            x_full.append(fund + 1.5 * zr_c + (-zu_c * 1.0))
            ys.append(fwd)
        ic_base = spearman(x_base, ys)
        ic_full = spearman(x_full, ys)
        delta = (ic_full - ic_base) / abs(ic_base) * 100 if ic_base else 0
        print(f"  {h}: fund+repur={ic_base:+.3f}  +unlock={ic_full:+.3f}  Δ={delta:+.1f}%")

    # 9. 验收
    print("\n" + "=" * 80)
    print("✅ 验收门槛 (Phase 2 设计 docs/news_score_phase2_design.md)")
    print("=" * 80)


if __name__ == "__main__":
    main()
