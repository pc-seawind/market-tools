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
