# handoff 12项映射（开发自查，不是验收通过）

|项|实现/证据|状态与边界|
|---|---|---|
|1 数据源盘点|初始状态、既有tushare.py、raw HTTP与失败文件、HK替代|已有凭证只继承不打印；不采购|
|2 长历史采集|yearly/Collector，2021—2025，calendar1212|CN12全量完成；HK见缺口|
|3 独立预冻结样本|frozen-contract.json，1bfbe45，首次stage hash|不含6只A股持仓；current-vintage选择偏差醒目标明|
|4 原始字节/日历/因子/基准|raw与meta、full-coverage、panel gzip|沪深日历与000300全对齐；历史行业unknown、非现金流总回报|
|5 历史执行证据和时钟|next_open、evidence/acquired_at/realization_at/open_at|不使用forward known_at；历史独立版本；日K队列未知|
|6 下一可成交/限制|基准与any_limit_touch压力、26项新增测试、逐笔verify|限价原价比较、T+1、跳空open；缺口unknown不跳有利价|
|7 A/B冻结/成本/OOS|trial1合同、71个A episode、3种B、15/30/50bps|共同train120/purge60/test60；所有未闭合保留|
|8 非空与不确定性|108股票折，六臂闭合36/29/54/54/15/55|配对与股票×时间簇区间；非PIT/非组合/非显著优势|
|9 唯一入口/断点|history_research CLI，systemd隔离，stages.jsonl|pilot先完成真实交易再扩全12；失败raw保留，成功checkpoint复用|
|10 测试与保真|405通过、152旧hash、raw与版本、两次离线结果一致|旧379和修复保留；旧脏数据未提交|
|11 禁止边界|限定新增文件、git差异、旧hash审计|无VPS/cron/持仓/旧计划/晋级/交易|
|12 诚实交付|DELIVERY/研究限制/incomplete结构化回传|CN有结果无运行失败；完整HK A/B未完成，权限/因子/HSI/执行缺口明列|
