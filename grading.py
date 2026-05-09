"""grading.py — 统一的选股推荐度分级模块.

被 funnel.sh / momentum.sh / screen.sh 共享. 修改分级规则只改这一个文件,
所有选股工具自动同步, 避免工具间的分级不一致.

评级维度 (逐步累加, 当前已实现的):
  1. 板块健康度 (sector_health)     heat_score 1-4
  2. SELL 信号 veto (signals.py)    penalty 0-3
  3. BUY 信号 bonus                  bonus 0/1
  4. 个股相对板块强度 (relative_strength)  ±2
  5. 基本面 (fundamentals_grade)     TODO: Phase 3 加入
  6. 消息面负面扫描 (news_scan)      TODO: Phase 4 加入

综合公式 (balanced):
  adjusted = heat_score - sell_penalty + buy_bonus + relative_strength_adj
           [+ fundamentals_adj - news_risk_adj]   (未来)

推荐度映射:
  adjusted ≥ 5: A (强推)
  adjusted ≥ 3: B (推荐)
  adjusted ≥ 2: C (观察)
  adjusted < 2: D (警示, 不剔除但明确降级)

设计哲学 (用户要求):
  - 所有候选放进监控, 逐步迭代
  - 分级而非二元过滤, 不粗暴剔除
  - 不同风格 (funnel/momentum) 可自定义 grade 阈值但用同一套因子
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import signals as _sig_mod
except Exception:
    _sig_mod = None


# ============================================================
# 分级计算
# ============================================================

def relative_strength_adj(stock_r1m, sector_r1m):
    """个股相对板块的 1M 表现调整.

    解决 "寒武纪板块 +26% 但个股 +5% 跑输却被评 A" 问题.

    Returns: int adjustment (-2 / -1 / 0 / +1 / +2)
    """
    if stock_r1m is None or sector_r1m is None:
        return 0
    delta = stock_r1m - sector_r1m
    if delta > 20:   return +2   # 大幅跑赢板块 → 真龙头
    if delta > 10:   return +1   # 跑赢
    if delta > -5:   return 0    # 跟随板块
    if delta > -15:  return -1   # 轻微跑输
    return -2                    # 严重跑输 (可能是问题股)


def detect_sell_buy_signals(r1w, r1m, r3m, vol_ratio, pos, pct_chg_today=None):
    """调 signals.py 检测信号. 返回 (sell_types_set, buy_types_set)."""
    if _sig_mod is None:
        return set(), set()
    sigs = _sig_mod.detect({
        "r1w": r1w, "r1m": r1m, "r3m": r3m,
        "vol_ratio": vol_ratio, "pos": pos,
        "pct_chg_today": pct_chg_today,
    })
    sell_types = {s[1] for s in sigs if s[1].startswith("SELL_")}
    buy_types  = {s[1] for s in sigs if s[1].startswith("BUY_")}
    return sell_types, buy_types


def sell_penalty(sell_types):
    """根据 SELL 信号强度返回扣分 + label."""
    if "SELL_EXTREME" in sell_types:
        return 3, "⛔ SELL_EXTREME"
    if "SELL_CONFIRMED" in sell_types or "SELL_TOP" in sell_types:
        label = "🔴 SELL_CONFIRMED" if "SELL_CONFIRMED" in sell_types else "🔻 SELL_TOP"
        return 2, label
    if "SELL_EXHAUSTION" in sell_types:
        return 1, "⚠️ SELL_EXHAUSTION"
    return 0, ""


def buy_bonus(buy_types):
    """BUY 信号加分 + label."""
    if not buy_types:
        return 0, ""
    return 1, " + ".join(sorted(buy_types))


def fundamentals_adj(roe_ttm, netprofit_yoy, or_yoy=None):
    """基本面调整分, 基于 ROE + 净利增速.

    回应 "基本面没接入选股逻辑" 的问题. 原则: 有数据才调整, 无数据返 0
    不影响其他维度.

    Returns: (score_adj, label)
      score_adj: -2 / -1 / 0 / +1 / +2
      label: 诊断文字, e.g. "ROE 18%/YoY +35% 👍" 或 "ROE -5%/亏损 ⚠️"
    """
    if roe_ttm is None and netprofit_yoy is None:
        return 0, ""   # 无基本面数据, 不影响评级

    # 分级 (宽松, 符合用户要求的"先宽松后迭代")
    roe_level = None
    if roe_ttm is not None:
        if roe_ttm > 15:  roe_level = "强"     # ROE > 15%
        elif roe_ttm > 8: roe_level = "中"     # ROE 8-15
        elif roe_ttm > 0: roe_level = "弱"     # ROE 0-8
        elif roe_ttm > -10: roe_level = "亏损" # ROE -10 to 0
        else:             roe_level = "严重亏损"

    yoy_level = None
    if netprofit_yoy is not None:
        if netprofit_yoy > 50:   yoy_level = "爆发"
        elif netprofit_yoy > 10: yoy_level = "增长"
        elif netprofit_yoy > -10: yoy_level = "平稳"
        elif netprofit_yoy > -30: yoy_level = "下滑"
        else:                    yoy_level = "恶化"

    # 组合评分
    adj = 0
    if roe_level in ("强",):                                adj += 2
    elif roe_level in ("中",):                              adj += 1
    elif roe_level in ("亏损",):                            adj -= 1
    elif roe_level in ("严重亏损",):                        adj -= 2

    if yoy_level in ("爆发",):                              adj += 1
    elif yoy_level in ("下滑",):                            adj -= 1
    elif yoy_level in ("恶化",):                            adj -= 2

    # cap
    adj = max(-2, min(+2, adj))

    # 诊断 label
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
    """给一只股综合打分, 返回带评级字段的 dict.

    Args:
      r: dict 含 r1w, r1m, r3m, vol_ratio (或能算出), pos, pct_chg
      sh_info: dict 从 sector_health.build_index 得到, 含
                heat_score, heat_label, reason, r1m (板块均涨)
      style: "balanced" (funnel) / "momentum" (追涨, SELL 容忍度更低) /
             "contrarian" (看左侧买点, BUY 加分更多)

    Returns: dict 里附加下列键:
      _heat_score, _heat_label, _heat_reason, _heat_r1m
      _sell_label, _buy_label, _rel_strength, _rel_delta
      _grade (A/B/C/D)
      _adj_score (numeric, for sorting)
    """
    heat_score = sh_info.get("heat_score", 2)
    heat_label = sh_info.get("heat_label", "🟡")
    heat_reason = sh_info.get("reason", "")
    sector_r1m = sh_info.get("r1m")

    stock_r1m = r.get("r1m")
    stock_r1w = r.get("r1w")
    stock_r3m = r.get("r3m")
    vol_ratio = r.get("vol_ratio")
    if vol_ratio is None and r.get("amt_cur") is not None and r.get("amt_20d"):
        vol_ratio = r["amt_cur"] / max(r["amt_20d"], 0.01)
    pos = None
    if _sig_mod is not None:
        pos = _sig_mod.position_proxy(stock_r1m)

    # 信号
    sell_types, buy_types = detect_sell_buy_signals(
        stock_r1w, stock_r1m, stock_r3m, vol_ratio, pos, r.get("pct_chg")
    )
    sp, sell_label = sell_penalty(sell_types)
    bb, buy_label = buy_bonus(buy_types)

    # 相对强度
    rel_adj = relative_strength_adj(stock_r1m, sector_r1m)
    rel_delta = (stock_r1m - sector_r1m) if (stock_r1m is not None
                                              and sector_r1m is not None) else None

    # 基本面调整 (有数据才生效)
    fund_adj = 0
    fund_label = ""
    if fund_info:
        fund_adj, fund_label = fundamentals_adj(
            fund_info.get("roe_ttm") or fund_info.get("roe"),
            fund_info.get("netprofit_yoy"),
            fund_info.get("or_yoy"),
        )

    adjusted = heat_score - sp + bb + rel_adj + fund_adj

    # momentum 风格: SELL 信号硬降级 (不管 rel_strength 加分多高)
    # 追涨买入 SELL_EXTREME 的股 = 接末期逃命筹码, 直接 D
    if style == "momentum":
        if "SELL_EXTREME" in sell_types:
            adjusted = min(adjusted, 1)   # 强制 D
        elif "SELL_CONFIRMED" in sell_types or "SELL_TOP" in sell_types:
            adjusted = min(adjusted, 1)   # 强制 D (追涨风格中, 顶部确认即不追)
        elif "SELL_EXHAUSTION" in sell_types:
            adjusted = min(adjusted, 2)   # 最多 C

    # contrarian 风格: BUY 信号翻倍加分 (之前在 bb 处理里已经 ×2)
    elif style == "contrarian":
        if buy_types:
            adjusted += bb   # 再加一次 bb, 共 3x 买信号权重

    # 映射到 grade
    if adjusted >= 5:   grade = "A"
    elif adjusted >= 3: grade = "B"
    elif adjusted >= 2: grade = "C"
    else:               grade = "D"

    r["_heat_score"] = heat_score
    r["_heat_label"] = heat_label
    r["_heat_reason"] = heat_reason
    r["_heat_r1m"] = sector_r1m
    r["_sell_label"] = sell_label
    r["_buy_label"] = buy_label
    r["_rel_adj"] = rel_adj
    r["_rel_delta"] = rel_delta
    r["_fund_adj"] = fund_adj
    r["_fund_label"] = fund_label
    r["_grade"] = grade
    r["_adj_score"] = adjusted
    return r


# ============================================================
# 基本面数据加载 (从 cache_parquet 的 fina_indicator parquet)
# ============================================================

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

GRADE_DESC = {
    "A": "🌟 A 级 · 高推荐 · 板块热 + 无卖信号 + 跑赢板块/有买信号",
    "B": "✅ B 级 · 推荐 · 板块温和, 或弱卖信号",
    "C": "👀 C 级 · 观察 · 信号矛盾 (板块热但信号卖, 或板块温和但个股跑输)",
    "D": "⚠️ D 级 · 警示 · 板块衰退, 或严重卖信号, 或大幅跑输板块",
}


def render_group(stocks, heading=None, show_tags=True):
    """把 [stocks] (已打 grade) 按 grade 分组并渲染.

    假设每只股 dict 已有 _grade/_heat_label/_sell_label/_buy_label
    + 基础字段 ts_code/name/pe_ttm/mv_yi/r1m/amt_cur/amt_20d/tags (optional)

    Returns: str (完整渲染文本)
    """
    from collections import defaultdict
    by_grade = defaultdict(list)
    for r in stocks:
        by_grade[r.get("_grade", "C")].append(r)

    lines = []
    if heading:
        lines.append(heading)

    for grade in ["A", "B", "C", "D"]:
        if grade not in by_grade: continue
        rs = sorted(by_grade[grade], key=lambda r: -(r.get("_adj_score", 0)))
        lines.append(f"\n━━━ {GRADE_DESC[grade]}  ({len(rs)} 只) ━━━")
        for r in rs:
            rel_str = ""
            if r.get("_rel_delta") is not None:
                sign = "↑" if r["_rel_delta"] >= 0 else "↓"
                rel_str = f" (相对板块{sign}{abs(r['_rel_delta']):.1f}pp)"

            extras = []
            if r.get("_sell_label"): extras.append(r["_sell_label"])
            if r.get("_buy_label"):  extras.append(r["_buy_label"])
            extra_str = "  " + " ".join(extras) if extras else ""

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
                lines.append(f"         └ 板块: {r['_heat_reason']}{rel_str}")
            if r.get("_fund_label"):
                lines.append(f"         └ 基本面: {r['_fund_label']}")

    return "\n".join(lines)


if __name__ == "__main__":
    # 自测
    test_cases = [
        {"name": "寒武纪 (板块热+个股跑输)",
         "r": {"ts_code": "688256.SH", "name": "寒武纪", "r1w": -14, "r1m": 5.7,
               "r3m": 40, "vol_ratio": 1.5, "pe_ttm": 180, "mv_yi": 5000,
               "amt_cur": 10, "amt_20d": 8},
         "sh": {"heat_score": 4, "heat_label": "🔥",
                "reason": "板块 +26%", "r1m": 26}},
        {"name": "立讯精密 (板块热+跑赢+BUY)",
         "r": {"ts_code": "002475.SZ", "name": "立讯精密", "r1w": 2, "r1m": 47.3,
               "r3m": 25, "vol_ratio": 3.9, "pe_ttm": 30, "mv_yi": 5194,
               "amt_cur": 25, "amt_20d": 6.4},
         "sh": {"heat_score": 4, "heat_label": "🔥",
                "reason": "元器件 +25%", "r1m": 25}},
        {"name": "陕西煤业 (板块温和+个股跑输)",
         "r": {"ts_code": "601225.SH", "name": "陕西煤业", "r1w": -8, "r1m": -7.8,
               "r3m": -3, "vol_ratio": 1.4, "pe_ttm": 15, "mv_yi": 2500,
               "amt_cur": 6, "amt_20d": 4.3},
         "sh": {"heat_score": 3, "heat_label": "🟡",
                "reason": "煤炭 +3.5%", "r1m": 3.5}},
        {"name": "江波龙 (板块热+SELL_CONFIRMED)",
         "r": {"ts_code": "301308.SZ", "name": "江波龙", "r1w": 25.8, "r1m": 61.3,
               "r3m": 30, "vol_ratio": 2.7, "pe_ttm": 37, "mv_yi": 2017,
               "amt_cur": 50, "amt_20d": 18.5},
         "sh": {"heat_score": 4, "heat_label": "🔥",
                "reason": "存储 +36.8%", "r1m": 36.8}},
    ]
    for tc in test_cases:
        r = compute_grade(tc["r"], tc["sh"])
        print(f"\n{tc['name']}")
        print(f"  板块: {r['_heat_label']} score={r['_heat_score']}  1M={r['_heat_r1m']:+.1f}%")
        print(f"  个股 1M: {r['r1m']:+.1f}%  rel_delta={r['_rel_delta']:+.1f}pp  rel_adj={r['_rel_adj']:+d}")
        print(f"  信号: SELL={r['_sell_label'] or '-'}  BUY={r['_buy_label'] or '-'}")
        print(f"  👉 推荐度: {r['_grade']}  (adj_score={r['_adj_score']})")
