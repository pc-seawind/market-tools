"""backtest_news_phase2_block.py — 验证 Phase 2 大宗交易折溢价 (z_block).

复用 phase1/phase2_unlock 的 panel 框架. 同 11 BK-mapped concept × eval window.

验收 (设计文档):
  - 单变量 IC_20d > +0.05 (z_block 同向: +z 利好)
  - 增量 IC vs fund 单 ≥ +5%
  - 月度反指 ≤ 1/4

注意 sparsity: 即使 20d 窗口, concept-level z 也可能稀疏. 同时报告 active 子集.
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
import news_score_phase2_block as nsb  # type: ignore
import backtest_sector_v3 as btv3  # type: ignore
from concepts_data import stocks_of, CONCEPTS  # type: ignore


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
    ap.add_argument("--prefetch-days", type=int, default=240)
    ap.add_argument("--window-days", type=int, default=20)
    args = ap.parse_args()

    cmap = bf.load_concept_bk_map()
    mapped_concepts = [c for c, v in cmap.items() if v.get("bks")]
    print(f"🎯 {len(mapped_concepts)} BK-mapped concepts", file=sys.stderr)
    bk_prefetched = btv3.prefetch_all_bks(cmap)

    # Prefetch block_trade for all members + close prices
    all_ts = sorted({ts for c, ms in CONCEPTS.items() for ts, _ in ms})
    print(f"📥 prefetch block_trade for {len(all_ts)} concept members...", file=sys.stderr)
    rows = nsb.fetch_block_trade_by_ts(all_ts, verbose=True)
    pairs = [(r.get("ts_code"), r.get("trade_date")) for r in rows
             if r.get("ts_code") and r.get("trade_date")]
    close_map = nsb.fetch_close_prices(pairs)
    by_ts = nsb._build_premium_index(rows, close_map)
    n_with_prem = sum(len(v) for v in by_ts.values())
    print(f"  premium index: {n_with_prem} trades, "
          f"{len(by_ts)} stocks with valid data", file=sys.stderr)

    # Build panel
    end = args.end_date
    start_dt = datetime.strptime(end, "%Y%m%d") - timedelta(days=args.prefetch_days)
    eval_start_dt = start_dt + timedelta(days=60)
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
            z, diag_z = nsb.z_block_60d(members, d, by_ts,
                                         hist_days=60, window_days=args.window_days)
            fund_res = btv3.score_concept_at(c, cmap, bk_prefetched, d,
                                             strategy="v3_ob_hard05_hi_only")
            if fund_res is None: continue
            fund_score, _ = fund_res
            r5 = btv3.fwd_return(c, cmap, bk_prefetched, d, 5)
            r20 = btv3.fwd_return(c, cmap, bk_prefetched, d, 20)
            cur_prem = diag_z.get("cur") if z is not None else None
            n_trades = diag_z.get("n_trades", 0)
            panel.append({
                "date": d, "concept": c,
                "z_block": z, "cur_prem": cur_prem, "n_trades": n_trades,
                "fund_score": fund_score, "fwd_5d": r5, "fwd_20d": r20,
            })

    print(f"\n📊 panel n={len(panel)}", file=sys.stderr)
    n_with_z = sum(1 for r in panel if r["z_block"] is not None)
    print(f"  with z_block: {n_with_z}", file=sys.stderr)

    # 1. 单变量 IC
    print("\n" + "=" * 80)
    print(f"📊 z_block 单变量 IC (n_total={len(panel)}, w/ z = {n_with_z})")
    print("=" * 80)

    for h in ("fwd_5d", "fwd_20d"):
        full = [(r["z_block"], r[h]) for r in panel
                if r["z_block"] is not None and r[h] is not None]
        ic_full = spearman([t[0] for t in full], [t[1] for t in full])
        # active subset (|z| > 0.5)
        active = [(r["z_block"], r[h]) for r in panel
                  if r["z_block"] is not None and abs(r["z_block"]) > 0.5
                  and r[h] is not None]
        ic_active = spearman([t[0] for t in active], [t[1] for t in active])
        # extreme positive (z > 1.0, premium-heavy = bullish)
        pos = [(r["z_block"], r[h]) for r in panel
               if r["z_block"] is not None and r["z_block"] > 1.0
               and r[h] is not None]
        # extreme negative (z < -1.0, deep-discount = bearish)
        neg = [(r["z_block"], r[h]) for r in panel
               if r["z_block"] is not None and r["z_block"] < -1.0
               and r[h] is not None]

        ic_str_full = f"{ic_full:+.3f}" if ic_full is not None else "n/a"
        ic_str_act = f"{ic_active:+.3f}" if ic_active is not None else "n/a"

        print(f"\n  {h}:")
        print(f"    full panel        n={len(full):>4}  IC={ic_str_full}")
        print(f"    |z|>0.5 active    n={len(active):>4}  IC={ic_str_act}")
        if pos and neg:
            mu_pos = statistics.mean([t[1] for t in pos])
            mu_neg = statistics.mean([t[1] for t in neg])
            other = [r[h] for r in panel
                     if r["z_block"] is not None and abs(r["z_block"]) <= 1.0
                     and r[h] is not None]
            mu_o = statistics.mean(other) if other else 0
            print(f"    z > +1.0 (溢价)    n={len(pos):>4}  mean={mu_pos:+.2f}%  "
                  f"vs others {mu_o:+.2f}% gap={mu_pos-mu_o:+.2f}pp")
            print(f"    z < -1.0 (折价)    n={len(neg):>4}  mean={mu_neg:+.2f}%  "
                  f"vs others {mu_o:+.2f}% gap={mu_neg-mu_o:+.2f}pp")

    # 2. 月度稳定性
    print(f"\n📊 z_block 月度 IC 稳定性 (fwd_20d)")
    by_m = defaultdict(list)
    for r in panel:
        if r["z_block"] is not None and r["fwd_20d"] is not None:
            by_m[r["date"][:6]].append((r["z_block"], r["fwd_20d"]))
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
            if ic < 0:  # 同向: 正期望. 负 IC = 反指失效
                n_inv += 1
        ic_str = f"{ic:+.3f}" if ic is not None else "n/a"
        print(f"  {ym}: n={len(lst):>3}  IC={ic_str}")
    if m_ics:
        print(f"  IC mean={statistics.mean(m_ics):+.3f}  std="
              f"{statistics.stdev(m_ics) if len(m_ics)>1 else 0:.3f}  "
              f"反指失效月={n_inv}/{len(m_ics)}")

    # 3. 加入 fund_score 增量 IC
    print(f"\n📊 fund vs fund + z_block_clip2 增量 IC")
    for h in ("fwd_5d", "fwd_20d"):
        clean = [(r["fund_score"], r["z_block"], r[h]) for r in panel
                 if r["z_block"] is not None and r[h] is not None]
        if len(clean) < 30:
            print(f"  {h}: n={len(clean)} 不足")
            continue
        ic_fund = spearman([t[0] for t in clean], [t[2] for t in clean])
        x_comb = []
        ys = []
        for fund, z, fwd in clean:
            zc = max(-2, min(2, z))
            x_comb.append(fund + zc * 1.0)
            ys.append(fwd)
        ic_comb = spearman(x_comb, ys)
        delta = (ic_comb - ic_fund) / abs(ic_fund) * 100 if ic_fund else 0
        print(f"  {h}: fund={ic_fund:+.3f}  fund+z_block={ic_comb:+.3f}  Δ={delta:+.1f}%")

    # 4. 验收
    print("\n" + "=" * 80)
    print("✅ 验收门槛 (Phase 2 设计 docs/news_score_phase2_design.md)")
    print(f"  - 单变量 IC_20d > +0.05: ", end="")
    full20 = [(r["z_block"], r["fwd_20d"]) for r in panel
              if r["z_block"] is not None and r["fwd_20d"] is not None]
    ic_full20 = spearman([t[0] for t in full20], [t[1] for t in full20]) if full20 else None
    if ic_full20 is not None:
        print(f"{ic_full20:+.3f}  {'✅' if ic_full20 > 0.05 else '❌'}")


if __name__ == "__main__":
    main()
