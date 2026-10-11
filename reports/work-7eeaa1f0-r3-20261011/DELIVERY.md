# revision 3：正式签审入口交接支持

只补独立验收明确要求的入口契约与隔离端到端证据；revision2五项成果不重写。

## 新增实测

|阶段|实际执行与断言|
|---|---|
|研究准入|真实 research-event CLI，合成临时 scope，watch；scope 原 bytes 不变|
|首次签审|真实 finalize CLI → Store.apply → reduce_plan，final WATCH version=1；非BUY|
|重试|相同 finalize 输入，版本仍为1，账本不重复追加|
|研究更新|真实 research-event 写第二版本，旧 plans 读取为空，旧 finalize 返回 plan_outside_tracking_scope|
|重新签审|新绑定 research_event_id=research-v2、expected_version=1，真实 finalize 返回 version=2|
|daily|真实 track 函数，唯一行情 provider seam 使用隔离合成数据，当前 research-v2、not_held|
|周末正文|真实 run saturday/sunday CLI 使用fixture，report.json及中文正文包含当前version=2的WATCH计划|
|账本核对|计划事件总数恰好2，无BUY、自选pending为空，不修改方法账本|

**这些是合成隔离工程证据，不是公司研究、真实生产签审或自然执行回执。**
生产 daily 不依赖 formal plans 才能监控 watch；本测试同时验证研究投影的daily消费与正式计划的周末消费，
不把daily伪装成持仓/收益或交易执行器。

完整命令、stdout、stderr、输入、签审输出、中文报告及哈希在 `SYNTHETIC-cli-handoff/receipt.json`。
模板与字段说明：`docs/research-plan-handoff.md`、`examples/research-*.template.json`。
模板中占位符必须由真实研究者填写，不能直接执行，更不能复制合成来源入生产。

## 测试与执行边界

本轮30项+18子测试通过（0.81秒）。仅新增本地串行端到端状态链及小范围回归，
状态账本和发布协调阶段按正确性要求串行；不重新启动revision2已成功Ubuntu/WSL测试、全量采集或GPU工作。
继续保留两机既有真实回执，5090入口不明不构成本轮重复计算理由。无parallel assistant。

修正测试脚本读取run CLI输出时的解析方式：run除了JSON摘要还带RESULT_JSON行，
使用JSONDecoder读取首对象，并读取实际report.json验证正文，不能把摘要当完整计划。

## 自然证据待验

本轮18:20左右北京时，既有本周末调度时点已经过去且早于本轮补充；
只读检查本地run索引见 `natural-pending.json`，不声称它能证明gateway或飞书状态。
没有新建cron、改时间、补跑、重部署或重启gateway。
下一次既有自然任务后仍需原topic只读关联dispatch、实际消费正文与delivery/readback。

10只公司研究、9持仓复核、真实入池签审与最终发布仍由investment负责，
生产无推荐或收益未成熟是合理状态，不伪造样本。总体保持pending，不宣告整体通过。
