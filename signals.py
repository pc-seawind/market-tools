"""signals.py — 市场信号检测 (纯函数, 从 metrics 推信号).

被 daily.sh 和 backtest.sh 共享, 修改信号规则只改这一个文件,
两边自动同步.

## 信号规则 (基于 2025-09 → 2026-05 回测验证)

### 卖点分级 (SELL_EXHAUSTION → CONFIRMED → EXTREME)
基于回测: 江波龙 12 次 SELL_EXHAUSTION 样本中 92% 在 +20d 下跌.
分级避免误判早期, 帮助判断情绪顶深度:

  ⚠️  SELL_EXHAUSTION   1W > +15 + 位置 > 85    [进入 20 日观察期]
  🔴 SELL_CONFIRMED     1W > +25 + 位置 > 85    [确认, 开始减仓]
  ⛔ SELL_EXTREME       1W > +35 + 位置 > 85    [极端顶, 立即减仓]

### SELL_BREAKDOWN (加位置过滤)
回测发现: 强势股 (位置 > 70) 下跌缩量是**健康回调**, 不是破坏.
仅低位缩量才真破坏.

  📉 SELL_BREAKDOWN   1W < -10 + 量比 < 0.8 + 位置 < 50

### SELL_TOP (修复)
原规则 `1M > +50 + 1W < 0` 在强势股正常回调时误触发 (江波龙 3 次全失败).
新规则要求: 大涨过后 + 放量下跌, 区分"回调"与"顶部".

  🔻 SELL_TOP   3M > +100 + 1W < -10 + 量比 > 1.5

### BUY_PULLBACK (加 r3m 确认)
原规则在震荡股上失败 (澜起 6 次 BUY_PULLBACK 全负收益).
新规则要求 r3m > 0, 确保在**明确上升趋势**中的回调才是买点.

  💧 BUY_PULLBACK   1M ≥ +20 + 1W ∈ (-10, 0) + 量比 < 1 + 3M > 0

### 其他信号 (未改动)
  📈 BUY_EARLY     量比 ≥ 2 + 1W ∈ [-3, +5]
  🎯 BUY_BREAKOUT  位置 ≥ 85 + 量比 ≥ 1.5 + 1W ∈ (0, 10]
  🚀 TODAY_SURGE   当日 ≥ +7%
  💥 TODAY_DROP    当日 ≤ -7%
"""


def detect(m):
    """从 metrics dict 推 signals list.

    Input m = {
        "r1w":             float | None,
        "r1m":             float | None,
        "r3m":             float | None,   # 新增 (SELL_TOP + BUY_PULLBACK 需要)
        "vol_ratio":       float | None,
        "pos":             float | None,
        "pct_chg_today":   float | None,
    }

    Output: [(icon, signal_type, description), ...]
    """
    sigs = []
    r1w = m.get("r1w")
    r1m = m.get("r1m")
    r3m = m.get("r3m")
    vr  = m.get("vol_ratio")
    pos = m.get("pos")
    today = m.get("pct_chg_today")

    # ════════════════ 买点 ════════════════

    # BUY_EARLY: 放量企稳 (量比 ≥ 2x 且 1W 平稳)
    if vr is not None and vr >= 2.0 and r1w is not None and -3 <= r1w <= 5:
        sigs.append(("📈", "BUY_EARLY",
            f"放量企稳 (量比 {vr:.1f}x, 1W {r1w:+.1f}%), 机构早期吸筹特征"))

    # BUY_BREAKOUT: 放量突破 (位置接近高点 + 放量 + 温和上涨)
    if pos is not None and pos >= 85 and vr is not None and vr >= 1.5 \
       and r1w is not None and 0 < r1w <= 10:
        sigs.append(("🎯", "BUY_BREAKOUT",
            f"放量突破 (位置 ≈{pos:.0f}%, 量比 {vr:.1f}x, 1W +{r1w:.1f}%), 趋势加速"))

    # BUY_PULLBACK: 已移除 (2026-05 两轮 backtest 验证仍无效, +20d 0% 正收益)
    # 原规则: 1M ≥ +20 + 1W ∈ (-10, 0) + 量比 < 1 + r3m > 0 + 位置 < 80
    # 问题: 纯技术指标无法区分"强势回调"和"顶部震荡", 都打到顶部震荡.
    # 结论: 除非加入主题/行业/基本面验证, 否则这类信号技术指标不可靠.
    # 用户如需"左侧买点", 改用 funnel.sh / momentum.sh --preset=contrarian.

    # ════════════════ 卖点分级 ════════════════

    # SELL 三级 (从弱到强, 短路: 只报最强的一级)
    if r1w is not None and pos is not None and pos > 85:
        if r1w > 35:
            sigs.append(("⛔", "SELL_EXTREME",
                f"极端顶部 (1W {r1w:+.1f}% ≥ +35%, 位置 ≈{pos:.0f}%), "
                f"情绪极度过热, 立即分批减仓"))
        elif r1w > 25:
            sigs.append(("🔴", "SELL_CONFIRMED",
                f"顶部确认 (1W {r1w:+.1f}% ≥ +25%, 位置 ≈{pos:.0f}%), "
                f"末期加速加剧, 开始减仓 30-50%"))
        elif r1w > 15:
            sigs.append(("⚠️", "SELL_EXHAUSTION",
                f"末期警示 (1W {r1w:+.1f}%, 位置 ≈{pos:.0f}%), "
                f"进入 20 日观察期, 暂不加仓"))

    # SELL_BREAKDOWN: 已移除 (两轮 backtest 验证后仍无效)
    # 第一轮: 1W < -10 + 量比 < 0.8 → +20d 77% 正收益
    # 第二轮: 加 r1m<-10 + r3m<0 + 位置<40 → 触发减到 2 次但 +20d 100% 正
    # 根本问题: "持续下跌 + 缩量" 在 A 股里更多是"超跌反弹前夜"而不是"破位",
    # 技术指标单独无法判断 "趋势已死" vs "深度调整后反弹".
    # 结论: 纯技术信号对 breakdown 场景不可靠, 移除.

    # SELL_TOP: 主升浪末端 (要求 3M > 100 + 放量下跌)
    if (r3m is not None and r3m > 100
        and r1w is not None and r1w < -10
        and vr is not None and vr > 1.5):
        sigs.append(("🔻", "SELL_TOP",
            f"主升浪末端 (3M +{r3m:.1f}%, 1W {r1w:+.1f}% 放量下跌 "
            f"量比 {vr:.1f}x), 高概率顶部"))

    # ════════════════ 当日异动 ════════════════

    if today is not None and today >= 7:
        sigs.append(("🚀", "TODAY_SURGE",
            f"当日急涨 {today:+.1f}%, 短期警惕获利回吐"))
    if today is not None and today <= -7:
        sigs.append(("💥", "TODAY_DROP",
            f"当日急跌 {today:+.1f}%, 排查基本面触发"))

    return sigs


# ============================================================
# 位置近似计算 (daily.sh/backtest.sh 共享)
# 真实 120 天位置需拉 120 天 daily, 这里用 r1m 作 proxy
# ============================================================

def position_proxy(r1m):
    """从 1M 涨幅推算 120 天位置 (%). 粗糙但成本低."""
    if r1m is None: return None
    if r1m >= 40:   return 90
    if r1m >= 20:   return 75 + (r1m - 20) * 0.75
    if r1m >= 0:    return 50 + r1m * 1.25
    if r1m >= -20:  return 50 + r1m * 1.5
    return 20


if __name__ == "__main__":
    # 自测: 几个典型 case
    tests = [
        {"name": "寒武纪 20260507 (极端末期)",
         "m": {"r1w": 37.7, "r1m": 82.1, "r3m": 60, "vol_ratio": 1.5,
               "pos": 90, "pct_chg_today": 2.36}},
        {"name": "澜起科技 20260507 (确认顶)",
         "m": {"r1w": 17.9, "r1m": 64.9, "r3m": 20, "vol_ratio": 2.1,
               "pos": 90, "pct_chg_today": 5.49}},
        {"name": "江波龙 20251020 (完美 BUY_PULLBACK)",
         "m": {"r1w": -1.9, "r1m": 61.6, "r3m": 80, "vol_ratio": 0.9,
               "pos": 90, "pct_chg_today": 0}},
        {"name": "澜起 20251020 (假 BUY_PULLBACK, r3m 负)",
         "m": {"r1w": -7.9, "r1m": 26.7, "r3m": -10, "vol_ratio": 0.8,
               "pos": 80, "pct_chg_today": -2}},
        {"name": "江波龙 20251016 (旧 SELL_TOP 误触发)",
         "m": {"r1w": -2.5, "r1m": 97.3, "r3m": 80, "vol_ratio": 1.5,
               "pos": 90, "pct_chg_today": -1}},
    ]
    for t in tests:
        sigs = detect(t["m"])
        print(f"\n{t['name']}:")
        if sigs:
            for icon, typ, desc in sigs:
                print(f"  {icon} {typ}: {desc}")
        else:
            print(f"  (无信号)")
