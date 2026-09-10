# MT-1.1 Revision 2 真实交付（2026-09-11）

本轮修复独立验收P1/P2，并补真实研究输入—有效时间—条件动作—原计划绑定—报告闭环。不是自行验收通过或策略有效性声明。

## 修复与边界
- 行情日期与决策时间分离：当天早盘审昨行情合法；隔夜公告按精确decision_at判定，不倒填reviewed_at。日期不明精度保守处理，naive时间、未来1微秒、过期或错误时区拒绝。
- near_MA20_low_volume仅均线附近缩量诊断，不声称真正回调承接结构；阈值未调整，版本shadow-2。
- 宽泛发现4714只保留discovery-pool；452只风险否决从可推进队列移除；4262只仍是待研究，不是可买池；本批工作量限定10只，不为指定股票改阈值。
- 技术trigger470=54风险reject+416风险unknown，**全部不是可买池**；研究未齐与仅待方法批准分开，当前后者为0。
- 新大陆通道统一从实际产物读取：VALUE/TREND/REVERSAL，修正v1交付摘要遗漏。

## 真实时间与研究材料闭环
价格日：20260910；决策：2026-09-10T21:34:53.551022+00:00；计算代码：44a6c8b884f5e29be43c8703f03d4b8844c47504。
真实输入来自投资域已有first2研究文件，代码只做格式/时间/hash验证；未重新声称公司事实已签审，也未伪造primary PDF。

### 002463.SZ｜当前发现通道：未进入发现
绑定：legacy-0776df654a268bc35a1d21e8 v1；持仓unknown；原期限unknown（未覆盖）；动作：持仓未确认，不能生成实盘持有动作。
TREND evidence：partial；时间链 `{"price_asof": "2026-09-10", "decision_at": "2026-09-10T21:34:53.551022+00:00", "reviewed_at": "2026-09-10T21:32:12.927227+00:00", "valid_until": "2026-09-18T15:59:59.999999+00:00", "review_precision": "instant"}`
已接入事实（原投资域部分研究）：{"period": "2026H1", "revenue_yoy_pct": 61.17, "attributable_profit_yoy_pct": 73.72, "adjusted_attributable_profit_yoy_pct": 70.0, "operating_cashflow_yoy_pct": -45.14}
仍需：应收存货与经营现金流下降原因；泰国扩产盈利可持续性；9月7日调研全文和后续公告；最新价格与盈利情景估值
未持有条件：未进入当前发现，不自动放行

### 600346.SH｜当前发现通道：VALUE,REVERSAL
绑定：legacy-072ea033668e78be6c6874bf v1；持仓unknown；原期限unknown（未覆盖）；动作：持仓未确认，不能生成实盘持有动作。
VALUE evidence：partial；时间链 `{"price_asof": "2026-09-10", "decision_at": "2026-09-10T21:34:53.551022+00:00", "reviewed_at": "2026-09-10T21:32:12.927227+00:00", "valid_until": "2026-09-18T15:59:59.999999+00:00", "review_precision": "instant"}`
已接入事实（原投资域部分研究）：{"period": "2026H1", "revenue_yoy_pct": -5.45, "attributable_profit_yoy_pct": 136.25, "adjusted_attributable_profit_yoy_pct": 132.35, "operating_cashflow_yoy_pct": -82.03}
仍需：现金流下降的营运资本及其他支付拆解；非经常损益来源与持续性；最新价格及正常化估值；半年报后重大公告与三季度经营数据
REVERSAL evidence：partial；时间链 `{"price_asof": "2026-09-10", "decision_at": "2026-09-10T21:34:53.551022+00:00", "reviewed_at": "2026-09-10T21:32:12.927227+00:00", "valid_until": "2026-09-18T15:59:59.999999+00:00", "review_precision": "instant"}`
已接入事实（原投资域部分研究）：{"period": "2026H1", "revenue_yoy_pct": -5.45, "attributable_profit_yoy_pct": 136.25, "adjusted_attributable_profit_yoy_pct": 132.35, "operating_cashflow_yoy_pct": -82.03}
仍需：现金流下降的营运资本及其他支付拆解；非经常损益来源与持续性；最新价格及正常化估值；半年报后重大公告与三季度经营数据
未持有条件：风险否决，不推进新买研究

恒力石化：VALUE/REVERSAL，partial经营材料已接入，但非金融负债shadow门禁仍拒绝，所以不在可推进队列。沪电股份：TREND部分研究已接入并绑定原计划，但当前价格发现未入选，不能用故事强行保送。
实际冻结了63个证券计划（含未进当前A股发现池的既有证券），没有任何confirmed holding；真实持仓与原期限缺失保留unknown。确认持仓/到期/原EXIT/版本冲突的行为由合成单测覆盖，不伪造实盘例子。

## 本批十只工作队列（不是推荐）
|代码/名称|全部通道|未持有方向|剩余阻塞|
|---|---|---|---|
|002396.SZ 星网锐捷|TREND|研究未完成|{"TREND": ["risk:unknown:numeric_risk_checks_pass_major_events_tradability_review_pending", "evidence:pending:营收/盈利/订单持续性待原文核验；同比负基数不可当增速", "valuation:pending:earnings_scenarios_peer_comparison_price_justification_required"]}|
|300677.SZ 英科医疗|TREND|研究未完成|{"TREND": ["risk:unknown:numeric_risk_checks_pass_major_events_tradability_review_pending", "evidence:pending:营收/盈利/订单持续性待原文核验；同比负基数不可当增速", "valuation:pending:earnings_scenarios_peer_comparison_price_justification_required"]}|
|000526.SZ 学大教育|TREND|研究未完成|{"TREND": ["risk:unknown:numeric_risk_checks_pass_major_events_tradability_review_pending", "evidence:pending:营收/盈利/订单持续性待原文核验；同比负基数不可当增速", "valuation:pending:earnings_scenarios_peer_comparison_price_justification_required"]}|
|603012.SH 创力集团|TREND|研究未完成|{"TREND": ["risk:unknown:numeric_risk_checks_pass_major_events_tradability_review_pending", "evidence:pending:营收/盈利/订单持续性待原文核验；同比负基数不可当增速", "valuation:pending:earnings_scenarios_peer_comparison_price_justification_required"]}|
|002479.SZ 富春环保|TREND|研究未完成|{"TREND": ["risk:unknown:numeric_risk_checks_pass_major_events_tradability_review_pending", "evidence:pending:营收/盈利/订单持续性待原文核验；同比负基数不可当增速", "valuation:pending:earnings_scenarios_peer_comparison_price_justification_required"]}|
|600547.SH 山东黄金|TREND|研究未完成|{"TREND": ["risk:unknown:numeric_risk_checks_pass_major_events_tradability_review_pending", "evidence:pending:营收/盈利/订单持续性待原文核验；同比负基数不可当增速", "valuation:pending:earnings_scenarios_peer_comparison_price_justification_required"]}|
|920403.BJ 康农种业|TREND|研究未完成|{"TREND": ["risk:unknown:numeric_risk_checks_pass_major_events_tradability_review_pending", "evidence:pending:营收/盈利/订单持续性待原文核验；同比负基数不可当增速", "valuation:pending:earnings_scenarios_peer_comparison_price_justification_required"]}|
|000676.SZ 智度股份|TREND|研究未完成|{"TREND": ["risk:unknown:numeric_risk_checks_pass_major_events_tradability_review_pending", "evidence:pending:营收/盈利/订单持续性待原文核验；同比负基数不可当增速", "valuation:pending:earnings_scenarios_peer_comparison_price_justification_required"]}|
|001872.SZ 招商港口|TREND|研究未完成|{"TREND": ["risk:unknown:numeric_risk_checks_pass_major_events_tradability_review_pending", "evidence:pending:营收/盈利/订单持续性待原文核验；同比负基数不可当增速", "valuation:pending:earnings_scenarios_peer_comparison_price_justification_required"]}|
|002517.SZ 恺英网络|TREND|研究未完成|{"TREND": ["risk:unknown:numeric_risk_checks_pass_major_events_tradability_review_pending", "evidence:pending:营收/盈利/订单持续性待原文核验；同比负基数不可当增速", "valuation:pending:earnings_scenarios_peer_comparison_price_justification_required"]}|

## 生产接入与留证
现有morning/evening/saturday/sunday四cron只追加MT11 shadow标记块，既有prompt其余内容保留；备份+CAS+二次独立回读，cron/domain/cwd/delivery/其他元字段均未改变；没有新增cron。
VPS备份：`/home/emox/.homespace/cron/_mt11-sidecar-backup-20260911-053453164333`。
真实手动生产入口：`mt1_job.py parallel-cycle morning`，独立cgroup `mt11-r2-final-cycle-20260911`，3900秒硬限，exit0；复用原真实panel，不重复242请求。
**尚未取得下一次自然cron触发/飞书投递回执**；本次手动CLI真实链路不能冒充自然调度全链路验收。

## 测试与产物
262 tests passed（包含原195项）；独立审计包含5561股/16683记录/4714去重/四hash以及63个原计划字段绑定。
正式本轮目录：`/home/emox/work/projects/market-tools/.cron_state/mt1/parallel-runs/20260910T213453549663Z-morning`。
- cycle.json/report.md：本轮真实运行和全流程
- bound-review.json/.md、bound-ledger-snapshot.json：研究包→时间门禁→原计划/版本/期限绑定→条件观察报告
- review-input.json、review-sources/<hash>：真实partial包及原文档冻结
- research-batch.jsonl：十只交接；research-queue.jsonl：4262只待研究；discovery-pool.jsonl：含风险拒绝诊断的4714宽池
- stages.csv/funnel.json.gz/summary.json：全量记录、交叉表、源日期/hash/代码hash
- .cron_state/mt1/mt11-r2-{audit,cron-deployment,cron-readback}.json：独立审计与生产配置证据

## 未完成分类
- 公司研究仍由投资域完成：风险/估值/催化材料不足，不制造pass/final；本轮2家真实材料为partial，不是完整经营签审。
- 真实持仓确认与原期限重建缺输入，绑定能力已实现，但实际账本仍unknown；观察接口只读，不擅自改权威计划或实盘仓位。
- 下一次自然cron与投递链回执尚待发生，不能以手动CLI代替。
- 20/40/60自动收割**功能仍未实现**；观测窗口也尚未成熟；不把两者混为一谈。
- 新方法有效性、完整行业定量估值、真实回调历史路径识别仍未完成；旧2024主题缺档独立incomplete，不重启旧采集循环。
- 未交易、未写自选、未扩权或部署其他worker、未push；原3个未跟踪文件未动。
