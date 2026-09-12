# MT14 实施进度（开发记录，不是投资验收）

工单 `work_5d6320876daa43b70a3b`。串行执行，无子 agent，无 push/定时部署/实盘。

|ID|状态|实现与证据|
|---|---|---|
|01|completed|HEAD fc0a8a7；47,165 文件保护基线；原全量 574 passed；a26e931|
|02|completed|参数白名单、不可变费用/成熟度/风险合同；f5ab4c1|
|03|completed|隔离根、事件、锁、幂等阶段；ed6a87e|
|04|completed|sweep 原字节/hash/财报日期/范围；c80fbd9|
|05|completed|CN/HK 原始价格/因子重构，技术与执行分离；c80fbd9、d4aa673|
|06|completed|真实 screen 原版/候选计算；abebda4；成熟后逐代收紧 d23b6b7|
|07|completed|原 MT13 observe/register_policy 分账；旧仓继续退出；abebda4、d23b6b7|
|08|completed|60 日主窗、20/40诊断、来源重算、去重/重叠拦截；98ab65c|
|09|completed|固定资金槽/成本/范围/时段，净改善及尾部/回撤；98ab65c|
|10|completed|实验指针原子切换、证据前缀绑定、prepare 恢复、风险/篡改回退；ae4b3c7、f1d3fbd|
|11|completed|run/status/verify/demo；过期/失败自动新 attempt，旧回执保留；d4aa673、7bb7bee、e9a9fe8|
|12|completed|机器回执/manifest/报告；工程与策略分栏；scheduled=false；d4aa673|
|13|completed|新增 30 个 tripwire；边界、源重构、完整 demo、后继代、二次报价、失败恢复|
|14|completed|synthetic-verified：真实改变实验指针并恢复；独立语义 verify、重复 CLI 均 exit 0|
|15|completed|最终代码 real-release 已完成：50→26；技术9只均HOLD；成熟0，均 continue_shadow|
|16|completed|604 passed；独立 verify 与重复run exit 0；11,338 个实验文件 hash 重跑不变；保护复核与交付归档完成|

## 本轮修复中发现的问题

- 系统 python 无 pytest：使用现有 homespace .venv；未安装依赖。
- transient service 不继承工具环境：最初缺 TUSHARE_TOKEN 的真实失败保留。后续通过 stdin 传入已有环境，不把密钥写入 argv、文件或报告。
- 全股票池曾对每个股票重复序列化整帧：已改为每帧一次 hash，避免平方级计算。
- 后继代/active 实际作下一基线与旧仓退出续接已补齐，不只改 active 标签。
- 同一命令遇到失败/过期自动换真实当前时间的新 attempt，不回填旧时点。
- observed-quote-v1 需要两次递增报价，不能一次 once；已接 30 秒 watch。没有自然信号时不伪造执行。
- 报价路由单测曾缺 scope_codes：fixture 已补全，30 个新增测试最终通过；不隐瞒开发中失败。

## 保护范围说明

保护清单包含旧仓库、原历史、投资域和本机调度配置。唯一获准旧代码改动为 `mt1/candidates.py` 参数化（默认输出不变）。另观察到生产 longitudinal/latest.json 被同时运行的 thesis-weekly-bootstrap 在 05:31:09 更新；已保留对比及对应 manifest，未擅自恢复该正常更新。原历史材料未丢失/覆盖。

VPS 未执行任何写操作；后补的两次只读调度 hash 快照覆盖 43 项，内容相同。该快照不是开发开始时的远端基线，报告不混淆测量窗口。

## R2 独立验收修复（进行中）

- P1 发布断点：manifest 仅是归档检查点，新增 release-stages/<run>/<category>.json 与 complete.json；原轮同命令补齐发布，完成前 status/verify 保持 incomplete。异常失败回执不删除。
- P1 只读核验：root 身份、候选白名单/类别、active/intent/committed、证据重算、前序链、发布事件及逐类别终态一致性；verify 不调用 recover。synthetic verify 也拆为无锁、无初始化的只读路径。
- 新测试只替换采集边界，使用明确标注的缩小真实输入回放；实际 run/两引擎/validate/publish/verify 不 mock。回放不是新增真实样本。激活 prepare/switch 另由 SYNTHETIC_ONLY 实际指针测试覆盖。
- 原轮材料保留；本次真实采集与测试回执写 reports/mt14-r2。远端开工前基线无法回补，继续披露。

R2 测试进度：原全量加只读/逐类别恢复 tripwire 共 625 项已实测通过（227.12s）；另补 62 个合成前向观测轮的完整 run prepare/switch 激活故障测试进行中，不以低层 publish 单测替代。真实 release 版新采集完成，5562→50/26，技术9只两版HOLD，成熟0；独立 verify/同命令幂等正在复核。开发中两次旧代码采集尝试因边开发边核验触发 engine_or_contract_version_changed，拒绝行为与失败目录完整保留，没有改写旧 manifest 的版本 hash 来强行放行。

R2 最终：625 项回归通过 + 2 项完整成熟 run 故障恢复通过，当前收集全部 627 项覆盖；两份合成夹具各62轮、各1次晋级提交。真实 verify/重复run exit0，11,341 文件hash不变；工程缺口已修复，交付边界与并发既有定时器审计例外分栏。等待原投资topic独立验收。

## R3 独立回归退修

R2 的 12 个回放恢复/篡改单测受真实墙钟影响，独立 12 failed / 613 passed 已确认有效。仅测试时钟修复：同时绑定原决策时点的 action_loop.now / iteration_loop.now，归档时间和原 1800 秒生产门禁不变。新增远期墙钟回放、1800/1801 边界、过期真实输入拒绝后同 request-id 使用另一份新采集归档恢复的完整 run 测试；不 mock 引擎、验证或发布。当前全部测试（含两个62轮成熟合成测试）重新运行中，现有R2真实轮只读verify已exit0，无重复全市场抓数。

R3 最终：单次全量631 passed in1303.54s，未排除测试；针对性17 passed in147.94s。原1800/1801秒边界及过期同请求重采恢复均通过；引擎/合同hash与R2真实交付完全相同，现有真实verify exit0。原始日志、测试夹具、保护比较与范围说明已归档，重新回传独立验收，不自行accepted。
