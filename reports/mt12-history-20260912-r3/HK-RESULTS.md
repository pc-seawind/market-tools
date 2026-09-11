# HK 2021—2025 真实历史诊断（r3）

固定汇丰00005、港交所00388、中移动00941；不改原参数。9个共同OOS窗、27个股票×fold。
当前vintage复权价格择时诊断；不是PIT选股、实际成交凭证或资金组合收益。
成交为经session资格重建后的下一session日open估计；费用每边25/50/75bps。
资格来自完整非空HKEXnews股票年度公告与专项分类交叉查询及官方市场例外；不是逐tick开盘状态认证。
正常日09:20—09:30为开盘估计窗口，不伪造日open实际成交时间；延迟开市使用公告时间。
T+2为清算周期，非禁售期；无CN固定涨跌停/T+1。VCM日保守情景为事后执行压力而非信号。

|基准25bps组/臂|事件数|闭合|闭合均值|截止有值/全事件|共同截止均值|平均/最差全路径回撤|
|---|---:|---:|---:|---|---:|---|
|A/old_ma60_5|23|13|-6.0842%|23/23|-0.9298%|-8.2729%/-20.8286%|
|A/structure_failure|23|8|-9.4110%|23/23|-1.8028%|-9.2849%/-20.8286%|
|A/structure_failure_atr|23|18|-2.1895%|23/23|-0.5262%|-5.6847%/-10.0075%|
|B/pullback|25|20|-3.2515%|25/25|-1.6430%|-5.9709%/-18.7406%|
|B/old_trigger|23|18|-2.1895%|23/23|-0.5262%|-5.6847%/-10.0075%|
|B/breakout|10|8|-2.0845%|10/10|-1.0217%|-3.5205%/-6.8161%|

主比较使用共同截止全部事件：已退出转零息现金，未退出以精确fold末close估值；仅扣已发生费用。

|基准配对新版−旧版|已知/全分母|截止估值差|描述性95%区间|平均路径回撤差（正较好）|
|---|---|---:|---|---:|
|A/structure_failure|23/23|-0.8730%|-3.2491% — 1.1870%|-1.0120%|
|A/structure_failure_atr|23/23|0.4036%|-3.6409% — 2.7170%|2.5882%|
|B/breakout|27/27|0.0698%|-3.6226% — 3.3304%|3.5386%|
|B/pullback|27/27|-1.0730%|-4.1589% — 1.2033%|-0.6861%|

B不是同入场因果比较；全部27个股票×fold资本机会分母含完整可观测的无信号现金和unknown。

|全费用/执行/组/臂/口径|有值/分母|截止均值|未退出假设卖出费敏感性|
|---|---|---:|---:|
|hk_next_open_v1|25|A|old_ma60_5|entered_events|23/23|-0.9298%|-1.0448%|
|hk_next_open_v1|25|A|structure_failure|entered_events|23/23|-1.8028%|-1.9695%|
|hk_next_open_v1|25|A|structure_failure_atr|entered_events|23/23|-0.5262%|-0.5835%|
|hk_next_open_v1|25|B|old_trigger|entered_events|23/23|-0.5262%|-0.5835%|
|hk_next_open_v1|25|B|old_trigger|capital_opportunities|27/27|-0.4483%|-0.4971%|
|hk_next_open_v1|25|B|breakout|entered_events|10/10|-1.0217%|-1.0734%|
|hk_next_open_v1|25|B|breakout|capital_opportunities|27/27|-0.3784%|-0.3975%|
|hk_next_open_v1|25|B|pullback|entered_events|25/25|-1.6430%|-1.6954%|
|hk_next_open_v1|25|B|pullback|capital_opportunities|27/27|-1.5213%|-1.5698%|
|hk_next_open_v1|50|A|old_ma60_5|entered_events|23/23|-1.3090%|-1.5383%|
|hk_next_open_v1|50|A|structure_failure|entered_events|23/23|-2.1259%|-2.4585%|
|hk_next_open_v1|50|A|structure_failure_atr|entered_events|23/23|-0.9650%|-1.0794%|
|hk_next_open_v1|50|B|old_trigger|entered_events|23/23|-0.9650%|-1.0794%|
|hk_next_open_v1|50|B|old_trigger|capital_opportunities|27/27|-0.8221%|-0.9195%|
|hk_next_open_v1|50|B|breakout|entered_events|10/10|-1.4638%|-1.5668%|
|hk_next_open_v1|50|B|breakout|capital_opportunities|27/27|-0.5421%|-0.5803%|
|hk_next_open_v1|50|B|pullback|entered_events|25/25|-2.0812%|-2.1857%|
|hk_next_open_v1|50|B|pullback|capital_opportunities|27/27|-1.9270%|-2.0238%|
|hk_next_open_v1|75|A|old_ma60_5|entered_events|23/23|-1.6862%|-2.0294%|
|hk_next_open_v1|75|A|structure_failure|entered_events|23/23|-2.4473%|-2.9450%|
|hk_next_open_v1|75|A|structure_failure_atr|entered_events|23/23|-1.4017%|-1.5728%|
|hk_next_open_v1|75|B|old_trigger|entered_events|23/23|-1.4017%|-1.5728%|
|hk_next_open_v1|75|B|old_trigger|capital_opportunities|27/27|-1.1940%|-1.3398%|
|hk_next_open_v1|75|B|breakout|entered_events|10/10|-1.9036%|-2.0577%|
|hk_next_open_v1|75|B|breakout|capital_opportunities|27/27|-0.7051%|-0.7621%|
|hk_next_open_v1|75|B|pullback|entered_events|25/25|-2.5172%|-2.6736%|
|hk_next_open_v1|75|B|pullback|capital_opportunities|27/27|-2.3307%|-2.4755%|
|hk_vcm_day_conservative_v1|25|A|old_ma60_5|entered_events|23/23|-0.9298%|-1.0448%|
|hk_vcm_day_conservative_v1|25|A|structure_failure|entered_events|23/23|-1.8028%|-1.9695%|
|hk_vcm_day_conservative_v1|25|A|structure_failure_atr|entered_events|23/23|-0.5262%|-0.5835%|
|hk_vcm_day_conservative_v1|25|B|old_trigger|entered_events|23/23|-0.5262%|-0.5835%|
|hk_vcm_day_conservative_v1|25|B|old_trigger|capital_opportunities|27/27|-0.4483%|-0.4971%|
|hk_vcm_day_conservative_v1|25|B|breakout|entered_events|10/10|-1.0217%|-1.0734%|
|hk_vcm_day_conservative_v1|25|B|breakout|capital_opportunities|27/27|-0.3784%|-0.3975%|
|hk_vcm_day_conservative_v1|25|B|pullback|entered_events|25/25|-1.6430%|-1.6954%|
|hk_vcm_day_conservative_v1|25|B|pullback|capital_opportunities|27/27|-1.5213%|-1.5698%|
|hk_vcm_day_conservative_v1|50|A|old_ma60_5|entered_events|23/23|-1.3090%|-1.5383%|
|hk_vcm_day_conservative_v1|50|A|structure_failure|entered_events|23/23|-2.1259%|-2.4585%|
|hk_vcm_day_conservative_v1|50|A|structure_failure_atr|entered_events|23/23|-0.9650%|-1.0794%|
|hk_vcm_day_conservative_v1|50|B|old_trigger|entered_events|23/23|-0.9650%|-1.0794%|
|hk_vcm_day_conservative_v1|50|B|old_trigger|capital_opportunities|27/27|-0.8221%|-0.9195%|
|hk_vcm_day_conservative_v1|50|B|breakout|entered_events|10/10|-1.4638%|-1.5668%|
|hk_vcm_day_conservative_v1|50|B|breakout|capital_opportunities|27/27|-0.5421%|-0.5803%|
|hk_vcm_day_conservative_v1|50|B|pullback|entered_events|25/25|-2.0812%|-2.1857%|
|hk_vcm_day_conservative_v1|50|B|pullback|capital_opportunities|27/27|-1.9270%|-2.0238%|
|hk_vcm_day_conservative_v1|75|A|old_ma60_5|entered_events|23/23|-1.6862%|-2.0294%|
|hk_vcm_day_conservative_v1|75|A|structure_failure|entered_events|23/23|-2.4473%|-2.9450%|
|hk_vcm_day_conservative_v1|75|A|structure_failure_atr|entered_events|23/23|-1.4017%|-1.5728%|
|hk_vcm_day_conservative_v1|75|B|old_trigger|entered_events|23/23|-1.4017%|-1.5728%|
|hk_vcm_day_conservative_v1|75|B|old_trigger|capital_opportunities|27/27|-1.1940%|-1.3398%|
|hk_vcm_day_conservative_v1|75|B|breakout|entered_events|10/10|-1.9036%|-2.0577%|
|hk_vcm_day_conservative_v1|75|B|breakout|capital_opportunities|27/27|-0.7051%|-0.7621%|
|hk_vcm_day_conservative_v1|75|B|pullback|entered_events|25/25|-2.5172%|-2.6736%|
|hk_vcm_day_conservative_v1|75|B|pullback|capital_opportunities|27/27|-2.3307%|-2.4755%|

卖飞20日、假突破10日均限定共同OOS边界；边界不足unknown。尾部、换手、分层、未知覆盖见full-results及common-cutoff-results。
只有3个股票簇，2000次交叉股票×fold bootstrap仅为描述性；幸存者偏差/现时行业未知不因此消失。
因子用新浪链归一到2025末，已有Yahoo链交叉核验；不是现金股息账户收益。手续费为冻结的混合费率情景，不含实际委托金额/手数/最低佣金逐笔计费。
日open非盘口保证：没有队列、冲击、成交容量、券商账户限制或借贷复用假设。持有现金资金足额的独立事件，不声称真实组合可执行。
