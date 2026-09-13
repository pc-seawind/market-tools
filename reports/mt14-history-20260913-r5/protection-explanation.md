# 保护差异说明

原保护清单与本单开始时均核对 112205 项；本单开始到检查时 0 差异。
原任务开始到当前只发现两项差异，均为投资域审批/恢复记录，不是策略或生产配置：

1. MT14-historical-comparison-plan.md：投资域追加已批准的子包兼容性修正，禁止旧顶层 stub、不改旧指纹/manifest。
2. MT14-historical-comparison-tasks.md：投资域追加中断、恢复及关联新工单的追溯；未降低16项要求。

本单没有编辑这两个文件。具体 before/after hash 见 protection-check.json。
原脏 narrative/recommendations/sector_picks jsonl、watchlist 删除和未跟踪 backtest 文件保持原状，不暂存、不提交。
旧 MT13/MT14 源码相对 dd8a5f8 字节不变；旧 R2 manifest 原字节未改，直接 verify exit 0。
生产/实验 active、持仓、scope、自选、调度均未改，不 push、不实盘、不修改 gateway。
