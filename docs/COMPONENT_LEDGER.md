# COMPONENT_LEDGER — market-tools 组件台账

> 生成 2026-09-10。证据来源：`backtest_component_audit.py`
> （n=15788 样本 = 214 只 concept 成分股 × 75 个评估日，2025-01 ~ 2026-07，
> step=5；超额 = 同日横截面去均值）。
> 本文件是 `archive/README.md` 引用的台账全文。
>
> **维护规则**：增删组件、改变消费者关系、或复核某组件的回测证据后，
> 必须同时更新本文件。否则台账会像 README 的旧告警表一样漂移。

---

## 0. 状态定义

| 状态 | 含义 | 处置 |
|---|---|---|
| `ACTIVE(cron)` | 有 cron job 直接调用 → 生产链路 | 改动需回测 + 端到端验证 |
| `ACTIVE(人跑)` | 无 cron，但 README / 流程里由人显式执行 | 改动只需自测 |
| `LIB` | 库模块，只被其他脚本 import | 改动看调用方 |
| `RESEARCH` | 研究 / 回测工具，不面向生产 | 保留，不要求消费者 |
| `RETIRED` | 已 `git mv` 进 `archive/` | 需要时 `git mv` 回来 |
| `PENDING` | 无消费者，且回测证据未复核 | **先不动**，标状态 |

> 判据三条（退役需要同时满足）：
> 1. **无消费者** —— 无 cron / 报告 / 脚本调用；
> 2. **无回测支持** 或 **回测反向**；
> 3. **有替代路径** —— 它想解决的问题由另一个在线组件承担。
>
> 三条不同时满足 → 不退役，进 §6 PENDING。**判据 3 必须实证核实**，
> 2026-09-10 就出过一次错（见 §7 缺口 1）。

---

## 1. 生产链路 — `ACTIVE(cron)`

| 组件 | cron 消费者 | 回测状态 | 备注 |
|---|---|---|---|
| `sector_score.py` | evening-market-recap / morning-market-brief / weekend-saturday-recap / weekend-sunday-preview / +2 | **有效**：板块热度分层 HEAT_4 20d 超额 +2.27%、40d +5.29% | Tier1 Gate a/b/c/c'；输出 41 板块评分 |
| `sector_picks.py` | 同上 4 个 + 报告复用 | **有效**：`action × channel` 三通道（VALUE / TREND / REVERSAL） | 2026-09-10 加 `REVERSAL` 左向通道（三闸：板块分 < 46 + pos120 ≤ 30 + 距 120 日高点 ≤ −40%） |
| `evening_recap_data.sh` | evening-market-recap / morning-market-brief | — | 2026-09-10 新增**反转配额**（`EVENING_RECAP_REVERSAL_QUOTA`，默认 6） |
| `rec_log.py` | daily-rec-verify / evening-market-recap / morning-market-brief / +2 | — | 推荐状态流（append-only），唯一有 T+N 后验证闭环的路径 |
| `rec_watchdog.py` | daily-rec-verify / weekend-saturday-recap | — | 只看每只票**最新**状态，EXIT/SELL 后不再对旧 BUY 重复报警 |
| `watchlist_sync.py` | evening-market-recap / morning-market-brief | — | 2026-09-10 改为按 `action × channel` 解析；REVERSAL 允许 COLD 板块 |
| `watchlist_decay.py` | watchlist-decay-weekly | — | 只评 `tier == 观察池` |
| `htsc_raw_data.py` | evening-market-recap / morning-market-brief / weekend-sunday-preview | — | HTSC 半结构化 raw 层（候选补充，不得绕过 Framework 直接 BUY） |
| `htsc_skill_bridge.py` | evening-market-recap / morning-market-brief / narrative-prefilter-daily / +1 | — | HTSC skill 桥 |
| `htsc_sector_flow.py` | weekend-saturday-recap | — | 板块资金流（首选 HTSC，THS 兜底） |
| `thesis_enrich_daily.py` | thesis-enrich-daily | — | 每工作日 append `update_log`（技术面 + stop_loss 三层检查），silent |
| `thesis_review.py` | thesis-weekly-review | — | 周度 pillar scorecard |
| `thesis_bootstrap.py` | thesis-weekly-bootstrap | — | 缺 thesis 的新 watchlist 股自动建档 |
| `narrative_prefilter.sh` | narrative-prefilter-daily | — | 三源快讯预过滤 |
| `narrative_radar.py` | narrative-radar-daily / narrative-picker-weekly | — | score≥2 事件入库 |
| `narrative_track.py` | narrative-track-daily / narrative-track-weekly-doc | 探索性（exact-D14 hit 54.2% / strict 35.4%，95% CI 宽） | 禁止宣称稳定 alpha |

---

## 2. 人跑链路 — `ACTIVE(人跑)`

| 组件 | 谁跑 | 备注 |
|---|---|---|
| `daily.sh` | 人（README：每日开盘前 1h） | 6 段简报。**设计原则**：不发基于成本价的 P&L 告警（工具不控盘） |
| `diligence.sh` | 人 / agent 按需 | 一键跑 tier 2/3 全部工具 |
| `compare.sh` / `fundamentals.sh` / `flows.sh` / `history.sh` / `quote.sh` / `policy.sh` | 人 / agent 按需 | 投研 flow 工具箱 |
| `concepts.sh` | 人 / agent 按需 | 主题轮动 |
| `fina_sync.py` | 人（预热） | 批量预热全市场 `fina_indicator` parquet，`grading.py::fundamentals_adj` 的依赖 |
| `migrate_cache.sh` / `cache_parquet.py` | 人（迁移） | parquet 缓存迁移 |
| ⚠️ `screen.sh` | **无人消费** | 见 §7 缺口 1 |

---

## 3. 库模块 — `LIB`

| 组件 | 被谁 import |
|---|---|
| `tushare.py` | 34 个脚本（数据总入口） |
| `concepts_data.py` | 12 个脚本（concepts 主题表，**固定 14 主题，不要擅自改**） |
| `quote_sources.py` | `narrative_track` / `rec_log` / `sector_picks` / `thesis_enrich_daily` |
| `sector_picks_block_trade.py` | `sector_picks` / `news_score_phase1` / `news_score_phase2_block` |
| `grading.py` | `screen.sh`（+ `cache_parquet`）—— **无 cron 消费者**，见 §6 |
| `signals.py` | `daily.sh`（人跑）+ `thesis_enrich_daily`（cron） |
| `sector_health.py` | `sector_score`（heat_score 1-4） |
| `etf_data.py` | `sector_picks` / `sector_score` |
| `akshare_fallback.py` | `tushare` |
| `watchlist_data.py` | `daily.sh`（watchlist.yaml 的读取 shim） |
| `watchlist_ops.py` | watchlist 提案审批（`apply_proposal`） |
| `cls_telegraph_filter.py` | `narrative_prefilter.sh` |
| `narrative_sector_bench.py` | `narrative_track` / `backtest_event_ingest` |
| `news_score_phase1.py` | `sector_score`（+ 3 个回测） |

---

## 4. 研究 / 回测工具 — `RESEARCH`

不面向生产，**不需要消费者**。保留，因为它们是"某条结论为什么被删"的唯一证据。

| 组件 | 用途 |
|---|---|
| `backtest_component_audit.py` | **本台账的主证据**：全量横截面审计（入场侧口径） |
| `backtest_leftside_reversal.py` | 左侧反转通道专项回测（n=198：20d 超额 +4.13% / 胜率 60.1%） |
| `backtest_elastic_net.py` / `backtest_moneyflow.py` / `backtest_score_full.py` / `backtest_sector_v3.py` / `backtest_phase_signal.py` | 历史因子 / 评分体系探索 |
| `backtest_news_phase1.py` / `backtest_news_phase2_block.py` / `backtest_news_phase2_unlock.py` | 新闻评分分阶段验证 |
| `backtest_event_ingest.py` / `narrative_backtest.py` / `narrative_backtest_p2.py` / `narrative_feature_tag.py` / `narrative_label_event.py` | narrative 事件回测栈（P2 仍是 shadow，见 `CONTEXT.md` 第 10 条） |
| `migrate_cache.sh` | 缓存迁移（一次性） |

---

## 5. 已退役 — `RETIRED`（`archive/`）

| 组件 | 判据 1 无消费者 | 判据 2 回测 | 判据 3 替代路径 |
|---|---|---|---|
| `funnel.sh` | ✅ 44 个 cron 0 引用 | ❌ 反向：A +3.45% > B +0.17% > D +0.50% > C −0.68% → 分级无可用序 | ⚠️ 原写 screen.sh 承担，**实证不成立**（见 §7） |
| `momentum.sh` | ✅ 0 引用 | ❌ 同上（共用 `grading.py`） | ✅ `sector_picks.py` 的 `TREND` 通道（R_momentum20 20d 超额 +2.79%） |
| `backtest.sh` | ✅ 单票重放工具，`signals.py` 只剩描述性标记后失去对象 | — | ✅ `backtest_component_audit.py` + `backtest_leftside_reversal.py` |
| `signal_collector.py` | ✅ 唯一消费者是它自己的两个回测脚本 | 从未进入推荐路径 | ✅ 7 维短线信号无一进入生产 |
| `backtest_signals.py` | ✅ 只服务 `signal_collector.py` | — | — |
| `backtest_signals_v2.py` | ✅ 同上 | — | — |

---

## 6. 待决 — `PENDING`（**先不动**）

| 组件 | 现状 | 待决原因 |
|---|---|---|
| `news_score_phase2_unlock.py` | 无消费者 | 有自身回测证据但**尚未复核** |
| `htsc_portfolio_watchlist.py` | 全仓库零引用 | 用途待确认（疑似 HTSC 自选同步早期版本） |
| `narrative_backfill_pubdate.py` | 零引用 | 一次性回填工具，可能已完成使命 |
| `build_backtest_dataset.py` / `backtest_signal_compare.py` | 零引用 | 早期研究脚手架 |
| `grading.py` | 只服务 `screen.sh`，而 `screen.sh` 无人消费 | 去留取决于缺口 1 的决策 |
| `signals.py` | `daily.sh`（人跑）+ `thesis_enrich_daily`（cron） | 生产仍在用，**不是**待决，放在此处仅为提示它已无任何行动型信号 |

---

## 7. 已知缺口

**缺口 1 —— 「全市场基础仓候选」这条能力实际空缺。**
`screen.sh` 存在、能跑、< 15s，但**没有接入任何 cron**：2026-09-10 用 63 个
cron 的 prompt 快照逐条 grep，`screen.sh` / `daily.sh` / `grading` 全部 0 命中。
`archive/README.md` 里 `funnel.sh` 那行写的"改由 `screen.sh` 接入周日展望承担"
经核实**不成立** —— `weekend-sunday-preview` 的 prompt 只调
`sector_score.py` / `sector_picks.py` / `htsc_raw_data.py` / `htsc_skill_bridge.py` / `rec_log.py`。
影响：目前没有任何组件做"**全市场**扫一遍找基础仓候选"，生产链路是
**板块驱动**的（`sector_score` → `sector_picks`）。
处置选项：(a) 把 `screen.sh` 真接进周日展望；(b) 明确接受板块驱动、
删掉 `screen.sh` + `grading.py`；(c) 重写一个基于回测有效因子的全市场筛。
**未决策前不改代码。**

**缺口 2 —— exit-side（卖出）完全没有回测。**
本台账所有证据都是入场侧口径（"此后 20d 收益"），回答不了"持仓中触发卖出
vs 继续持有哪个好"。当前卖出侧只有三套非技术信号机制：thesis `stop_loss`
三层（38/41 只票为 `现价 -15% → 砍 1/3`）、推荐流水线 `EXIT/SELL` 状态迁移、
以及唯一残留的技术面标记 `SIG_TOP_EXTREME`（n=26，20d 超额 −3.38%）。
补法见 §8 待办。

**缺口 3 —— README 文档漂移。**
`README.md` 第 130-140 行仍列着 `🚨 STOP_LOSS / ✅ TAKE_PROFIT /
⚠️ REDUCE(1W>+15% → 减 30%) / 💡 ADD` 四行告警表，但代码侧**早已删除**
（`daily.sh` 头注释："工具不控盘，不发基于成本价的 P&L 告警"）。
其中 `REDUCE` 与本次被毙的 `SELL_EXHAUSTION` 同族（都基于 1W 涨幅阈值），
回测证据是**反的** → 该表应删或标注为历史。

---

## 8. 2026-09-10 审计关键证据（数字索引）

基准：`BASELINE_ALL` n=15788，20d 绝对 +2.20%，超额胜率 40.9%。

### 判死（已删）

| 规则 | n | 20d 超额 | 20d 胜率 | 结论 |
|---|---|---|---|---|
| `BUY_EARLY`（量比 ≥2x + 1W∈[−3,+5]） | 298 | **−1.74%** | 32.0% | 负 alpha |
| `BUY_BREAKOUT`（pos120 ≥85 + 量比 ≥1.5） | 365（真实口径） | +0.37% | 40.8% | 零 alpha；生产 proxy 口径 n=6（事实上从不触发） |
| `SELL_EXHAUSTION`（pos>85 + 1W>15） | 128 | **+4.03%** | 50.0% | **方向相反**：之后继续持有更赚 |
| `SELL_CONFIRMED`（pos>85 + 1W>25） | 52 | **+0.63%** | 44.2% | 同上 |
| `SELL_TOP`（3M>+100 + 1W<−10 + 量比>1.5） | **0** | — | — | 15788 样本零触发 → 不可测 = 不可信 |
| `GRADE_*` A/B/C/D | — | A +3.45 / B +0.17 / D +0.50 / C −0.68 | — | **非单调**，D 优于 C → 分级无可用序 |

### 判活（保留 / 加强）

| 因子 | n | 20d 超额 | 20d 胜率 | 备注 |
|---|---|---|---|---|
| `HEAT_4`（板块最热档） | 1826 | **+2.27%** | 45.8% | 40d +5.29%；HEAT_2 反而 −0.89% |
| `GRADE_*_A`（仅 A 档） | 537 / 492 | +3.45% / +3.82% | 47.7% / 48.2% | 只有 A 档，B/C/D 无单调序 |
| `L_deep_dd40`（距 120 日高点 ≤ −40%） | 291 | **+2.96%** | 56.4% | 左侧反转的有效内核 |
| `L_low30`（pos120 ≤ 30，仅位置低） | 4828 | **−0.76%** | 40.8% | 反例：只有"跌透"有 alpha，"位置低"没有 |
| `SIG_TOP_EXTREME`（1W>+35% 且 1M>+28%） | 26 | −3.38% | 33.3% | 唯一残留的技术面顶部证据；样本太小，仅描述 |
| `REL_+2`（相对强度最强） | 434 | +1.96% | 46.8% | 与 `REL_-2`(+2.10%) 构成 **U 型**；中间 `REL_0` −0.44% |

---

## 9. 待办

1. **exit-side 回测**（缺口 2）：`backtest_exit_rules.py`，同一 15788 样本框架
   + 持仓状态机，label 换成"触发卖出规则 vs 继续持有"。候选规则：
   板块热度掉档（HEAT_4 → 更低）、相对强度衰减（REL_+2 → ≤0）、
   趋势破位（跌破 MA20/MA60 + 量能确认）、止损参数化（−10/−15/−20 砍 1/3）。
2. **缺口 1 的处置决策**（`screen.sh` 去留）。
3. **README §告警表**（缺口 3）删除或标注为历史。
