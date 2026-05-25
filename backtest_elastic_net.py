"""backtest_elastic_net.py — 用 elastic-net / ridge / OLS 找 sector_score 最优权重.

背景:
  Task A/B/C 已确认:
    - ETF share fund_flow      r = -0.07  ❌ 反向
    - main_5d/20d (大+超大单)   r = +0.18 / +0.21  ✓ 正向
    - sm_5d/20d   (散户)        r = -0.21 / -0.28  ✓ 反指最强
    - rate_20d    (净流入率%)    r = +0.10
    - main_score  (step_20)     r = +0.008  ❌ 阶梯把信号搞没
    - narrative event D14-D28   r = +0.36  (但 n=12 太小, 只能定性)

  本脚本用 BK moneyflow 大样本 (n~2600) 跑多元回归, 得到每个特征对 fwd_20d_rel
  的标准化系数 — 那就是它们各自该在 sector_score 里占多少权重.

设计:
  panel rows = (BK code, date) — 7 BKs × ~600 days = ~4060 rows post-warmup
  features (X):
    main_5d, main_20d         — 大+超大单累加
    sm_5d, sm_20d             — 散户净额
    rate_5d, rate_20d         — net_amount_rate% 平均
  target (y):
    fwd_20d_rel               — T+20 BK 指数收益 - 当日全 BK 平均

  三个模型并行跑, 都 z-score 标准化:
    1. OLS (无正则) — baseline + p-values
    2. RidgeCV     — 处理共线性 (main_5d/main_20d 高度相关)
    3. ElasticNetCV — L1+L2, 自动剔除冗余特征

  报告:
    - 系数 (标准化, 单位: σ_x → σ_y)
    - 95% CI (OLS)
    - VIF 共线性诊断
    - per-BK 残差 (检查模型对 ai_chip vs cpo 是否同样有效)
    - 把系数 normalize 到 0-100 给 sector_score 当权重建议

CLI:
  python3 backtest_elastic_net.py
  python3 backtest_elastic_net.py --start 20240101 --end 20260331
  python3 backtest_elastic_net.py --json > regression_out.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import ElasticNetCV, RidgeCV, LinearRegression
from sklearn.preprocessing import StandardScaler

# 复用 backtest_moneyflow 的 fetch + feature builder
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_moneyflow import AI_BK_MAP, fetch_bk_moneyflow, compute_bk_signal


# ─── 数据组装 ──────────────────────────────────────────────────────────────

def build_panel(start_date: str, end_date: str) -> pd.DataFrame:
    """Walk all BKs, compute features, fill rel returns. Returns DataFrame."""
    print(f"📥 fetching {len(AI_BK_MAP)} BK moneyflow ...", file=sys.stderr)
    rows = []
    for m in AI_BK_MAP:
        data = fetch_bk_moneyflow(m["bk"])
        if not data:
            continue
        print(f"  {m['bk']:<14} {m['name']:<14} {len(data)} rows ({data[0]['date']} ~ {data[-1]['date']})",
              file=sys.stderr)
        for idx in range(len(data)):
            d = data[idx]["date"]
            if d < start_date or d > end_date:
                continue
            sig = compute_bk_signal(data, idx, m)
            if sig is None:
                continue
            rows.append(asdict(sig))

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # cross-section mean (per date) of fwd_*d to compute relative
    for k in ("fwd_5d", "fwd_20d", "fwd_40d"):
        df[f"{k}_xs_mean"] = df.groupby("date")[k].transform("mean")
        df[f"{k}_rel"] = df[k] - df[f"{k}_xs_mean"]
    return df


# ─── 回归 ──────────────────────────────────────────────────────────────────

FEATURES = ["main_5d", "main_20d", "sm_5d", "sm_20d", "rate_5d", "rate_20d"]
TARGET = "fwd_20d_rel"


def run_regressions(df: pd.DataFrame) -> dict[str, Any]:
    """Three models on standardized features. Returns coefficients + metrics."""
    sub = df.dropna(subset=FEATURES + [TARGET]).copy()
    if len(sub) < 100:
        return {"error": f"insufficient samples: {len(sub)}"}

    X_raw = sub[FEATURES].values.astype(float)
    y = sub[TARGET].values.astype(float)

    # standardize X (z-score)
    sc = StandardScaler()
    X = sc.fit_transform(X_raw)
    # y also z-score? No — keep y in raw % so coefficients are interpretable as "1σ feature → β % excess"
    # but actually for cross-feature comparison, standardizing y too gives unitless betas.
    sc_y = StandardScaler(with_mean=True, with_std=True)
    y_std = sc_y.fit_transform(y.reshape(-1, 1)).ravel()

    # Pearson r per feature (univariate)
    univ = {}
    for i, f in enumerate(FEATURES):
        r = np.corrcoef(X[:, i], y)[0, 1]
        univ[f] = round(float(r), 4)

    # OLS
    ols = LinearRegression(fit_intercept=True)
    ols.fit(X, y)
    y_pred = ols.predict(X)
    rss = float(np.sum((y - y_pred) ** 2))
    tss = float(np.sum((y - y.mean()) ** 2))
    ols_r2 = 1 - rss / tss if tss > 0 else None
    # standard errors
    n, p = X.shape
    sigma2 = rss / max(1, n - p - 1)
    XtX_inv = np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.maximum(np.diag(XtX_inv) * sigma2, 0))
    t_stats = ols.coef_ / np.where(se > 0, se, np.nan)
    # crude two-sided p-value from normal approx (t-dist asymptotic)
    from math import erf, sqrt
    def _p(tv: float) -> float:
        if math.isnan(tv): return float("nan")
        return 2 * (1 - 0.5 * (1 + erf(abs(tv) / sqrt(2))))

    # OLS on standardized y for unitless beta
    ols_std = LinearRegression(fit_intercept=True)
    ols_std.fit(X, y_std)

    # Ridge (CV alpha)
    ridge = RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0, 100.0], cv=5)
    ridge.fit(X, y)

    # ElasticNet (CV alpha, l1_ratio grid)
    enet = ElasticNetCV(l1_ratio=[0.1, 0.3, 0.5, 0.7, 0.9, 1.0],
                        alphas=None, cv=5, n_alphas=20, max_iter=5000)
    enet.fit(X, y)

    # VIF
    vif = []
    for i, f in enumerate(FEATURES):
        # regress feature i on the rest
        other = [j for j in range(len(FEATURES)) if j != i]
        if not other:
            vif.append(1.0); continue
        lr = LinearRegression()
        lr.fit(X[:, other], X[:, i])
        r2_i = lr.score(X[:, other], X[:, i])
        vif.append(1 / max(1e-8, 1 - r2_i))

    return {
        "n": int(n),
        "y_std_dev": float(y.std()),
        "univariate_r": univ,
        "ols_raw_y": {
            "coef": {f: round(float(ols.coef_[i]), 6) for i, f in enumerate(FEATURES)},
            "se":   {f: round(float(se[i]), 6) for i, f in enumerate(FEATURES)},
            "t":    {f: round(float(t_stats[i]), 3) for i, f in enumerate(FEATURES)},
            "p":    {f: round(float(_p(t_stats[i])), 4) for i, f in enumerate(FEATURES)},
            "intercept": round(float(ols.intercept_), 4),
            "r2": round(float(ols_r2), 4) if ols_r2 is not None else None,
        },
        "ols_std_y": {
            "beta": {f: round(float(ols_std.coef_[i]), 4) for i, f in enumerate(FEATURES)},
        },
        "ridge_cv": {
            "alpha": float(ridge.alpha_),
            "coef": {f: round(float(ridge.coef_[i]), 6) for i, f in enumerate(FEATURES)},
            "score_r2": round(float(ridge.score(X, y)), 4),
        },
        "elastic_net_cv": {
            "alpha": float(enet.alpha_),
            "l1_ratio": float(enet.l1_ratio_),
            "coef": {f: round(float(enet.coef_[i]), 6) for i, f in enumerate(FEATURES)},
            "score_r2": round(float(enet.score(X, y)), 4),
            "n_nonzero": int(np.sum(np.abs(enet.coef_) > 1e-8)),
        },
        "vif": {f: round(float(vif[i]), 2) for i, f in enumerate(FEATURES)},
    }


def per_bk_residual(df: pd.DataFrame, out: dict[str, Any]) -> dict[str, Any]:
    """Refit ElasticNet on all data, then per-BK predict + residual stats."""
    sub = df.dropna(subset=FEATURES + [TARGET]).copy()
    if len(sub) < 100 or "elastic_net_cv" not in out:
        return {}
    X = StandardScaler().fit_transform(sub[FEATURES].values.astype(float))
    y = sub[TARGET].values.astype(float)
    enet = ElasticNetCV(l1_ratio=[0.1, 0.3, 0.5, 0.7, 0.9, 1.0],
                        alphas=None, cv=5, n_alphas=20, max_iter=5000)
    enet.fit(X, y)
    sub["pred"] = enet.predict(X)
    sub["resid"] = sub[TARGET] - sub["pred"]
    bks = {}
    for bk, g in sub.groupby("bk"):
        bks[bk] = {
            "name": str(g.iloc[0]["name"]),
            "sub_domain": str(g.iloc[0]["sub_domain"]),
            "n": int(len(g)),
            "resid_mean": round(float(g["resid"].mean()), 3),
            "resid_std":  round(float(g["resid"].std()), 3),
            "resid_p10":  round(float(g["resid"].quantile(0.1)), 3),
            "resid_p90":  round(float(g["resid"].quantile(0.9)), 3),
            "pred_resid_corr": round(float(g["pred"].corr(g["resid"])), 3) if len(g) > 5 else None,
        }
    return bks


def coefs_to_score_weights(out: dict[str, Any]) -> dict[str, Any]:
    """ElasticNet 系数 → 0-100 sector_score 权重建议.

    思路:
      - 取 ElasticNet 标准化系数 (raw_y, X 已 z-score)
      - 转换: 单位是 "1σ X → β% T+20 excess return"
      - 跨特征 L1 normalize (绝对值占比) → 权重
      - 标记反向特征 (β<0) → 信号方向需要翻转
      - 合计 100 — 这就是建议给 fund_flow 子模块的内部权重

    ⚠ 不是直接整张 sector_score 的权重 (那张含 fundamental + news + tech)
       这里只是 fund_flow 部分应该怎么线性组合 6 个特征.
    """
    en_coef = out.get("elastic_net_cv", {}).get("coef", {})
    if not en_coef:
        return {}
    abs_total = sum(abs(v) for v in en_coef.values())
    if abs_total < 1e-8:
        return {f: 0 for f in FEATURES}
    weights = {}
    for f, c in en_coef.items():
        w = abs(c) / abs_total * 100
        weights[f] = {
            "weight_pct": round(w, 1),
            "direction": "+" if c > 0 else ("−" if c < 0 else "0"),
            "raw_beta": round(c, 6),
        }
    return weights


# ─── 报告 ──────────────────────────────────────────────────────────────────

def print_report(out: dict[str, Any], per_bk: dict[str, Any]):
    if "error" in out:
        print(f"❌ {out['error']}")
        return
    print("=" * 100)
    print(f"  Elastic-Net Multivariate Regression on Sector Money-flow Features")
    print("=" * 100)
    print(f"  n = {out['n']}, y_std (T+20 rel%) = {out['y_std_dev']:.3f}")
    print()

    # univariate
    print("  ━━━ 单变量 Pearson r (T+20 rel) ━━━")
    for f, r in out["univariate_r"].items():
        flag = " 📍" if abs(r) > 0.15 else ""
        print(f"    {f:<14} r = {r:+.4f}{flag}")
    print()

    # OLS
    ols = out["ols_raw_y"]
    print("  ━━━ OLS (raw y%) ━━━")
    print(f"    R² = {ols['r2']}, intercept = {ols['intercept']}")
    print(f"    {'feature':<14} {'coef':>10} {'se':>8} {'t':>7} {'p':>8}")
    for f in FEATURES:
        sig = " ***" if ols["p"][f] < 0.01 else (" *" if ols["p"][f] < 0.05 else "")
        print(f"    {f:<14} {ols['coef'][f]:>10.6f} {ols['se'][f]:>8.6f} {ols['t'][f]:>7.2f} {ols['p'][f]:>8.4f}{sig}")
    print()

    # OLS standardized y
    print("  ━━━ OLS (β on z-scored y, unitless) ━━━")
    print("    单位: 1σ feature ↑ → β σ T+20 rel excess change")
    for f in FEATURES:
        print(f"    {f:<14} β = {out['ols_std_y']['beta'][f]:+.4f}")
    print()

    # Ridge
    rd = out["ridge_cv"]
    print(f"  ━━━ Ridge (CV alpha = {rd['alpha']}) — R² = {rd['score_r2']} ━━━")
    for f in FEATURES:
        print(f"    {f:<14} coef = {rd['coef'][f]:+.6f}")
    print()

    # ElasticNet
    en = out["elastic_net_cv"]
    print(f"  ━━━ ElasticNet (CV α = {en['alpha']:.4g}, l1_ratio = {en['l1_ratio']:.2f}) — R² = {en['score_r2']} ━━━")
    print(f"    nonzero features: {en['n_nonzero']} / {len(FEATURES)}")
    for f in FEATURES:
        c = en["coef"][f]
        flag = " ZERO" if abs(c) < 1e-8 else ""
        print(f"    {f:<14} coef = {c:+.6f}{flag}")
    print()

    # VIF
    print("  ━━━ VIF (multicollinearity) — >5 = problematic ━━━")
    for f, v in out["vif"].items():
        flag = " ⚠️" if v > 5 else ""
        print(f"    {f:<14} VIF = {v:.2f}{flag}")
    print()

    # weights
    if "weight_recommendations" in out:
        print("  ━━━ → fund_flow 内部权重建议 (ElasticNet abs-coef normalize 100%) ━━━")
        for f, info in out["weight_recommendations"].items():
            d = info["direction"]
            print(f"    {f:<14} weight = {info['weight_pct']:>5.1f}%   dir = {d}   raw β = {info['raw_beta']:+.4f}")
        print()

    if per_bk:
        print("  ━━━ per-BK residual diagnostic (model fit quality) ━━━")
        for bk, info in per_bk.items():
            print(f"    {bk:<14} {info['name']:<14} n={info['n']}  resid mean={info['resid_mean']:+.2f}  std={info['resid_std']:.2f}")
        print()


# ─── main ──────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="20240101")
    p.add_argument("--end", default="20260331")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    df = build_panel(args.start, args.end)
    if df.empty:
        print("⚠️  no panel data", file=sys.stderr)
        return

    print(f"\n📊 panel size: {len(df)} rows × {len(df.columns)} cols", file=sys.stderr)
    out = run_regressions(df)
    out["weight_recommendations"] = coefs_to_score_weights(out)
    per_bk = per_bk_residual(df, out)

    if args.json:
        print(json.dumps({"regression": out, "per_bk_residual": per_bk}, ensure_ascii=False, indent=2))
    else:
        print_report(out, per_bk)


if __name__ == "__main__":
    main()
