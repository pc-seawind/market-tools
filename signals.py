"""signals.py — 市场异动标记 (纯函数, 从 metrics 推信号).

被 daily.sh / thesis_enrich_daily.py 消费; 修改规则只改这一个文件.

## 命名规范 (2026-09-10 统一)

所有信号常量一律 `SIG_` 前缀 —— 一眼可见"这是**描述性标记**, 不是推荐指令".
推荐动作只有三个词 (BUY / WATCH / AVOID), 由 sector_picks.py 的 action 字段给出;
本文件**不产出推荐**, 只描述当下形态.

## 现存规则 (全部通过全量回测, n=15788 样本 / 214 只 / 75 个评估日)

回测口径: 同日横截面超额收益 (ex) + 绝对收益 (abs), 见
backtest_component_audit.py。基准 = 全样本 abs20 = +2.20% / ex 胜率 40.9%.

  🛑 SIG_TOP_EXTREME   1W > +35% 且 1M > +28%
      [描述性顶部标记, 非行动指令] n=26, 20d 绝对 -1.43% / 超额 -3.38% (胜率 33%),
      40d 绝对 +1.99% / 超额 -0.91%。样本少, 只作"这个位置别加仓"的提示,
      **不构成减仓指令** (减仓走持仓纪律的止损线)。

  🚀 SIG_TODAY_SURGE   当日 ≥ +7%
  💥 SIG_TODAY_DROP    当日 ≤ -7%
      [纯描述] 当日异动, 用于日报/update_log 记录资金情绪, 无前瞻预测含义。

## 已删除规则 + 删除依据 (2026-09-10, 全量回测; 不要再加回来)

  ❌ BUY_EARLY (量比 ≥ 2x + 1W ∈ [-3,+5]) — n=298, 20d 绝对 +1.23% / 超额 -1.74%
     (胜率 32.0% vs 基准 40.9%), 40d 绝对 +3.35% < 基准 +4.19%。
     "放量企稳=机构吸筹" 这个叙事在全量样本上是**负 alpha**。

  ❌ BUY_BREAKOUT (位置 ≥ 85 + 量比 ≥ 1.5 + 1W ∈ (0,10]) —
     真实 pos120 口径 n=365: 20d 超额 +0.37% / 胜率 40.8% (基准 40.9%) → 零 alpha;
     生产口径 (position_proxy(r1m) ≥ 85 ⟺ 1M > +28%) n=**6** → 事实上从不触发。
     规则自身互相矛盾: 1M > +28% 的股票很难同时满足 1W ≤ +10%。

  ❌ SELL_EXHAUSTION / SELL_CONFIRMED (pos > 85 + 1W > 15 / 25) —
     **方向是反的**: 20d 绝对 +4.88% / +1.46%, 超额 +4.03% / +0.63%,
     均**好于**基准 (+2.20%)。也就是说"末期警示"之后继续持有更赚。
     根因: 这条规则本质是"强势动量股"筛选器 —— 高位置 + 大涨 → 动量延续,
     不是顶部。历史上"江波龙 12 样本 92% 下跌"是单票轶事, 全量样本推翻。
     对比: 现在真正的动量暴露由 sector_picks Tier 0 / R_momentum20 通道承担
     (20d 超额 +2.79%), 不需要 signals.py 再重复一遍。

  ❌ SELL_TOP (3M > +100 + 1W < -10 + 量比 > 1.5) — 75 个评估日 × 214 只
     = 15788 样本里 **0 次触发**。不可测 = 不可信, 已删。

  ❌ BUY_PULLBACK / SELL_BREAKDOWN — 更早 (2026-05) 两轮回测已删, 见 git 历史。

## 关于"位置"

本文件**不再接收 `pos` 参数**。历史上 daily.sh 传的是
`position_proxy(r1m)` —— 一个从 1M 涨幅推出来的**近似值**, 与 sector_picks.py 的
真实 120 日百分位 (pct_rank_120d) 是两个不同的东西, 却共用 "pos" 这个字。
命名统一后: 真实位置统一叫 `pos120`, 近似值叫 `pos_proxy` (且不在信号规则里用)。
"""


def detect(m):
    """从 metrics dict 推信号.

    Input m = {
        "r1w":             float | None,   # 5 交易日涨跌 %
        "r1m":             float | None,   # 21 交易日涨跌 %
        "pct_chg_today":   float | None,   # 当日涨跌 %
        # 以下字段历史上被用到, 保留接收以兼容调用方, 当前规则不再消费:
        "r3m": float | None, "vol_ratio": float | None, "pos": float | None,
    }

    Output: [(icon, signal_type, description), ...]
    """
    sigs = []
    r1w = m.get("r1w")
    r1m = m.get("r1m")
    today = m.get("pct_chg_today")

    # ── 顶部标记 (描述性, 非行动指令) ──
    if r1w is not None and r1m is not None and r1w > 35 and r1m > 28:
        sigs.append(("🛑", "SIG_TOP_EXTREME",
            f"极端拉伸 (1W {r1w:+.1f}% > +35% 且 1M {r1m:+.1f}% > +28%), "
            f"此位置不再加仓 (回测 n=26: 20d 绝对 -1.4%, 但不构成减仓指令)"))

    # ── 当日异动 (纯描述) ──
    if today is not None and today >= 7:
        sigs.append(("🚀", "SIG_TODAY_SURGE",
            f"当日急涨 {today:+.1f}%, 短期警惕获利回吐"))
    if today is not None and today <= -7:
        sigs.append(("💥", "SIG_TODAY_DROP",
            f"当日急跌 {today:+.1f}%, 排查基本面触发"))

    return sigs


# ============================================================
# 位置
# ============================================================
#
# 位置有两个含义完全不同的量, 不要混:
#   pos120    — 真实 120 交易日百分位 (0-100), 由 sector_picks.py 从日线序列算
#               (StockMetrics.pct_rank_120d)。做判断用这个。
#   pos_proxy — 从 1M 涨幅反推的粗近似, 只在拿不到日线序列时的**展示**场景用。
#               不要用它在任何规则里做阈值判断。

def pos_proxy(r1m):
    """从 1M 涨幅粗推位置 (%). 仅用于展示兜底, 不要用于规则判断."""
    if r1m is None:
        return None
    if r1m >= 40:
        return 90
    if r1m >= 20:
        return 75 + (r1m - 20) * 0.75
    if r1m >= 0:
        return 50 + r1m * 1.25
    if r1m >= -20:
        return 50 + r1m * 1.5
    return 20


# 旧名保留一个版本, 便于外部脚本平滑迁移 (2026-09-10 起改用 pos_proxy)
position_proxy = pos_proxy


if __name__ == "__main__":
    tests = [
        {"name": "极端拉伸 (1W +37.7 / 1M +82.1)",
         "m": {"r1w": 37.7, "r1m": 82.1, "pct_chg_today": 2.36}},
        {"name": "当日暴跌", "m": {"r1w": -12.0, "r1m": -20.0, "pct_chg_today": -8.1}},
        {"name": "平稳", "m": {"r1w": 1.2, "r1m": 3.0, "pct_chg_today": 0.4}},
    ]
    for t in tests:
        print(f"\n{t['name']}:")
        out = detect(t["m"])
        for icon, typ, desc in out:
            print(f"  {icon} {typ}: {desc}")
        if not out:
            print("  (无信号)")
