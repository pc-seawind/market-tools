"""bk_moneyflow.py — 板块大单 / 散户 资金流信号 (sector_score v3.1 fund_flow 子模块).

替代失效的 ETF share-flow 信号 (回测 r=-0.07 反向). 数据源:
  tushare moneyflow_ind_dc — 东财行业/概念板块每日大单+超大单+散户净流入额.

回测证据 (backtest_elastic_net.py, n=2603, 7 AI BK, 2024-01 ~ 2026-03):
  ━━━ 单变量 Pearson r ↔ T+20 rel ━━━
    main_minus_sm_20d      +0.22    ← 主力相对散户 (推荐主信号)
    sm_20d                 -0.22    ← 散户反指 (镜像)
    rate_20d               +0.10    ← 净流入率% (规一化)

回测证据 (backtest_sector_v3.py --compare, n=3955 panel, 11 mapped concepts):
  strategy        IC_5d   IC_20d  Q5-Q1_5d  极端%   中段%  IC_std(月度)
  v3 (老)         +0.111  +0.220   +1.75    27.4%  26.0%   0.388
  v3.1 (软+反转)   +0.134  +0.236   +1.59     0.0%  23.2%   0.297 ⭐

v3.1 = v3 + 两步软化:
  1. clip ±1σ (而非 ±2σ): 极值压回 ±1σ, 把"非黑即白"消除
  2. |z| > 1.5σ → 反转拉回中位 30%: 极端高分衰减 (Q5<Q4 实证) +
     极端低分反弹 (Q1>Q3 实证) 都被纠正

打分逻辑 (v3.1):
  1. 拉 80d moneyflow (60d 历史 z, 20d 当前窗口)
  2. 计算 main_minus_sm_20d_z + rate_20d_z (vs 60d 历史)
  3. clip 到 [-1, +1], 加权: cm*10 + cr*3 = ±13 swing, 加 base=20
  4. 极端 |z_main|>1.5 → score = 0.7*score + 0.3*20 (反转拉回)
  5. 多 BK 时, z 取均值

  ⚠ 避免 step_20 阶梯 (复刻验证 r=+0.008 信号被桶化) — 必须连续映射.
  ⚠ 不再用 sm 反指做硬阈值 — main_minus_sm 已隐含 (镜像), 重复计算反致偏.

输出:
  fund_flow_score_v3(concept) → (score: float[0-40], note: str, diagnostics: dict)
"""
from __future__ import annotations

import csv
import json
import statistics
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"
_BK_MAP_YAML = _HERE / "concept_bk_map.yaml"

# ─── concept→BK 映射加载 (lazy + cached) ──────────────────────────────────

_concept_bk_map_cache: dict[str, dict] | None = None


def load_concept_bk_map() -> dict[str, dict]:
    global _concept_bk_map_cache
    if _concept_bk_map_cache is not None:
        return _concept_bk_map_cache
    try:
        import yaml
    except ImportError:
        sys.stderr.write("⚠️  PyYAML not installed, falling back to json parse\n")
        return {}
    if not _BK_MAP_YAML.exists():
        return {}
    with open(_BK_MAP_YAML) as f:
        doc = yaml.safe_load(f)
    _concept_bk_map_cache = doc.get("concepts", {}) or {}
    return _concept_bk_map_cache


def bks_for_concept(concept: str) -> list[dict[str, str]]:
    """Return list of {code, name, kind} for the concept, or [] if pending/missing."""
    cmap = load_concept_bk_map()
    cfg = cmap.get(concept, {})
    if not cfg or cfg.get("status") == "pending":
        return []
    return cfg.get("bks", []) or []


# ─── tushare fetch ──────────────────────────────────────────────────────────

def _ts_csv(api: str, **params) -> list[dict[str, str]]:
    args = ["python3", str(_TUSHARE), api]
    for k, v in params.items():
        if k == "fields":
            args.append(f"--fields={v}")
        else:
            args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def fetch_bk_recent(bk_code: str) -> list[dict[str, Any]]:
    """Fetch all available rows for a BK; return ASC by date."""
    rows = _ts_csv("moneyflow_ind_dc", ts_code=bk_code)
    parsed = []
    for r in rows:
        try:
            parsed.append({
                "date": r["trade_date"],
                "close": float(r["close"]),
                "elg": float(r.get("buy_elg_amount") or 0),
                "lg":  float(r.get("buy_lg_amount") or 0),
                "md":  float(r.get("buy_md_amount") or 0),
                "sm":  float(r.get("buy_sm_amount") or 0),
                "net": float(r.get("net_amount") or 0),
                "rate": float(r.get("net_amount_rate") or 0),
            })
        except (KeyError, ValueError):
            continue
    parsed.sort(key=lambda x: x["date"])
    return parsed


# ─── 信号计算 ──────────────────────────────────────────────────────────────

@dataclass
class BkFlowSignal:
    bk_code: str
    bk_name: str
    n_days_history: int
    main_5d: float
    main_20d: float
    sm_5d: float
    sm_20d: float
    main_minus_sm_5d: float
    main_minus_sm_20d: float
    rate_5d: float
    rate_20d: float
    # z-scores vs trailing 60d
    main_minus_sm_z: float | None
    rate_z: float | None
    sm_z: float | None        # 散户净流入 z (正向 = 散户狂买, 反指)
    # v3.2 末期反转 features
    nav20d: float | None      # close[-1] / close[-21] - 1, %
    pos60: float | None       # close[-1] 在最近 60d 的百分位, [0..1]


def compute_bk_signal(rows: list[dict]) -> BkFlowSignal | None:
    """从 ASC 排序的 BK rows 中算最新窗口的信号."""
    if len(rows) < 21:  # 至少要 20d 当前 + 1d
        return None
    last = rows[-1]
    win5 = rows[-5:]
    win20 = rows[-20:]
    main_5d  = sum(r["elg"] + r["lg"] for r in win5)
    main_20d = sum(r["elg"] + r["lg"] for r in win20)
    sm_5d    = sum(r["sm"] for r in win5)
    sm_20d   = sum(r["sm"] for r in win20)
    rate_5d  = sum(r["rate"] for r in win5) / 5
    rate_20d = sum(r["rate"] for r in win20) / 20

    # v3.2: nav20d 和 pos60 (末期反转检测)
    nav20d = None
    if len(rows) >= 21:
        c0 = rows[-21]["close"]; c1 = rows[-1]["close"]
        if c0 > 0:
            nav20d = (c1 - c0) / c0 * 100  # %
    pos60 = None
    if len(rows) >= 60:
        win60 = [r["close"] for r in rows[-60:]]
        cur = win60[-1]
        rank = sum(1 for c in win60 if c <= cur)
        pos60 = rank / len(win60)

    # 历史 20d 滚动序列, 用于 z-score
    # 需要 60d 历史 + 20d 窗口 = 至少 80 行
    main_minus_sm_z = rate_z = sm_z = None
    if len(rows) >= 80:
        hist_main_diff: list[float] = []
        hist_rate: list[float] = []
        hist_sm: list[float] = []
        # 倒数第 80 天到倒数第 21 天 (避免和当前 20d 重叠)
        for end in range(len(rows) - 20 - 60, len(rows) - 20):
            if end < 19:
                continue
            w = rows[end - 19: end + 1]
            md = sum(r["elg"] + r["lg"] - r["sm"] for r in w)
            rt = sum(r["rate"] for r in w) / 20
            sm = sum(r["sm"] for r in w)
            hist_main_diff.append(md)
            hist_rate.append(rt)
            hist_sm.append(sm)
        if len(hist_main_diff) >= 30:
            mu_md = statistics.mean(hist_main_diff)
            sd_md = statistics.stdev(hist_main_diff)
            cur_md = main_20d - sm_20d
            if sd_md > 0:
                main_minus_sm_z = (cur_md - mu_md) / sd_md
        if len(hist_rate) >= 30:
            mu_rt = statistics.mean(hist_rate)
            sd_rt = statistics.stdev(hist_rate)
            if sd_rt > 0:
                rate_z = (rate_20d - mu_rt) / sd_rt
        if len(hist_sm) >= 30:
            mu_sm = statistics.mean(hist_sm)
            sd_sm = statistics.stdev(hist_sm)
            if sd_sm > 0:
                sm_z = (sm_20d - mu_sm) / sd_sm

    return BkFlowSignal(
        bk_code=last.get("bk_code", ""),  # filled by caller
        bk_name=last.get("bk_name", ""),
        n_days_history=len(rows),
        main_5d=main_5d, main_20d=main_20d,
        sm_5d=sm_5d,    sm_20d=sm_20d,
        main_minus_sm_5d=main_5d - sm_5d,
        main_minus_sm_20d=main_20d - sm_20d,
        rate_5d=rate_5d, rate_20d=rate_20d,
        main_minus_sm_z=main_minus_sm_z,
        rate_z=rate_z,
        sm_z=sm_z,
        nav20d=nav20d,
        pos60=pos60,
    )


# ─── score map ──────────────────────────────────────────────────────────────

def _z_to_score(z: float | None, max_pts: float, default_mid: float | None = None) -> float:
    """Linear map: z=-2 → 0, z=0 → max/2, z=+2 → max. Clipped."""
    if z is None:
        return default_mid if default_mid is not None else max_pts * 0.5
    if z <= -2: return 0
    if z >= +2: return max_pts
    return max_pts * (z + 2) / 4


def fund_flow_score_v3(concept: str) -> tuple[float, str, dict[str, Any]]:
    """0-40 板块资金面分 (v3.2, soft + revert + 末期反转 penalty).

    设计 (基于 backtest_sector_v3.py --compare 多次迭代):
      score = 20 (base) + cm * 10 + cr * 3
        cm = clip(z(main_minus_sm_20d), -1, +1)   # 主信号 (回归 r=+0.22)
        cr = clip(z(rate_20d),         -1, +1)    # 辅信号 (净流入率)

      若 |z(main)| > 1.5σ:  极端反转拉回 30% 中段
        score' = 0.7 * score + 0.3 * 20

      [v3.2 新增] 末期反转 penalty (hi_only 版):
      若 nav20d ≥ 15% AND pos60 ≥ 0.9 AND raw_score > 20 (base):
        score'' = 0.5 * score' + 0.5 * 20  (拉回 50% 中段)
        ⚠ 只罚高分: 低位+末期是"高位回调"非"末期狂热", 不应被错误奖励
        (历史 prereq: 末期 A 状态 fwd5d=+0.01% vs 整体 +1.04%, 命中率清晰)

    回测对比 (n=3955 panel, 11 mapped concept × 360d):
      v3 (老)         IC_5d=+0.111  IC_20d=+0.220  极端%=27.4%  IC_std=0.388
      v3.1 (soft+rev) IC_5d=+0.134  IC_20d=+0.236  极端%= 0.0%  IC_std=0.297
      v3.2 (+ob_h05)  IC_5d=+0.140  IC_20d=+0.252  Q5-Q1=+6.30  IC_std=0.314 ⭐

      v3.2 vs v3.1: IC_5d +4.5%, IC_20d +6.8%, Q5-Q1_20d +8.4%, IC_std 退化 5.7%

    [D 任务实证, 弃用]: 横截面 rank 替换在我们的 case 反而 IC 暴跌:
      xs_rank_pure   IC_5d=+0.026 (-82%)  -- 11 concept 信号同源, rank 丢绝对水平信息
      xs_hybrid_30   IC_5d=+0.112 (-21%)
    所以保留绝对 z-score, 接受同源 BK 时同分必然. 上层 sector_score 总分通过基本面/
    技术面/Gate 自然打散 (经实证: 4 共用 BK1036 板块的 fund 同分 25.3, 但总分被基本面
    拉开 14 分: 61.4 / 52.1 / 51.0 / 47.0).

    Return (score, note_str, diagnostics).
    """
    bks = bks_for_concept(concept)
    if not bks:
        return 20.0, "(no BK mapping; NEUTRAL fallback — 不要据此判断板块)", {
            "bks": [], "fallback": True}

    sigs: list[BkFlowSignal] = []
    for entry in bks:
        rows = fetch_bk_recent(entry["code"])
        if not rows:
            continue
        sig = compute_bk_signal(rows)
        if sig is None:
            continue
        sig.bk_code = entry["code"]
        sig.bk_name = entry["name"]
        sigs.append(sig)

    if not sigs:
        return 20.0, "(BK fetch all failed; NEUTRAL)", {"bks": [], "fetch_failed": True}

    z_main_diff = [s.main_minus_sm_z for s in sigs if s.main_minus_sm_z is not None]
    z_rate = [s.rate_z for s in sigs if s.rate_z is not None]
    z_sm = [s.sm_z for s in sigs if s.sm_z is not None]
    avg_z_main = statistics.mean(z_main_diff) if z_main_diff else None
    avg_z_rate = statistics.mean(z_rate) if z_rate else None
    avg_z_sm = statistics.mean(z_sm) if z_sm else None

    # v3.1 软化映射: clip ±1σ → 直接 [-1, +1] (不再除 2)
    def _zclip1(z): return max(-1.0, min(1.0, z)) if z is not None else None

    base = 20.0
    coef_main = _zclip1(avg_z_main)
    coef_rate = _zclip1(avg_z_rate)
    main_pts = (coef_main * 10.0) if coef_main is not None else 0.0  # ±10
    rate_pts = (coef_rate *  3.0) if coef_rate is not None else 0.0  # ±3

    raw_score = base + main_pts + rate_pts
    revert_applied = False
    if avg_z_main is not None and abs(avg_z_main) > 1.5:
        raw_score = 0.7 * raw_score + 0.3 * base  # 极端反转拉回 30%
        revert_applied = True

    # v3.2 末期反转 penalty: nav20 ∈ [15%, 30%) AND pos60≥0.9 → 拉回 50% 中段
    # ⚠ 只罚高分 (raw_score > base), 不奖励低分: 低位 + 末期是"高位回调"而非"末期狂热"
    # ⚠ nav20 ≥ 30% 上限保护: 主升浪强势趋势 (e.g. 月度涨 30%+), 不视为末期
    #    回测 (panel n=3955, 2024-01~2026-04):
    #    - z_main≥2.5 末期 (n=64) fwd_30d 跑输正常 -4.77pp, fwd_60d 修复
    #    - nav20+pos60 末期信号偏弱: 20D gap 仅 +0.9pp (建议未来用 z_main 替代)
    avg_nav20 = statistics.mean([s.nav20d for s in sigs if s.nav20d is not None]) \
        if any(s.nav20d is not None for s in sigs) else None
    avg_pos60 = statistics.mean([s.pos60 for s in sigs if s.pos60 is not None]) \
        if any(s.pos60 is not None for s in sigs) else None
    overbought_applied = False
    if (avg_nav20 is not None and 15.0 <= avg_nav20 < 30.0
            and avg_pos60 is not None and avg_pos60 >= 0.9
            and raw_score > base):  # 只罚高分 + 上限保护
        raw_score = 0.5 * raw_score + 0.5 * base  # 末期反转拉回 50%
        overbought_applied = True

    score = max(0.0, min(40.0, round(raw_score, 1)))

    avg_sm_20d = statistics.mean([s.sm_20d for s in sigs])
    avg_main_minus_sm_20d = statistics.mean([s.main_minus_sm_20d for s in sigs])
    sm_str = _fmt_cny(avg_sm_20d)
    md_str = _fmt_cny(avg_main_minus_sm_20d)

    if avg_z_main is None and avg_z_rate is None:
        note = f"BK 历史不足 (<80d, 走中位 base). main-sm_20d_raw={md_str}, sm_20d={sm_str}"
    else:
        zm = f"{avg_z_main:+.2f}σ" if avg_z_main is not None else "n/a"
        zr = f"{avg_z_rate:+.2f}σ" if avg_z_rate is not None else "n/a"
        zs = f"{avg_z_sm:+.2f}σ"   if avg_z_sm is not None else "n/a"
        rv  = " 🔄 极端反转拉回" if revert_applied else ""
        ob  = (f" ⚠️末期反转 (nav20={avg_nav20:+.1f}%, pos60={avg_pos60*100:.0f}%)"
               if overbought_applied else "")
        note = (f"base 20 + main-sm_z {zm} ({main_pts:+.1f}) + rate_z {zr} ({rate_pts:+.1f}) "
                f"= {score:.1f}/40{rv}{ob}; sm_z={zs} (info), raw main-sm={md_str}, sm={sm_str}")

    diag = {
        "bks": [{"code": s.bk_code, "name": s.bk_name,
                 "main_minus_sm_z": s.main_minus_sm_z,
                 "rate_z": s.rate_z,
                 "sm_z": s.sm_z,
                 "sm_20d": s.sm_20d, "main_20d": s.main_20d,
                 "main_minus_sm_20d": s.main_minus_sm_20d,
                 "nav20d": s.nav20d, "pos60": s.pos60,
                 "n_history": s.n_days_history} for s in sigs],
        "avg_z_main_minus_sm": avg_z_main,
        "avg_z_rate": avg_z_rate,
        "avg_z_sm": avg_z_sm,
        "avg_nav20d": avg_nav20,
        "avg_pos60": avg_pos60,
        "main_pts": main_pts,
        "rate_pts": rate_pts,
        "base": base,
        "revert_applied": revert_applied,
        "overbought_applied": overbought_applied,
        "version": "v3.2",
    }
    return score, note, diag


# ─── helpers ────────────────────────────────────────────────────────────────

def _fmt_cny(x: float) -> str:
    if abs(x) >= 1e8:
        return f"{x/1e8:+.2f}亿"
    if abs(x) >= 1e4:
        return f"{x/1e4:+.0f}万"
    return f"{x:+.0f}"


# ─── CLI ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--concept", required=False, help="测试单个 concept 的 fund_flow_score_v3")
    p.add_argument("--list", action="store_true", help="列出 yaml 里所有 mapping 状态")
    p.add_argument("--all", action="store_true", help="跑全部已映射 concept")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    cmap = load_concept_bk_map()
    if args.list:
        mapped = [c for c, v in cmap.items() if v.get("bks")]
        pending = [c for c, v in cmap.items() if v.get("status") == "pending"]
        print(f"📋 concept_bk_map.yaml 状态:")
        print(f"  已映射: {len(mapped)} concepts")
        for c in mapped:
            bks = cmap[c]["bks"]
            print(f"    {c}: {[b['code'] for b in bks]}")
        print(f"\n  PENDING: {len(pending)} concepts")
        for c in pending:
            print(f"    {c}: hint={cmap[c].get('hint','-')}")
        return

    targets = []
    if args.concept:
        targets = [args.concept]
    elif args.all:
        targets = [c for c, v in cmap.items() if v.get("bks")]
    else:
        p.print_help(); return

    out = []
    for c in targets:
        score, note, diag = fund_flow_score_v3(c)
        out.append({"concept": c, "score": score, "note": note, "diag": diag})
        if not args.json:
            print(f"\n━━━ {c} ━━━")
            print(f"  score: {score}/40")
            print(f"  note:  {note}")
            for bk in diag.get("bks", []):
                zm = f"{bk['main_minus_sm_z']:+.2f}" if bk['main_minus_sm_z'] is not None else "n/a"
                zr = f"{bk['rate_z']:+.2f}" if bk['rate_z'] is not None else "n/a"
                print(f"    {bk['code']:<14} {bk['name']:<14} z(main-sm)={zm}  z(rate)={zr}  n={bk['n_history']}")

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
