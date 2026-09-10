# archive/ — 已退役组件

2026-09-10 一次全量审计 (`backtest_component_audit.py`, n=15788 样本 = 214 只
concept 成分股 × 75 个评估日, 2025-01 ~ 2026-07) 之后, 把"没有任何 cron 消费、
且回测不支持其有效性"的组件移到这里。**不是删除** —— 需要时 `git mv` 回去即可。

审计结论全文见 `docs/COMPONENT_LEDGER.md`。

| 文件 | 退役原因 |
|---|---|
| `funnel.sh` | 无消费者 (44 个 cron 里 0 个引用) + 它依赖的 `grading.py` A/B/C/D 分级回测后**非单调** (A +3.45%/20d > B +0.17% > D +0.50% > C -0.68%), 分级本身没有可用的序。它想解决的"全市场基础仓候选"目前**没有在线承担者** —— `screen.sh` 存在且能跑 (<15s vs funnel 多轮 API 几分钟), 但 2026-09-10 逐条 grep 63 个 cron 的 prompt 快照后核实**它未接入任何 cron** (原表述"接入周日展望"不成立)。该能力空缺 + 三个处置选项见 `docs/COMPONENT_LEDGER.md` §7 缺口 1, 决策前不改代码。 |
| `momentum.sh` | 同上。且"博弈仓追涨"这一通道已由 `sector_picks.py` 的 `TREND` 通道承担, 且那条有回测支持 (R_momentum20 20d 超额 +2.79%)。两套追涨系统 = 重复命名/重复结论。 |
| `backtest.sh` | 单票信号重放工具。`signals.py` 2026-09-10 只剩 3 条描述性标记, 该工具失去对象; 信号有效性证据改由 `backtest_component_audit.py` (横截面 15788 样本) 与 `backtest_leftside_reversal.py` (左向通道) 提供。 |
| `signal_collector.py` | 7 维短线信号采集器 (龙虎榜/北向/主力/公告/研报/融资/突破结构), 唯一消费者是它自己的两个回测脚本, 从未进入任何报告的推荐路径。 |
| `backtest_signals.py` | 只服务 `signal_collector.py` (已退役)。 |
| `backtest_signals_v2.py` | 同上。 |

## 退役判据

一个组件进入 `archive/` 需要同时满足:

1. **无消费者** — 没有任何 cron job / 报告 / 其他脚本调用它 (用 `/tmp/cron-audit-*/`
   的 cron 快照 + 本地引用图核对);
2. **无回测支持** 或 **回测反向** — 要么没有被验证过, 要么验证结果显示无效;
3. **有替代路径** — 它想解决的问题由另一个仍在线的组件承担 (避免留下能力空洞)。
   ⚠️ 这一条**必须实证核实** (grep 消费者), 不能凭印象填 —— 2026-09-10 在 `funnel.sh` 上
   就错填过一次 ("改由 screen.sh 承担", 实际 screen.sh 无人消费)。

三条不同时满足的组件**不退役**, 只写进台账并标注状态 (例: `news_score_phase2_unlock.py`
= 无消费者, 但其自身回测证据尚未复核 → 台账里标"待决", 先不动)。
