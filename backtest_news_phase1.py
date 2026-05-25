"""backtest_news_phase1.py — 验证 news_score Phase 1 信号的预测力.

策略:
  1. 用 backtest_sector_v3 的 BK 历史 + 11 mapped concept + fwd return panel
  2. 对每个 (date, concept), 用 news_score_phase1 算 z_north/z_inst/z_repur
  3. 衡量:
     a) 单变量 IC: z_X vs fwd_5d, fwd_20d (Spearman)
     b) 多变量线性回归: 最优权重组合
     c) 加入 fund_flow 后总分 IC 是否提升

验收门槛 (主线指示):
  - 至少 2 个月有效样本 (n ≥ 11 concept × 40 day = 440 obs)
  - news_score 单变量 IC_20d > +0.10
  - 加入 sector_score 后总分 IC 提升 ≥ 5%

输出:
  - 终端: 单变量 IC + 多变量回归权重 + 综合分 IC
  - 决定: 接入 / 降级 / 重设计
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
import news_score_phase1 as ns  # type: ignore
import backtest_sector_v3 as btv3  # type: ignore
from concepts_data import stocks_of  # type: ignore


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


def linreg_3var(panel: list[dict], y_key: str) -> dict:
    """OLS y = b0 + b1*north + b2*inst + b3*repur"""
    rows = [(r["z_north"], r["z_inst"], r["z_repur"], r[y_key]) for r in panel
            if r["z_north"] is not None and r["z_inst"] is not None
            and r["z_repur"] is not None and r[y_key] is not None]
    if len(rows) < 30: return {"n": len(rows)}

    n = len(rows)
    # 构造 X (n × 4), y (n)
    # 用 numpy 简化
    try:
        import numpy as np
        X = np.array([[1, r[0], r[1], r[2]] for r in rows], dtype=float)
        y = np.array([r[3] for r in rows], dtype=float)
        # OLS
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        # R^2
        y_hat = X @ beta
        ss_tot = ((y - y.mean())**2).sum()
        ss_res = ((y - y_hat)**2).sum()
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        # 各变量 ic (单独)
        return {
            "n": n,
            "b0": float(beta[0]),
            "b_north": float(beta[1]),
            "b_inst": float(beta[2]),
            "b_repur": float(beta[3]),
            "r2": float(r2),
        }
    except ImportError:
        return {"n": n, "error": "numpy missing"}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--end-date", default="20260522")
    ap.add_argument("--prefetch-days", type=int, default=180)
    args = ap.parse_args()

    # 1. Load BK panel (复用 v3 backtest 的 prefetch + score 框架)
    cmap = bf.load_concept_bk_map()
    mapped_concepts = [c for c, v in cmap.items() if v.get("bks")]
    print(f"🎯 {len(mapped_concepts)} mapped concepts", file=sys.stderr)
    bk_prefetched = btv3.prefetch_all_bks(cmap)

    # 2. 拉 news data (从 cache)
    end = args.end_date
    start_dt = datetime.strptime(end, "%Y%m%d") - timedelta(days=args.prefetch_days)
    start = start_dt.strftime("%Y%m%d")
    print(f"📥 loading news cache {start} ~ {end}...", file=sys.stderr)
    hsgt = ns.fetch_hsgt_top10_history(start, end)
    inst = ns.fetch_top_inst_history(start, end)
    repur = ns.fetch_repurchase_history(start, end)
    print(f"  hsgt n={len(hsgt)}  top_inst n={len(inst)}  repur n={len(repur)}",
          file=sys.stderr)

    if len(inst) < 1000:
        print(f"❌ top_inst 数据不足 (n={len(inst)}), 不能继续", file=sys.stderr)
        return

    # 3. 对每个 mapped concept × eligible date, 算 news_score + 已有的 fund_flow + fwd
    panel = []
    fund_panel = []
    eval_start_dt = datetime.strptime(start, "%Y%m%d") + timedelta(days=60)  # 留 60d hist
    eval_end_dt = datetime.strptime(end, "%Y%m%d") - timedelta(days=20)  # 留 20d fwd
    eval_start = eval_start_dt.strftime("%Y%m%d")
    eval_end = eval_end_dt.strftime("%Y%m%d")
    print(f"📊 eval window: {eval_start} ~ {eval_end}", file=sys.stderr)

    for c in mapped_concepts:
        members = stocks_of(c) or []
        if not members:
            print(f"  ⚠️ {c}: no members", file=sys.stderr)
            continue
        # 取 BK eligible dates 且在 eval window 内
        bks = cmap[c].get("bks") or []
        bk_codes = [b["code"] for b in bks]
        bk_date_sets = [set(r["date"] for r in bk_prefetched[code]) for code in bk_codes
                        if bk_prefetched.get(code)]
        if not bk_date_sets: continue
        c_dates = sorted(set.intersection(*bk_date_sets))
        c_eligible = [d for d in c_dates if eval_start <= d <= eval_end]

        # 每周采样 1 次降低自相关 (可选, 暂全采)
        for d in c_eligible:
            # news signal
            sigs = ns.aggregate_concept_signals(members, hsgt, inst, repur, d,
                                                win_short=5, win_long=30, hist_days=60)
            # fund flow signal (v3.2)
            fund_res = btv3.score_concept_at(c, cmap, bk_prefetched, d,
                                             strategy="v3_ob_hard05_hi_only")
            if fund_res is None: continue
            fund_score, _ = fund_res

            r5 = btv3.fwd_return(c, cmap, bk_prefetched, d, 5)
            r20 = btv3.fwd_return(c, cmap, bk_prefetched, d, 20)

            panel.append({
                "date": d, "concept": c,
                "z_north": sigs.get("north_z"),
                "z_inst": sigs.get("inst_z"),
                "z_repur": sigs.get("repur_z"),
                "fund_score": fund_score,
                "fwd_5d": r5, "fwd_20d": r20,
            })

    print(f"\n📊 panel n={len(panel)}", file=sys.stderr)

    # 4. 单变量 IC
    print("\n" + "=" * 80)
    print(f"📊 News Phase 1 单变量 IC (n={len(panel)})")
    print("=" * 80)
    print(f"  {'signal':<10} {'fwd_5d_IC':>12} {'fwd_20d_IC':>12} {'n':>6}")
    print("  " + "-" * 46)
    for sig_key in ("z_north", "z_inst", "z_repur"):
        for h in ("fwd_5d", "fwd_20d"):
            clean = [(r[sig_key], r[h]) for r in panel
                     if r[sig_key] is not None and r[h] is not None]
            if len(clean) < 30:
                ic_str = "n/a"
            else:
                ic = spearman([t[0] for t in clean], [t[1] for t in clean])
                ic_str = f"{ic:+.3f}" if ic else "n/a"
            if h == "fwd_5d":
                ic5 = ic_str; n5 = len(clean)
            else:
                ic20 = ic_str
        print(f"  {sig_key:<10} {ic5:>12} {ic20:>12} {n5:>6}")

    # 5. 多变量 OLS
    print(f"\n📊 OLS 回归 (找最优权重)")
    for h in ("fwd_5d", "fwd_20d"):
        res = linreg_3var(panel, h)
        if "error" in res or res.get("n", 0) < 30:
            print(f"  {h}: n={res.get('n',0)} -- 不足/error: {res.get('error','')}")
            continue
        print(f"  {h}: n={res['n']:>4}  R²={res['r2']:+.4f}  "
              f"β: north={res['b_north']:+.3f}  inst={res['b_inst']:+.3f}  "
              f"repur={res['b_repur']:+.3f}  intercept={res['b0']:+.3f}")

    # 6. 综合 news_score (等权 z 累加 + base 7.5) IC
    print(f"\n📊 综合 news_score (等权) IC")
    for r in panel:
        if all(r[k] is not None for k in ("z_north", "z_inst", "z_repur")):
            zn = max(-2, min(2, r["z_north"]))
            zi = max(-2, min(2, r["z_inst"]))
            zp = max(-2, min(2, r["z_repur"]))
            r["news_score"] = 7.5 + 1.5*zn + 1.5*zi + 1.5*zp
        else:
            r["news_score"] = None

    for h in ("fwd_5d", "fwd_20d"):
        clean = [(r["news_score"], r[h]) for r in panel
                 if r["news_score"] is not None and r[h] is not None]
        if len(clean) < 30: continue
        ic = spearman([t[0] for t in clean], [t[1] for t in clean])
        print(f"  news_score etqu vs {h}: IC={ic:+.3f}  n={len(clean)}")

    # 7. fund_score (v3.2) 单独 + fund_score + news 加和的 IC 比较
    print(f"\n📊 fund vs fund+news 增量 IC")
    for h in ("fwd_5d", "fwd_20d"):
        # fund alone
        fund_clean = [(r["fund_score"], r[h]) for r in panel if r[h] is not None]
        ic_fund = spearman([t[0] for t in fund_clean], [t[1] for t in fund_clean])
        # fund + news (etqu w)
        comb_clean = [(r["fund_score"] + (r["news_score"] or 7.5), r[h]) for r in panel
                      if r[h] is not None]
        ic_comb = spearman([t[0] for t in comb_clean], [t[1] for t in comb_clean])
        delta = (ic_comb - ic_fund) / ic_fund * 100 if ic_fund else 0
        print(f"  {h}: fund={ic_fund:+.3f}  fund+news={ic_comb:+.3f}  Δ={delta:+.1f}%")

    # 7b. 纯 repur 信号 (z_north/z_inst 已证为反指/无信号)
    print(f"\n📊 纯 z_repur 信号 (Phase1_v1)")
    for r in panel:
        if r["z_repur"] is not None:
            zp = max(-2, min(2, r["z_repur"]))
            r["news_score_v1"] = 7.5 + 3.0 * zp  # ±6 swing
        else:
            r["news_score_v1"] = None

    for h in ("fwd_5d", "fwd_20d"):
        clean = [(r["news_score_v1"], r[h]) for r in panel
                 if r["news_score_v1"] is not None and r[h] is not None]
        if not clean: continue
        ic = spearman([t[0] for t in clean], [t[1] for t in clean])
        # combined with fund
        comb = [(r["fund_score"] + r["news_score_v1"], r[h]) for r in panel
                if r["news_score_v1"] is not None and r[h] is not None]
        ic_comb = spearman([t[0] for t in comb], [t[1] for t in comb])
        # fund alone (same n)
        fund_clean = [(r["fund_score"], r[h]) for r in panel
                      if r["news_score_v1"] is not None and r[h] is not None]
        ic_fund_aligned = spearman([t[0] for t in fund_clean], [t[1] for t in fund_clean])
        delta = (ic_comb - ic_fund_aligned) / ic_fund_aligned * 100 if ic_fund_aligned else 0
        print(f"  {h}: news_v1={ic:+.3f}  fund_aligned={ic_fund_aligned:+.3f}  "
              f"fund+news_v1={ic_comb:+.3f}  Δ={delta:+.1f}%")

    # 7c. 月度 IC 稳定性 (z_repur)
    print(f"\n📊 z_repur 月度 IC 稳定性")
    by_m = defaultdict(list)
    for r in panel:
        if r["z_repur"] is not None and r["fwd_20d"] is not None:
            by_m[r["date"][:6]].append((r["z_repur"], r["fwd_20d"]))
    m_ics = []
    for ym in sorted(by_m):
        lst = by_m[ym]
        if len(lst) < 20:
            print(f"  {ym}: n={len(lst)} 不足")
            continue
        ic = spearman([t[0] for t in lst], [t[1] for t in lst])
        if ic is not None: m_ics.append(ic)
        print(f"  {ym}: n={len(lst):>3}  IC_20d={ic:+.3f}" if ic else f"  {ym}: n/a")
    if len(m_ics) >= 2:
        print(f"  IC mean={statistics.mean(m_ics):+.3f}  std={statistics.stdev(m_ics):.3f}")

    # 8. 验收
    print("\n" + "=" * 80)
    print("✅ 验收门槛检查")
    print("=" * 80)


if __name__ == "__main__":
    main()
