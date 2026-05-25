"""sector_score.py — Tier 1 板块综合评分 (framework v2.3).

综合分 = 40% 资金面 + 25% 基本面 + 15% 消息面 + 20% 技术面 (= 100)

子分数定义见 SCORING 各函数. 不是线性映射, 用分段阈值 — 这样极端值
(如 flow_20d < -30 亿) 不会过度放大总分, 中段更有区分度.

CLI:
  python3 sector_score.py --all              # 扫 39 concepts, 输出 table
  python3 sector_score.py --sector <name>    # 单个 concept 详细分解
  python3 sector_score.py --all --json       # JSON 输出 (下游工具消费)
  python3 sector_score.py --hot              # 只显示 HOT (score >= 60) 板块

用法示例:
  python3 sector_score.py --all --hot        # 今天的 HOT sectors
  python3 sector_score.py --sector 光通信     # 单看光通信分解
"""
from __future__ import annotations
import argparse
import csv
import json
import statistics
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from etf_data import load_map, list_concepts, sector_signals, etfs_for, SectorSignals
from bk_moneyflow import fund_flow_score_v3, bks_for_concept

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"


# ─── Tier 1 Gate 决策 (framework v2.3) ─────────────────────────────────────

def tier1_gate(sig: SectorSignals) -> tuple[bool, str]:
    """Return (pass_gate, reason_code).

    pass_gate=True 表示板块进入 Tier 2-4 详细筛选.
    reason_code 描述哪条 rule 触发 (a/b/c/fail).
    """
    if sig.nav_pct_5d > 0:
        return (True, "a: nav_5d 正")
    # Rule (b) 需要跑 Tier 3 才知, 保守处理: nav_5d 负但 flow_20d 正 也进
    if sig.flow_5d_cny > 0 and sig.flow_20d_cny < 0:
        return (True, "c: flow 拐点 (20d 负 5d 正)")
    if sig.flow_5d_cny > 0:
        return (True, "c': flow_5d 正 (可能筑底)")
    return (False, "fail: nav 负 + flow 全负")


# ─── 子分数: 资金面 (40) ──────────────────────────────────────────────────

def fund_flow_score(concept: str, sig: SectorSignals) -> tuple[float, str]:
    """0-40 分 — v3 走 BK 大单/散户 (回归驱动连续映射).

    v2 (deprecated): 用 ETF 份额 × NAV 算 flow_5d_cny / flow_20d_cny + step_20 阶梯.
        回测显示 r=-0.07 反向 + step_20 把信号桶化丢失.
    v3 (current):   走 bk_moneyflow.fund_flow_score_v3
        - 数据源: tushare moneyflow_ind_dc 板块大单+散户
        - 信号:   main_minus_sm_20d_z (15分) + rate_20d_z (5分) + base 20
        - z-score vs 60d 历史, 线性映射 ±2σ → ±15/±5

    无 BK 映射的 concept (yaml status: pending) → NEUTRAL 20.0/40 fallback,
    并标注 "no BK mapping" 让上层知道这个分不可信.

    sig 参数仅作 v2 fallback / 调试; v3 不依赖它. 保留是为了兼容签名.
    """
    score, note, _diag = fund_flow_score_v3(concept)
    return score, note


# ─── 子分数: 基本面 (25) ──────────────────────────────────────────────────

_fina_cache: dict[str, dict[str, float]] = {}


def _ts_csv(api: str, **params) -> list[dict[str, str]]:
    args = ["python3", str(_TUSHARE), api]
    for k, v in params.items():
        if k == "fields":
            args.append(f"--fields={v}")
        else:
            args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def _fetch_fina(ts_code: str) -> dict[str, float]:
    """Latest fina_indicator for one stock. Cached."""
    if ts_code in _fina_cache:
        return _fina_cache[ts_code]
    rows = _ts_csv("fina_indicator", ts_code=ts_code,
                   fields="end_date,roe,grossprofit_margin,netprofit_yoy,or_yoy")
    if not rows:
        _fina_cache[ts_code] = {}
        return {}
    rows.sort(key=lambda x: x.get("end_date", ""), reverse=True)
    latest = rows[0]

    def g(k: str) -> float | None:
        v = latest.get(k)
        if v in (None, "", "None"):
            return None
        try:
            return float(v)
        except ValueError:
            return None

    result = {
        "roe": g("roe"),
        "net_yoy": g("netprofit_yoy"),
        "rev_yoy": g("or_yoy"),
        "gross": g("grossprofit_margin"),
    }
    _fina_cache[ts_code] = result
    return result


def fundamentals_score(concept: str) -> tuple[float, str]:
    """0-25 分. 板块成分股 ROE/净利 YoY 中位数.

    只对有 concepts_data.py 成分股的 concept 算; 无成分股的用 neutral=12.
    """
    try:
        from concepts_data import stocks_of, all_concepts
        if concept not in all_concepts():
            return 12.0, "(neutral: concept 无成分股 list)"
    except ImportError:
        return 12.0, "(neutral: concepts_data import 失败)"

    stocks = stocks_of(concept)
    if not stocks or len(stocks) < 3:
        return 12.0, "(neutral: 成分股 <3)"

    roes = []
    yoys = []
    for code, _name in stocks:
        f = _fetch_fina(code)
        if f.get("roe") is not None:
            roes.append(f["roe"])
        if f.get("net_yoy") is not None:
            yoys.append(f["net_yoy"])

    if not roes or not yoys:
        return 12.0, "(neutral: fina 数据缺)"

    med_roe = statistics.median(roes)
    med_yoy = statistics.median(yoys)
    loss_ratio = sum(1 for r in roes if r < 0) / len(roes)

    # Combine ROE + net_yoy
    # ROE score: 0-12 (roe < 0 → 0; roe > 15 → 12)
    # yoy score: 0-13 (yoy < 0 → 0; yoy > 30 → 13)
    roe_score = max(0, min(12, med_roe * 0.8))
    yoy_score = max(0, min(13, med_yoy / 30 * 13)) if med_yoy > 0 else 0
    if loss_ratio > 0.3:
        # 过多亏损股打折
        roe_score *= 0.5
        yoy_score *= 0.5

    total = round(roe_score + yoy_score, 1)
    note = f"med_ROE={med_roe:.1f}% ({roe_score:.0f}/12), med_净利YoY={med_yoy:.1f}% ({yoy_score:.0f}/13), 亏损股={loss_ratio:.0%}"
    return total, note


# ─── 子分数: 消息面 (15) ──────────────────────────────────────────────────
# phase1_v1 (2026-05-25): 接入 news_score_phase1 (纯 z_repur 信号).
# 回测验证 (n=448, 6个月): 单变量 IC_20d=+0.334, 加入总分 IC 提升 +24.6%
# z_north / z_inst 在回测中证为反指/无信号, 已弃用.
# Fallback: 数据缺失或不在 11 mapped concept → stub-9 neutral.

_NEWS_PHASE1_PRELOAD: dict | None = None  # 进程级 cache, 避免重复拉

def _get_news_preload() -> dict:
    """进程级 cache: 一次拉 90d, 给 news_score_phase1 复用."""
    global _NEWS_PHASE1_PRELOAD
    if _NEWS_PHASE1_PRELOAD is None:
        try:
            import news_score_phase1 as ns
            from datetime import datetime, timedelta
            now = datetime.now()
            hist_start_dt = now - timedelta(days=90)
            hist_start = hist_start_dt.strftime("%Y%m%d")
            today = now.strftime("%Y%m%d")
            _NEWS_PHASE1_PRELOAD = {
                "hsgt": ns.fetch_hsgt_top10_history(hist_start, today),
                "top_inst": ns.fetch_top_inst_history(hist_start, today),
                "repurchase": ns.fetch_repurchase_history(hist_start, today),
            }
        except Exception as e:
            print(f"⚠️  news preload failed: {e}", flush=True)
            _NEWS_PHASE1_PRELOAD = {"hsgt": [], "top_inst": [], "repurchase": []}
    return _NEWS_PHASE1_PRELOAD


def news_score(concept: str) -> tuple[float, str]:
    """0-15 分. phase1_v1: 纯 z_repur signal (回测 IC_20d=+0.334).

    门槛验证: backtest_news_phase1.py n=448 6mo (仅在 11 mapped concept 上)
      单变量 IC_20d=+0.334 (>+0.10 门槛 ✓)
      增量 IC: +24.6% (>+5% 门槛 ✓)
    边界: 普涨行情下信号被市场 beta 淹没 (e.g. 202604 IC_20d=-0.291)
          已用 ±1.5 swing 限制单月反转损害.

    ⚠ 守约: 只对回测过的 11 mapped concept 启用. 30 unmapped concept fallback 到
       stub-9, 等扩展回测验证后再放开 (避免在未验证 concept 上引入未知风险).
    """
    # 检查是否在已验证的 11 mapped concept 中
    try:
        import bk_moneyflow as bf
        cmap = bf.load_concept_bk_map()
        validated = (concept in cmap and cmap[concept].get("bks"))
    except Exception:
        validated = False
    if not validated:
        return 9.0, f"(unmapped concept, phase1 未验证; stub-9 fallback)"

    try:
        import news_score_phase1 as ns
        preload = _get_news_preload()
        if not preload.get("repurchase"):
            return 9.0, "(news data 缺失; stub-9 fallback)"
        score, note, _diag = ns.news_score_phase1(concept, preloaded=preload)
        return score, note
    except Exception as e:
        return 9.0, f"(phase1_v1 失败 {e!r}; stub-9 fallback)"


# ─── 子分数: 技术面 (20) ──────────────────────────────────────────────────

def technical_score(sig: SectorSignals) -> tuple[float, str]:
    """0-20 分. 综合 nav 趋势 + 位置 + 量比.

    用 pct_rank_60d + pct_rank_250d 双窗口 (framework v2.3.1 升级):
    - 60d 看短期趋势
    - 250d 看长期位置 (是否历史新高)
    """
    nav = sig.nav_pct_5d
    pos_60 = sig.pct_rank_60d
    pos_250 = sig.pct_rank_250d
    vol = sig.vol_ratio

    # nav 分 (0-10)
    if nav > 10:  nav_sc = 10
    elif nav > 5: nav_sc = 8
    elif nav > 0: nav_sc = 6
    elif nav > -3: nav_sc = 3
    else:         nav_sc = 0

    # 位置分 (0-7) — 用 60d + 250d 双窗口
    # 理想: 60d 中上 (50-80) + 250d 未到顶 (< 85)  = 可持续
    # 60d 100% + 250d 100% = 双顶, 末期
    # 60d < 30% + 250d < 30% = 底部, 可能触底
    if pos_60 >= 85 and pos_250 >= 85:
        pos_sc = 2   # 双顶末期
    elif pos_60 >= 50 and pos_60 < 85 and pos_250 < 85:
        pos_sc = 7   # 中段健康
    elif pos_60 >= 85 and pos_250 < 70:
        pos_sc = 5   # 新一轮启动 (短期冲高但长期未顶)
    elif pos_60 < 30:
        pos_sc = 3   # 底部
    else:
        pos_sc = 5   # 其他

    # 量比分 (0-3)
    if vol >= 1.5: vol_sc = 3
    elif vol >= 1.2: vol_sc = 2
    elif vol >= 0.8: vol_sc = 1
    else: vol_sc = 0

    total = round(nav_sc + pos_sc + vol_sc, 1)
    note = f"nav5d={nav:.1f}%({nav_sc:.0f}/10) + 位置[60d={pos_60},250d={pos_250}]({pos_sc:.0f}/7) + 量比{vol:.2f}x({vol_sc:.0f}/3)"
    return total, note


# ─── 综合评分 ─────────────────────────────────────────────────────────────

@dataclass
class SectorScore:
    concept: str
    data_quality: str
    tier1_pass: bool
    tier1_reason: str
    total_score: float
    tier: str                  # HOT_STRONG / HOT / NEUTRAL / COLD / AVOID
    fund_flow_pts: float       # /40
    fundamentals_pts: float    # /25
    news_pts: float            # /15
    technical_pts: float       # /20
    fund_flow_note: str
    fundamentals_note: str
    news_note: str
    technical_note: str
    raw_signals: dict[str, Any]  # nav_5d, position_pcts, flow_20d etc


def tier_label(score: float) -> str:
    if score >= 80: return "🔥🔥 HOT_STRONG"
    if score >= 60: return "🔥 HOT"
    if score >= 45: return "⚖️ NEUTRAL"
    if score >= 30: return "🧊 COLD"
    return "⛔ AVOID"


def score_sector(concept: str) -> SectorScore | None:
    """Score a single concept. Returns None if data unavailable."""
    sig = sector_signals(concept)
    if not sig:
        # Fallback concept (no ETF) — skip for now
        quality, _ = etfs_for(concept)
        return SectorScore(
            concept=concept, data_quality=quality,
            tier1_pass=False, tier1_reason="no ETF data",
            total_score=0.0, tier="⚪ NO_DATA",
            fund_flow_pts=0, fundamentals_pts=0, news_pts=0, technical_pts=0,
            fund_flow_note="-", fundamentals_note="-", news_note="-", technical_note="-",
            raw_signals={},
        )

    flow_pts, flow_note = fund_flow_score(concept, sig)
    fund_pts, fund_note = fundamentals_score(concept)
    news_pts, news_note = news_score(concept)
    tech_pts, tech_note = technical_score(sig)

    total = flow_pts + fund_pts + news_pts + tech_pts
    gate_pass, gate_reason = tier1_gate(sig)

    return SectorScore(
        concept=concept, data_quality=sig.data_quality,
        tier1_pass=gate_pass, tier1_reason=gate_reason,
        total_score=round(total, 1), tier=tier_label(total),
        fund_flow_pts=flow_pts, fundamentals_pts=fund_pts,
        news_pts=news_pts, technical_pts=tech_pts,
        fund_flow_note=flow_note, fundamentals_note=fund_note,
        news_note=news_note, technical_note=tech_note,
        raw_signals={
            "nav_5d": sig.nav_pct_5d, "nav_1m": sig.nav_pct_1m,
            "pct_rank_60d": sig.pct_rank_60d,
            "pct_rank_120d": sig.pct_rank_120d,
            "pct_rank_250d": sig.pct_rank_250d,
            "vol_ratio": sig.vol_ratio,
            "flow_5d_cny": sig.flow_5d_cny,
            "flow_20d_cny": sig.flow_20d_cny,
        },
    )


# ─── CLI utilities ────────────────────────────────────────────────────────

def _fmt_cny(x: float) -> str:
    sign = "+" if x >= 0 else ""
    abx = abs(x)
    if abx >= 1e8:
        return f"{sign}{x/1e8:.1f}亿"
    if abx >= 1e4:
        return f"{sign}{x/1e4:.0f}万"
    return f"{sign}{x:.0f}"


def print_scores_table(scores: list[SectorScore], show_hot_only: bool = False):
    """Print concise table of all scores."""
    # Filter
    if show_hot_only:
        scores = [s for s in scores if s.total_score >= 60]

    # Sort by total desc
    scores.sort(key=lambda x: -x.total_score)

    print(f"\n{'='*140}")
    print(f"{'板块':<30} {'总分':>5}  {'档位':<16} {'资金/40':>8} {'基本/25':>8} {'消息/15':>8} {'技术/20':>8} {'Gate':<5} {'主要信号':<30}")
    print(f"{'='*140}")
    for s in scores:
        gate = "✅" if s.tier1_pass else "❌"
        signal = f"nav5d={s.raw_signals.get('nav_5d',0):+.1f}% pos60={s.raw_signals.get('pct_rank_60d',0)}% flow20d={_fmt_cny(s.raw_signals.get('flow_20d_cny',0))}"
        print(f"{s.concept:<30} {s.total_score:>5.1f}  {s.tier:<16} {s.fund_flow_pts:>8.1f} {s.fundamentals_pts:>8.1f} {s.news_pts:>8.1f} {s.technical_pts:>8.1f} {gate:<5} {signal}")


def print_sector_detail(s: SectorScore):
    print(f"\n━━━ {s.concept} ━━━")
    print(f"  总分: {s.total_score} / 100    档位: {s.tier}    data_quality: {s.data_quality}")
    print(f"  Tier 1 Gate: {'✅ 通过' if s.tier1_pass else '❌ 不通过'} ({s.tier1_reason})")
    print()
    print(f"  📊 资金面 ({s.fund_flow_pts}/40):  {s.fund_flow_note}")
    print(f"  💼 基本面 ({s.fundamentals_pts}/25):  {s.fundamentals_note}")
    print(f"  📰 消息面 ({s.news_pts}/15):  {s.news_note}")
    print(f"  📈 技术面 ({s.technical_pts}/20):  {s.technical_note}")
    print()
    rs = s.raw_signals
    if rs:
        print(f"  原始数据:")
        print(f"    nav: 5d={rs.get('nav_5d',0):+.2f}% 1m={rs.get('nav_1m',0):+.2f}%")
        print(f"    位置: 60d={rs.get('pct_rank_60d',0)} 120d={rs.get('pct_rank_120d',0)} 250d={rs.get('pct_rank_250d',0)}")
        print(f"    flow: 5d={_fmt_cny(rs.get('flow_5d_cny',0))} 20d={_fmt_cny(rs.get('flow_20d_cny',0))}")
        print(f"    量比: {rs.get('vol_ratio',0):.2f}x")


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Tier 1 sector scoring (framework v2.3).")
    gp = ap.add_mutually_exclusive_group(required=True)
    gp.add_argument("--all", action="store_true", help="score all concepts")
    gp.add_argument("--sector", help="score a single concept with details")
    gp.add_argument("--list", action="store_true", help="list all concepts")
    ap.add_argument("--hot", action="store_true", help="filter HOT (score ≥ 60) only")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()

    if args.list:
        for c in list_concepts():
            q, es = etfs_for(c)
            print(f"  [{q}]  {c}")
        return

    concepts = [args.sector] if args.sector else list_concepts()

    scores = []
    for c in concepts:
        s = score_sector(c)
        if s:
            scores.append(s)

    if args.json:
        print(json.dumps([asdict(s) for s in scores], ensure_ascii=False, indent=2))
        return

    if args.sector:
        if scores:
            print_sector_detail(scores[0])
    else:
        print_scores_table(scores, show_hot_only=args.hot)


if __name__ == "__main__":
    main()
