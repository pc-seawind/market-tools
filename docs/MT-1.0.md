# MT-1.0 工程交付与运行手册

2026-09-10。策略规范来自 investment/reference/medium-term-recommendation-policy.md。
**不是完整策略上线声明：工程安全与记录层启用，新质量价值因子留 shadow；精确三通道历史回测仍未完成。**

## 当前边界

|能力|交付状态|不能据此宣称|
|---|---|---|
|CN/HK/US交易日门禁|已实现并现场取数验收；失败关闭|不是实际交易执行引擎|
|早盘/晚盘/周六/周日入口|数据编排、断点、错误隔离、记录已实现|四种真实定时触发/最终飞书交付尚未全部现场验收|
|结构化计划|SQLite append-only事件、版本CAS、请求幂等、独立持有方向|研究状态不是成交；迁移不是历史建仓重建|
|旧数据迁移|原rec逐行保留，thesis保存原文快照，缺失项未知|历史理由里的“浮盈”不成为已确认个人盈亏|
|自选过滤|原始from-recap关闭；最终计划筛选、dry-run已验|本次没有实际HTSC写入，未验真写入回执|
|全市场质量价值|全市场股票列表采集，无HOT过滤；逐股财报可续跑|先8只、部署后100只小批量验收，非全量覆盖；新参数不替换生产评分|
|20/40/60回测|信号带复放、配对退出/持有、组合指标、PIT资料门禁|不从历史原始数据重算真实三通道，不能称精确策略收益|
|方法注册表|candidate→shadow→validated→active、回退降级、证据门禁|没有自动证明来源真实性/策略有效；不自动改代码|
|周日研究|支持原文hash校验与方法注册；联网由现有agent工具完成|没抓取原文不能标已完成研究|

## 完整推荐逻辑

1. 候选有两路：现有 sector_picks 原样保留；新全市场质量价值研究池独立存在。
   前者的机器 BUY 不直接等于中期 BUY，后者目前全部 shadow。
2. 排除财务、流动性、重大事件风险，再检查生产技术/估值硬约束。未知不是通过。
3. 三通道：VALUE 要质量/估值以及未来1—3个月修复路径；TREND 要业绩/订单/景气证据；
   REVERSAL 要经营/供需边际改善，深跌只是候选入口。既有生产评分未在本次被新因子替换。
4. 补齐证据来源与日期、验证节点、价格条件、风险边界、失效条件、下次复核日。
   最终门禁校验结构完整性和明确审查结果，不代替研究员核查证据真实性。
5. 分开写未持有者 BUY/WATCH、已有持仓者 WATCH/HOLD/SELL/EXIT。
   holding_status 只认 unknown/not_held/confirmed；actual_cost 可空，不能从推荐价推断成本。
6. 原始日期、理由、参考价、原始期限不可改。HOLD延续原episode。
   延期须 extension_evidence；EXIT后重新入选必须另建plan_id/episode。
7. 退出要基于逻辑证伪、明确风险边界、估值/催化兑现或到期复核，不因板块冷、一天破线自动退出。
8. 早盘使用前一完成交易日数据；晚盘检查当日期待交易日。市场各用自己的日历。
   周末研究不受休市限制。周六复盘全部计划/退出记录，周日承接周六、联网、排下周验证节点。
9. 方法迭代先candidate/shadow；PIT、真实通道、20/40/60、独立样本和样本外不过关不升active。
   输入的artifact_hash/声明仍需人工审计，状态机不是第三方证明系统。

## 单命令入口

```bash
# 数据阶段：必须RunDetached或systemd-run；下例在独立cgroup执行
systemd-run --user --unit=mt1-evening-$(date +%s) \
  --property=RuntimeMaxSec=7200 \
  --working-directory=/home/emox/work/projects/market-tools \
  /usr/bin/python3 /home/emox/work/projects/market-tools/mt1_job.py \
  run evening --collect --max-stocks 100
# morning/saturday/sunday 同入口替换phase，不带--collect
# 同日重跑默认run_id会复用成功阶段，重试失败阶段；新研究批次使用新的--run-id
# 研究员写一份JSON证据包后，一条命令完成事件/差异/记录/自选dry-run
python3 mt1_job.py finalize --input /absolute/review-bundle.json
python3 mt1.py watchlist                 # 默认dry-run
# --execute 是显式第三方写入开关；本次未使用，不允许原始候选绕过
```

`mt1_job.py`只在内存中从当前worker继承必要Tushare/代理环境，不写密钥文件。
入口默认数据在 `.cron_state/mt1/`；manifest.json逐阶段running/done/failed，report.json是结果。
报告写 investment/reference/medium-term-reviews/，每次不同时间后缀，不覆盖历史。
初步run报告明确“待证据复核”；finalize的覆盖列表才说明哪些已审查。
各事件失败隔离；重跑用同一request_id和完全相同payload，幂等返回原结果。
未发生变化不要重新写HOLD事件，只在reviewed_plan_ids里记录覆盖。

旧rec保留历史，不双写：MT-1.0的新计划以plans.db为权威；旧rec绩效报表**不会自动包含新事件**。
这项兼容投影尚未实现，不能混报两套样本。thesis enrich继续更新原thesis，不自动覆盖计划证据。

## 输入契约

新计划必须包含以下字段，未知可以为null，但最终BUY不允许缺证据：

```json
{
  "plan_id": "CN-EXAMPLE-episode-1", "code": "EXAMPLE.SH", "name": "虚构示例",
  "market": "CN", "episode": "episode-1", "channel": "VALUE", "state": "WATCH",
  "original_reason": "仅示例：等待经营修复证据", "original_date": "2026-09-10",
  "reference_price": null, "original_deadline": "2026-12-09",
  "holding_status": "unknown", "actual_cost": null,
  "unheld_direction": "WATCH", "held_direction": "WATCH",
  "evidence": [], "milestones": [], "price_condition": null,
  "risk_boundary": null, "invalidation": null, "review_due": "2026-10-09",
  "qualification": "pending_review"
}
```

最终结论还要：`qualification=final`，`reviewer`，`method_status=active`，
`checks={financial,liquidity,major_event,technical,valuation: "pass"}`，
`evidence=[{url,date,claim}]`，`milestones=[{date,condition}]`，
`price_condition={below或above:正数,basis,source_url}`，
`risk_boundary={condition,source_url}`及invalidation；非空字符串“未知”不是价格条件。
新因子标`candidate_origin=quality_value_shadow`时，即使其他项齐全也不能成为最终合格BUY。
数值示例不写入生产、不构成真实报价建议。

review-bundle结构：

```json
{
  "phase": "evening", "reviewer": "研究员标识",
  "reviewed_plan_ids": [],
  "plan_events": [],
  "method_events": [],
  "research": {"fetch_status": "not_requested", "sources": []}
}
```

每个plan_event / method_event为
`{id,expected_version,request_id,reason,payload}`，新建expected_version=0。
更新payload只放变更字段。原有plan_id从`mt1.py plans`获取，不自行猜造。
方法payload见mt1/methods.py；研究原文引用为
`{url,fetched_at,content_path,content_hash}`，content_hash为原文文件SHA-256。
获取失败传`fetch_status=failed`，不能把搜索摘要占位当作完整原文。

## 日历与数据细节

- CN使用Tushare trade_cal(exchange=SSE)，不是星期判断，要求连续32个自然日日历记录。
- HK使用hk_tradecal，US使用us_tradecal并处理America/New_York DST。
- 港美日历接口不提供半日市收盘，使用17:00当地时间保守完成边界；半日市不提前认完成。
  例外延长交易/临时全市场停市需要额外日历更新；当前并非精确盘中交易时段模型。
- 早盘复用失败时明示缺口，**不调用只支持当日采集的旧晚盘脚本伪造昨日快照**。
- 全市场扫描抓取当前stock_basic，逐股fina_indicator显式请求ann_date。
  公告日期缺失/未来/同日无时刻均不用，日行情日期不符不用。缺失原因和覆盖率始终输出。
- qv-shadow-1参数为探索性：ROE>=10、正EPS与经营现金流、PE(0,25]、PB(0,3]、
  换手率>0、负债率<=70、非ST。行业（尤其银行）适用性尚未校准，不能称已验证质量因子。
- `--max-stocks 0`可扫全量，按日期目录缓存每股财报；中断后复用成功数据。
  生产试运行上限100只，只代表小批量接通，不能声称覆盖全部5561只。

## 回测契约与局限

`mt1.py backtest --input <JSON> --out <JSON>`接收显式signals/bars/sessions/benchmark。
先检查11项provenance，包括PIT财务/行业、历史成分、退市、归档真实通道信号、
下一可成交价、复权、停牌涨跌停、费用、基准和样本外切分。缺一项返回unsupported、metrics=null。

可复放的输入只是一份**已归档信号带**：下一可买开盘入场，下一可卖开盘退出，
显式不可成交日顺延，缺行情不能静默跳过；退市须明确结算价，否则遗漏并报告原因。
固定等额独立资金槽、重叠样本占不同资金槽；退出现金不再投资。
输出20/40/60收益、基准超额、日频组合最大回撤、逐样本尾部CVaR、双边总换手、资金占用，
同入场退出vs持有配对差。不是现金重投资优化组合，也没有完成自动滚动样本外统计。

**当前真实数据不满足精确复刻条件，没有MT-1.0历史收益数字可以报告。**
旧回测脚本包含当前成分/代理口径，不能填入“精确收益”。
机器可读验收缺口见MT-1.0-backtest-readiness.json。未来先补数据和通道重算器，再做样本外验收。

## 运维与回退

`mt1_deploy_cron.py`默认dry-run展示四个核心报告新prompt，`--apply`才更新gateway。
更新前备份、逐文件并发内容比较，回读验证除prompt外所有字段不变；不新增cron/不改调度。
仅home-ubuntu工作区现场验收；其他worker需要安装同一commit，未验不得宣称全部worker上线。
回退先读gateway备份与当前prompt差异，不能覆盖之后的修改；不要删除plans.db或历史报告。
新因子始终shadow，代码回退也不需要回滚生产选股阈值。恢复原始候选自动自选不是默认回退动作。

## 官方接口依据（2026-09-10核验）

- [A股日历](https://tushare.pro/document/2?doc_id=26)
- [港股日历](https://tushare.pro/document/2?doc_id=250)
- [美股日历](https://tushare.pro/document/2?doc_id=253)


## 实际交付验收

- 全部测试：48 passed（新增MT-1.0测试22项，原有测试26项）。
- 先8只shadow，后按gateway晚盘prompt的真实命令路径采集100只。
- CN/HK期望2026-09-10，美股2026-09-09；64个旧标的计划迁移，原记录未修改。
- 四个核心cron已备份、改prompt并回读确认其他字段完全未变；未新增报告任务。
- 最终自选dry-run为空，没有实际第三方写入，没有执行交易。
- 逐项数字、备份目录和未验范围见MT-1.0-acceptance.json。
