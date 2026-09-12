# MT13 revision 2 增量交付（待原topic独立验收）

work_id: work_c04ee4237f5a367a1ed9
代码提交：ad2bf3d。运行命令、部署模板、回退见 docs/mt13/REVISION2.md。

## 本轮改动

- P0：有限时真实HTTP采集worker、逐响应原始字节/实际时刻/hash/字段来源、自动execution bundle适配与消费。执行入口重新从原响应推导，拒绝手工canonical真实封装。交易日、限流、权限/网络失败、错过窗口有显式状态及恢复路径。
- strict-open-v1 的60秒窗口没有放宽。CN资格源接入；HK公开源无法证明strict开盘前venue资格，明确阻断。另提供独立root固定的observed-quote-v1，按两次实际报价增长及实际收齐时刻估值，**不是可成交证明**，不把延迟last伪装历史open。
- P1：同一SELL signal_id/原理由保持不变，持续风险可审计追加execution attempt；保留3日TTL，恢复不得使用复核/恢复之前open，人工取消永不自动复活，暂停须显式恢复。
- P2：早/晚/周消费脚本和有限worker service模板，职责分离，技术失败不阻断原行情/公司研究。全部未接生产、未发布。

## 可复验结果

- 全量pytest：546 passed in 46.59s；原516测试不删改。
- audit.json：60个原响应内容hash通过；real-consumer-archive 2个snapshot、两组SYNTHETIC恢复各10个、observed合成3个，归档读取校验通过。
- real-once：实际休市HTTP预取wall 2.581秒，单次9标的批量quote 0.512秒，总3.243秒；自动消费到隔离账本，重复消费idempotent=true。
- probe-1：真实三次报价约0.55/0.57/0.98秒；observed-probe三次约0.55/0.54/0.55秒。证明当前网络运行方式的耗时可进入60秒预算，**不证明下次自然开盘必成功**。
- last-session-api-probe：补查2026-09-11真实接口，停牌12条、涨跌停5641条、六只CN因子各1条；采集时刻仍2026-09-12，**不是9月11日开盘前快照**。
- 真实9只动作仍HOLD；真实采集消费未制造信号、未建模拟仓、未发生模拟成交。既有九持仓完整动作卡及源hash仍在revision1证据目录，未重写。
- SYNTHETIC-recovery-suspended / SYNTHETIC-recovery-rate_limited：3日失败→过期→同一SELL追加attempt2→合法新open→恰好1次退出。所有时钟/成交明确合成。
- SYNTHETIC-observed：另模型原响应形状→实际观测估值时刻→隔离模拟成交，明确合成，不与真实结果混合。
- structural-morning/evening/weekly：实际消费命令产物，早晚原三段为既有合成占位文本，技术九HOLD来自真实archive；这只是结构接线验证，不是已发布日报。

## 未完成/待自然条件与审批（不是隐去工程缺口）

1. 2026-09-14首个合法实时开盘尚未到；没有捕获未来成交。自然窗口资格/报价/执行验证待投资topic批准调度后运行。
2. 当前公开源不能完成HK strict模型的开盘前venue资格证明，故仍硬阻断；可运行的替代只限独立observed-quote-v1估值模型，须明确选择，不能宣称strict验证通过。
3. 生产调度、早晚周发布接线均留给投资topic独立复验和批准；本轮未改VPS/群/时间/持仓/scope/自选，未交易，未push。

原462/516测试与全部历史A/H/MT13证据保留。shadow使用授权不等于validated；本交付不自行宣告验收通过。
