**纯合成工程样例｜TEST/BENCH，未写生产事件**
固定模拟时钟 2026-10-11T12:00:00Z；只演示可复现算式和旧退出/新episode隔离。

**研究 episode 绩效核验（不是实盘盈亏）**
未复权收盘价情景；显式双边费用同样作用于股票与基准；非总回报、非真实成交/个人盈亏；退出后为继续持有反事实而非持仓。
|代码/episode|核验段|基准日期→目标日期|状态/原因|毛/净价格收益|基准净收益/超额|
|---|---|---|---|---|---|
|TEST / episode1|推荐期间|2026-09-01→2026-09-30|verified_price_scenario：已核验原文数据及公司行动区间|10.00% / 9.78%|4.79% / 4.99%|
基准 BENCH；币种 CNY；价格口径 unadjusted_close；费用 SYNTHETIC 10bps each way：买入 10 bps / 卖出 10 bps。
|TEST / episode1|推荐期间|2026-09-01→2026-11-02|closed_before_horizon：explicit exit; no implied continued position|待核验 / 待核验|待核验 / 待核验|
|TEST / episode1|推荐至退出|2026-09-01→2026-09-30|verified_price_scenario：已核验原文数据及公司行动区间|10.00% / 9.78%|4.79% / 4.99%|
基准 BENCH；币种 CNY；价格口径 unadjusted_close；费用 SYNTHETIC 10bps each way：买入 10 bps / 卖出 10 bps。
|TEST / episode1|退出后反事实|2026-09-30→2026-10-09|verified_price_scenario：已核验原文数据及公司行动区间|9.09% / 8.87%|4.55% / 4.32%|
基准 BENCH；币种 CNY；价格口径 unadjusted_close；费用 SYNTHETIC 10bps each way：买入 10 bps / 卖出 10 bps。
|TEST / episode2|推荐期间|2026-10-09→2026-11-09|not_matured：target date not reached|待核验 / 待核验|待核验 / 待核验|
基准 BENCH；币种 CNY；价格口径 unadjusted_close；费用 SYNTHETIC 10bps each way：买入 10 bps / 卖出 10 bps。
