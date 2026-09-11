# MT-1.2 开发交付（待投资 topic #3055 独立验收）

work_id: work_e36a7c662cffcc43a1d7
实现提交：1be0f3a；基线 4b68ef8。原 337 测试保留，新增 39，完整 376 passed（4.92s）。
本轮开发、验证、归档完成不代表收益有效或生产上线。没有调用交易、自选写入、VPS 或 cron 修改。

## 实际只读 9 持仓对照

真实采集完成 2026-09-11T13:42:08 UTC 附近；各股票数据日期 2026-09-11。6 只 A 股价格因子口径可用；3 只港股复权锚点不可验证，风险价阻断。
用户成本/实际买入日/数量均保持未知。首次前向观察时间保留为 2026-09-11T13:38:42.195307+00:00，不是用户建仓日。
|代码/名称|收盘|旧退出|新入场|新持仓风险|结构低点|ATR20|保护线|
|---|---|---|---|---|---|---|---|
|001309.SZ 德明利|402.7300|exit_review|breakout:watch_structure / pullback:watch_structure|monitor|386.1500|23.7965|331.3405|
|603986.SH 兆易创新|371.3200|exit_review|breakout:watch_structure / pullback:watch_structure|monitor|367.0000|18.8655|314.7235|
|688195.SH 腾景科技|194.8900|monitor|breakout:watch_structure / pullback:setup|monitor|158.3000|16.0500|146.7400|
|301511.SZ 德福科技|106.4000|monitor|breakout:watch_structure / pullback:watch_structure|monitor|76.9124|7.8195|82.9416|
|300750.SZ 宁德时代|330.5100|exit_review|breakout:watch_structure / pullback:watch_structure|monitor|326.0000|9.9230|300.7410|
|601138.SH 工业富联|64.0700|monitor|breakout:watch_structure / pullback:setup|monitor|58.7500|2.3980|56.8760|
|01810.HK 小米集团|26.3600|unknown|not_ready|unknown|unknown|unknown|unknown|
|03690.HK 美团|75.1000|unknown|not_ready|unknown|unknown|unknown|unknown|
|00700.HK 腾讯控股|428.4000|unknown|not_ready|unknown|unknown|unknown|unknown|

旧入场 6 只均 wait；腾景科技、工业富联仅为新回调 setup，未确认触发。德明利、兆易创新、宁德时代的旧规则 exit_review 警示仍保留，新监控当前未触发不能当作撤销旧警示或安全结论。
6 只 A 股结构区间为首次观察前 20 个完成交易日（具体逐只起止日期见 real-nine-cards.md/JSON）；表中所有价格折回当日单位，不是限价执行价。
全部 9 只行业 RS unknown；港股宽基与行业均 unknown；当前 scope 无美股，不声称美股真实覆盖。

## 可运行 A/B 与首轮结果

- A 真实：相同 6 个可核验的假设前向观察入场，三退出臂各 6 pending；没有下一可成交 session，0 成交、净收益 unknown。另 3 港股 blocked，未删行。
- B 真实：只有 120 session 历史，小于冻结滚动划分的训练+purge+观察条件；6 只 not_matured、3 只 blocked；三臂 0 交易。
- 合成正确性：A 三臂各 1 笔已闭合；B 旧触发 2 笔（1 未成熟）、突破确认 1 笔、回调确认 1 笔。完整费用/回撤/尾部/次数/假突破/卖飞未知覆盖在 synthetic-ab-final.json。
- 合成数据仅人工构造分支覆盖，不是实盘行情或策略优越性证据；不得把合成的损失差异用于择优。真实 cohort 是当前持仓事实，不冒充独立选股样本。

## 幂等、原件和跨周证据

- read-only-proof.json：77 个受保护原文件（scope/watchlist/thesis/计划 DB+WAL+SHM/原 cohort）前后 hash 一致。
- repeat-proof.json：重复运行返回同一 manifest/hash，没有重建监控或累加观察天数。
- weekly-final.json：9 个稳定 pending 跨快照去重；原件完整性错误 0；expected 未提供，明确 observed_only/not_verified，不冒充自然调度验收。
- 首次 systemd 采集未继承 TUSHARE_TOKEN，失败已保存 real-input；通过仅传环境变量名重跑 r2/r3 成功，无 credential 值进入产物。
- 原始输入保留真实 CLI CSV stdout 和港股 HTTP body；不是仅 URL/hash，也不把 CLI 缓存的解析结果叫原始 HTTP。

最终归档 manifest：`reports/timing-upgrade-20260911/real-archive/mt12-timing/user-reset-20260911T120928Z/2026-09-11/1ea322065a61d04256903d8d/r000001/manifest.json`
最终归档 manifest SHA256：`528c2aeb4636f060ed4cebad2bf2018d43a4695a113fd0a5cd0f5937663107d4`

## 接入/回退与未完成分类

已实现：P0/P1 状态机、P2 辅助诊断、收盘与下一可成交区分、公司行动口径防线、幂等/跨周/原件归档、A/B CLI。
数据未知：HK 复权锚点/宽基，全部行业成员与历史行业基准，US 实际价格因子适配尚未采集；历史 PIT 选股总体、退市/停牌/涨跌停/结算逐 session 真实执行证据及真实券商费用未补齐。
尚未成熟：真实 20/40/60 session 观察、滚动样本外与卖飞后续窗口；不能证明参数有效。
未执行（授权边界）：VPS/cron 接线、真实 watchlist/交易写入、方法 validated/active 晋级；由投资 topic 独立验收，后续仅决定 shadow 接线。
不改日报三段式，技术卡只附于第三段。longitudinal/weekly 接入、唯一 CLI、previous 幂等与回退开关见 docs/mt12/README.md。回退停止附表调用即可，旧链不变，归档不删。
历史研究/真实9只不能完成无幸存者偏差策略验证，本次不以制造虚假数据消除 incomplete。
