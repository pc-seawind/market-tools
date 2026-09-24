# R2：默认入口自动恢复与 pending 输入契约（待独立复验）

工单 work_61180bc0afd04c2148c0；本轮仅闭合原验收提出的两项必需缺口，不重跑华泰或市场收费探针。
代码：market-tools fb936c1；执行说明：investment cadc86d。均未 push。

## 1. 默认入口确实此前没有自动恢复，现在已接通

只读核验 gateway `evening-market-recap.json`，其 prompt 引用 home-ubuntu 上的
`investment/reference/operations/weekend-loop-20260912/engine-contracts/evening.md`。
该文件的默认命令是 `python3 .../mt1_job.py run evening --collect`。
实际路径为 `mt1_job.py → mt1.py main → mt1.pipeline.run → recap_stage`。
此前这里只有一次 collector 调用，旧真实恢复试验的确是人工再次启动，不能作为自动自愈证据。

现有入口接入 `mt1/recap_collection.py`，不增加 cron：
- 同一次 CLI 调用内，最多调用两轮原 collector。
- 共用一个 monotonic deadline，默认总预算仍 2280 秒；第二轮 env budget 和进程 timeout 只取剩余秒数。
- 同输入成功 checkpoint 由原 collector 复用；失败项受持久化次数约束，不重复成功 A，只重试 B。
- 完成就停；预算耗尽、两次上限、锁冲突、缺 checkpoint、无效时点等均有明确终态。
- `runs/<run_id>/recap-recovery.json` 和 `report.raw_recap.recovery` 返回实际轮数、预算、coverage、owner、next_action 与可执行 inspect_command。
- 终态 `automatic_retry_pending=false`；明确由 code:recap-collector 先检查 checkpoint/RPC trace，再决定修复，不承诺无人触发的后续恢复，不擅自重置次数/再开预算。
- coverage 和 collector sector_attempts 两处都不再在达到上限后泛称 resume_failed_only。成功部分仍独立消费；主日报继续原流程。

## 2. 默认生产路径的运行回执（不伪称自然行情实跑）

为不重复收费采集，使用本机真实 CLI、pipeline、shell、独立子进程及真实 checkpoint IO，**仅 provider 和日历换成明确标记的离线 fixture**。
两种场景各只调用一次 `mt1.py main(run evening --collect)`，不是手动启动 collector 两次。
这是默认生产代码分支（fixture=None）的控制流集成验证，不是休市日自然市场执行或投递证明。

|场景|CLI 启动次数|collector 自动调用次数|provider 请求顺序|消费者终态|
|---|---:|---:|---|---|
|B 首次失败、第二次成功|1|2|A、B、B|complete；A/B 都消费，A 复用|
|B 持续失败|1|2|A、B、B|attempt_limit；只消费 A，B 有明确 owner/检查命令|

另外的共享预算回归模拟第一轮耗尽全部时间，确认不会启动第二轮，终态 budget_exhausted；历史日期重采明确拒绝。
原有冷缓存 210 秒超时→128.943 秒成功等真实市场探针沿用上一轮，不重复执行。

回执：summary.json、default-recovered/state/runs/default-entry-offline/{report,recap-recovery}.json、default-terminal 同路径。
验证范围与零市场/华泰/发布调用声明：validation-scope.json。现场默认 cron 引用证据：default-production-entry.json。

## 3. research.review_items 是 schema 错误，已最小修复

采用**明确拒绝**，不自动搬运或猜测研究结论：
- finalize 在验证 source / 打开 Store 之前拒绝 `research.review_items`，包括空嵌套和两个层级同时存在。
- 错误 `review_items_wrong_level` 指示：改为顶层 bundle.review_items，按当前 plans 绑定 plan_id、整数 expected_version，填写 pending/note，删除嵌套键，不可加入 reviewed_plan_ids。
- 顶层结构必须为对象列表，plan_id 唯一非空，expected_version 必须为整数；既有 scope、实际版本、pending/已审互斥门禁保持。
- 更新 docs/MT-1.0.md、生成模板 mt1_deploy_cron.py，以及实际被现有任务读取的 investment 四份 engine-contracts。
- 四份 engine-contracts 原本尚未纳入 git，本轮首次纳入版本管理；只新增 pending 约束（晚间额外新增自动恢复说明），其余既有正文保留；没有修改 gateway cron。

## 4. 真实历史错形回放与 pending 可见消费

历史源：`.cron_state/mt1/chains/2026-09-24-morning/review-*.json` 的真实 bundle；report 的 phase/run_id/plans 原样投影为可携带 fixture。源文件 hash 和投影说明位于 tests/fixtures/recap-followup/source-manifest.json。

- 原错形：拒绝且尚未创建 state/investment 目录，无账本副作用。
- 错层级与顶层同时出现：同样明确拒绝，不静默择一。
- 隔离 ledger 回放：使用历史九计划快照，按隔离 ledger 中实际版本绑定 corrected-bundle，仅修 pending 结构。
- finalize 实际结果：pending_review_ids=9，reviewed_plan_ids=[]，unreviewed=9，research_status=not_verified，changes=[]。
- 生成正文保留“覆盖 0/9”，九行“待复核：完整财务/估值/经营传导未签审”均可见；逐计划快照前后完全相同。
- 没有修改生产 bundle、账本、研究签审或发布旧结果。

回执：schema-negative-receipt.json、pending-replay/{corrected-bundle,consumer-receipt}.json，以及 pending-replay/investment/reference/medium-term-reviews 中的实际 finalize 正文。
历史源 hash 再核对：originals-unchanged.json。

## 5. 回归与交付范围

`/home/emox/work/homespace/.venv/bin/python -m pytest`：最终 **172 passed**，详见 regression-final.txt。
包含上一轮 167 项与本轮默认入口成功/终态、共享预算、真实历史错形拒绝、pending 可见及账本不变回放。
初次合跑暴露 CLI 测试写 MT1_TRACKING_SCOPE 未恢复的测试隔离问题；已由 monkeypatch 注册并恢复该环境变量，未改生产 scope 门禁，最终合跑通过。

本单必需缺口已闭合，`incomplete=[]`，交原 topic 独立复验，不自行宣告验收通过。
全量 21 板块、费用金额未知、无历史逐 RPC trace、研究结论 0/9、整周历史缺口及自然后续交易日观察继续作为既有边界，不扩大为本单新增必需工程；不宣称这些已完成。
