"""grading.py — 板块热度注入 + 诊断标签 (2026-09-10 重构).

## 这个模块现在做什么

给任意候选股附加三类**描述性**信息:
  1. `_heat_score` / `_heat_label` / `_heat_reason` — 该股所属板块的热度档 (1-4)
  2. `_rel_delta` / `_rel_label` — 个股 1M 相对板块 1M 的偏离 (诊断标签)
  3. `_fund_adj` / `_fund_label` — 基本面 (ROE / 净利 YoY) 诊断标签
  4. `_top_flag` — 是否触发 SIG_TOP_EXTREME (极端拉伸)

排序用 `_score = heat_score + fund_adj`, 但**主排序 = `_heat_score`**;
基本面只在同一热度档内做微调 (基本面单独未回测, 不改变档位归属).

## 为什么删掉了 A/B/C/D 分级 (2026-09-10)

旧设计: adjusted = heat - sell_penalty + buy_bonus + rel_strength → A/B/C/D.
全量回测 (backtest_component_audit.py, n=15788, 214 只 × 75 评估日) 结论:

  - `sell_penalty` **方向是反的**: SELL_EXHAUSTION / SELL_CONFIRMED 之后
    20d 绝对收益 +4.88% / +1.46%, 好于基准 +2.20% (超额 +4.03% / +0.63%).
    扣它们的分 = 系统性压低强势动量股.
  - `relative_strength_adj` **非单调**: delta>+20 → 20d 超额 +1.96%;
    delta<-15 → +2.10%; 而中间段 (0~-5) → **-0.44%**.
    也就是说"跑输板块"不是坏事, "紧跟板块"才最差; 线性 ±2 打分没有依据.
  - `buy_bonus` 依赖的 BUY_EARLY / BUY_BREAKOUT 已因负 alpha / 零 alpha 删除.
  - 剩下的 grade 分布也非单调: A(+3.45%/20d) > B(+0.17%) > **D(+0.50%) > C(-0.68%)**.
    "D = 警示" 的说法与数据不符 → 分级字母是**虚假的精度**, 已删除.

删掉 A/B/C/D 后, 真正有前瞻 alpha 的信息只剩**板块热度**:
HEAT_4 → 20d 超额 +2.27% / 40d +5.29%; HEAT_2 → -0.89%; HEAT_1 → +0.53%.
而"板块热度"本来就由 sector_score.py 的板块 tier (HOT_STRONG/HOT/NEUTRAL/COLD)
表达 —— 保留 A/B/C/D 就是同一个信息的第二套命名 (用户 2026-09-10 指出的
"名词重复率高" 问题之一). 所以本模块不再产出推荐度字母.

被 funnel.sh / momentum.sh / screen.sh 共享 (funnel/momentum 已归档, 见
docs/COMPONENT_LEDGER.md).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import signals as _sig_mod
except Exception:
    _sig_mod = None


HEAT_DESC = {
    4: "🔥🔥 热度 4 · 板块最强 (回测 20d 超额 +2.27% / 40d +5.29%)",
    3: "🔥 热度 3 · 板块偏热 (回测 20d 超额 +0.08%)",
    2: "⚖️ 热度 2 · 板块中性偏冷 (回测 20d 超额 -0.89%)",
    1: "🧊 热度 1 · 板块最冷 (回测 20d 超额 +0.53%)",
}


def rel_label(delta):
    """相对板块偏离的**描述**标签 (不打分, 见模块 docstring 的回测依据)."""
    if delta is None:
        return ""
    if delta > 20:
        return f"大幅跑赢板块 +{delta:.1f}pp"
    if delta > 10:
        return f"跑赢板块 +{delta:.1f}pp"
    if delta > -5:
        return f"跟随板块 {delta:+.1f}pp"
    if delta > -15:
        return f"轻微跑输板块 {delta:+.1f}pp"
    return f"大幅跑输板块 {delta:+.1f}pp"


def top_flag(sig_names):
    """是否有极端拉伸标记 (SIG_TOP_EXTREME)."""
    return "SIG_TOP_EXTREME" in set(sig_names or [])


def fundamentals_adj(roe_ttm, netprofit_yoy, or_yoy=None):
    """基本面诊断: 返回 (score_adj -2..+2, label). **档内微调用, 不单独回测**.

    回应 "基本面没接入选股逻辑" 的问题. 原则: 有数据才调整, 无数据返 0.
    """
    if roe_ttm is None and netprofit_yoy is None:
        return 0, ""   # 无基本面数据, 不影响

    roe_level = None
    if roe_ttm is not None:
        if roe_ttm > 15:  roe_level = "强"
        elif roe_ttm > 8: roe_level = "中"
        elif roe_ttm > 0: roe_level = "弱"
        elif roe_ttm > -10: roe_level = "亏损"
        else:             roe_level = "严重亏损"

    yoy_level = None
    if netprofit_yoy is not None:
        if netprofit_yoy > 50:   yoy_level = "爆发"
        elif netprofit_yoy > 10: yoy_level = "增长"
        elif netprofit_yoy > -10: yoy_level = "平稳"
        elif netprofit_yoy > -30: yoy_level = "下滑"
        else:                    yoy_level = "恶化"

    adj = 0
    if roe_level in ("强",):                                adj += 2
    elif roe_level in ("中",):                              adj += 1
    elif roe_level in ("亏损",):                            adj -= 1
    elif roe_level in ("严重亏损",):                        adj -= 2

    if yoy_level in ("爆发",):                              adj += 1
    elif yoy_level in ("下滑",):                            adj -= 1
    elif yoy_level in ("恶化",):                            adj -= 2

    adj = max(-2, min(+2, adj))

    parts = []
    if roe_ttm is not None:
        parts.append(f"ROE {roe_ttm:.1f}%")
    if netprofit_yoy is not None:
        parts.append(f"净利 YoY {netprofit_yoy:+.0f}%")
    if or_yoy is not None:
        parts.append(f"营收 YoY {or_yoy:+.0f}%")
    icon = "👍" if adj >= 1 else ("⚠️" if adj <= -1 else "·")
    label = f"{icon} {' '.join(parts)}" if parts else ""
    return adj, label


def compute_grade(r, sh_info, style="balanced", fund_info=None):
    """给一只股附上板块热度档 + 诊断标签.

    Args:
      r: dict 含 r1w, r1m, r3m, vol_ratio (或 amt_cur/amt_20d), pct_chg
      sh_info: sector_health.build_index 的单只结果
               (heat_score / heat_label / reason / r1m)
      style: 已废弃 (旧 balanced/momentum/contrarian 影响的是被删除的
             sell/buy 因子). 保留参数仅为兼容调用方.
      fund_info: 可选基本面 dict (roe_ttm/roe, netprofit_yoy, or_yoy)

    Returns: 同一个 dict, 附加以下键:
      _heat_score / _heat_label / _heat_reason / _heat_r1m
      _rel_delta / _rel_label
      _fund_adj / _fund_label
      _top_flag
      _score  (heat_score + fund_adj, 桶内微调用)
      _adj_score (== _score, 旧名兼容)
    """
    heat_score = sh_info.get("heat_score", 2)
    r["_heat_score"] = heat_score
    r["_heat_label"] = sh_info.get("heat_label", "🟡")
    r["_heat_reason"] = sh_info.get("reason", "")
    r["_heat_r1m"] = sh_info.get("r1m")

    # 相对板块偏离 (诊断标签, 不打分)
    sector_r1m = sh_info.get("r1m")
    stock_r1m = r.get("r1m")
    delta = (stock_r1m - sector_r1m) if (stock_r1m is not None
                                         and sector_r1m is not None) else None
    r["_rel_delta"] = delta
    r["_rel_label"] = rel_label(delta)

    # 信号 (只做展示标记; 不再进分数)
    sigs = []
    if _sig_mod is not None:
        sigs = _sig_mod.detect({
            "r1w": r.get("r1w"), "r1m": r.get("r1m"),
            "r3m": r.get("r3m"), "vol_ratio": r.get("vol_ratio"),
            "pct_chg_today": r.get("pct_chg"),
        })
    r["_top_flag"] = top_flag([s[1] for s in sigs])
    r["_signal_labels"] = [f"{s[0]} {s[1]}" for s in sigs]

    # 基本面 (档内微调)
    fund_adj, fund_label = 0, ""
    if fund_info:
        fund_adj, fund_label = fundamentals_adj(
            fund_info.get("roe_ttm") or fund_info.get("roe"),
            fund_info.get("netprofit_yoy"),
            fund_info.get("or_yoy"),
        )
    r["_fund_adj"] = fund_adj
    r["_fund_label"] = fund_label

    r["_score"] = heat_score + fund_adj
    r["_adj_score"] = r["_score"]   # 旧名兼容
    return r


def load_fundamentals_map(ts_codes):
    """从 parquet 拉指定股的最新一期 fina_indicator. 返回 {ts_code: {...}}.

    无数据的 ts_code 不在返回 dict 里 (调用方需处理 None).
    """
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import cache_parquet
        import duckdb
    except Exception:
        return {}

    parquet_path = os.path.join(cache_parquet.PARQUET_DIR, "fina_indicator",
                                 "fina_indicator.parquet")
    if not os.path.exists(parquet_path):
        return {}

    codes_sql = "', '".join(ts_codes)
    con = duckdb.connect()
    try:
        # 取每只股最近一期
        q = f"""
            WITH ranked AS (
                SELECT ts_code, end_date, roe, roa, netprofit_yoy, or_yoy,
                       grossprofit_margin, netprofit_margin, debt_to_assets,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY end_date DESC) AS rn
                FROM read_parquet('{parquet_path}')
                WHERE ts_code IN ('{codes_sql}')
            )
            SELECT * EXCLUDE rn FROM ranked WHERE rn = 1
        """
        rows = con.execute(q).fetchall()
        cols = [d[0] for d in con.description]
    except Exception:
        return {}

    def to_float(x):
        try: return float(x) if x not in (None, "", "None") else None
        except: return None

    result = {}
    for row in rows:
        d = dict(zip(cols, row))
        result[d["ts_code"]] = {
            "end_date": d.get("end_date"),
            "roe": to_float(d.get("roe")),
            "roa": to_float(d.get("roa")),
            "netprofit_yoy": to_float(d.get("netprofit_yoy")),
            "or_yoy": to_float(d.get("or_yoy")),
            "grossprofit_margin": to_float(d.get("grossprofit_margin")),
            "netprofit_margin": to_float(d.get("netprofit_margin")),
            "debt_to_assets": to_float(d.get("debt_to_assets")),
        }
    return result


# ============================================================
# 渲染辅助
# ============================================================

def render_group(stocks, heading=None, show_tags=True):
    """把 [stocks] (已打热度标签) 按板块热度档分组并渲染.

    Returns: str
    """
    from collections import defaultdict
    by_heat = defaultdict(list)
    for r in stocks:
        by_heat[r.get("_heat_score", 2)].append(r)

    lines = []
    if heading:
        lines.append(heading)

    for heat in (4, 3, 2, 1):
        if heat not in by_heat:
            continue
        rs = sorted(by_heat[heat], key=lambda r: -(r.get("_score", 0)))
        lines.append(f"\n━━━ {HEAT_DESC[heat]}  ({len(rs)} 只) ━━━")
        for r in rs:
            extras = []
            if r.get("_top_flag"):
                extras.append("🛑 极端拉伸")
            if r.get("_rel_label"):
                extras.append(r["_rel_label"])
            extra_str = "  " + " │ ".join(extras) if extras else ""

            tags_str = ""
            if show_tags:
                if r.get("tags"):
                    cs = [c for kind, c in r.get("tags", []) if kind == "concept"]
                    tags_str = " │ " + (cs[0] if cs else r.get("industry", "?") + " [行业]")
                else:
                    tags_str = " │ " + r.get("industry", "?")

            vr = r.get("vol_ratio") or (
                r.get("amt_cur", 0) / max(r.get("amt_20d", 0.01), 0.01))
            pe = r.get("pe_ttm") or 0
            mv = r.get("mv_yi") or 0
            r1m = r.get("r1m")
            r1m_str = f"{r1m:+.1f}%" if r1m is not None else "n/a"

            lines.append(
                f"  {r.get('_heat_label', '?')} {r['ts_code']:<11} {r.get('name', '?')[:8]:<8}  "
                f"PE={pe:>5.1f}  市值={mv:>5.0f}亿  "
                f"1M={r1m_str:>7}  量比={vr:.1f}x{tags_str}{extra_str}"
            )
            if r.get("_heat_reason"):
                lines.append(f"         └ 板块: {r['_heat_reason']}")
            if r.get("_fund_label"):
                lines.append(f"         └ 基本面: {r['_fund_label']}")

    return "\n".join(lines)


if __name__ == "__main__":
    test_cases = [
        {"name": "寒武纪 (板块热 + 大幅跑输)",
         "r": {"ts_code": "688256.SH", "name": "寒武纪", "r1w": -14, "r1m": 5.7,
               "r3m": 40, "vol_ratio": 1.5, "pe_ttm": 180, "mv_yi": 5000,
               "amt_cur": 10, "amt_20d": 8},
         "sh": {"heat_score": 4, "heat_label": "🔥",
                "reason": "板块 +26%", "r1m": 26}},
        {"name": "立讯精密 (板块热 + 跑赢)",
         "r": {"ts_code": "002475.SZ", "name": "立讯精密", "r1w": 2, "r1m": 47.3,
               "r3m": 25, "vol_ratio": 3.9, "pe_ttm": 30, "mv_yi": 5194,
               "amt_cur": 25, "amt_20d": 6.4},
         "sh": {"heat_score": 4, "heat_label": "🔥",
                "reason": "元器件 +25%", "r1m": 25}},
        {"name": "陕西煤业 (板块中性偏冷 + 跑输)",
         "r": {"ts_code": "601225.SH", "name": "陕西煤业", "r1w": -8, "r1m": -7.8,
               "r3m": -3, "vol_ratio": 1.4, "pe_ttm": 15, "mv_yi": 2500,
               "amt_cur": 6, "amt_20d": 4.3},
         "sh": {"heat_score": 2, "heat_label": "🟡",
                "reason": "煤炭 +3.5%", "r1m": 3.5}},
    ]
    for tc in test_cases:
        r = compute_grade(tc["r"], tc["sh"])
        print(f"\n{tc['name']}")
        print(f"  板块: {r['_heat_label']} heat={r['_heat_score']}  板块1M={r['_heat_r1m']:+.1f}%")
        print(f"  个股 1M: {r['r1m']:+.1f}%  {r['_rel_label']}")
        print(f"  → 排序分 {r['_score']} (热度 {r['_heat_score']} + 基本面 {r['_fund_adj']:+d})")
        if r.get("_signal_labels"):
            print(f"  标记: {' / '.join(r['_signal_labels'])}")
