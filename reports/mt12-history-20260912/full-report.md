# MT1.2 真实历史择时诊断（full）

**只支持当前 vintage 样本内的价格择时诊断，不支持历史选股有效性、组合收益或自动晋级。**

预先冻结 2021—2025 年 12 只独立 A 股（pilot 为前 3 只），没有按照回报替换或删样本。全部 TREND；非当前持仓池。
本次有效股票 12，采集失败 0，股票×OOS 折 108。
120 交易日训练/暖机（不拟合）＋60 purge＋60 test，stride120。交易不得越过 test 末尾；未退出保留，不能强行平仓或删除。

## 基准：每侧 15 bps，下一可成交开盘价情景

|组/臂|信号样本|闭合|状态计数|闭合净事件均值|最差闭合交易|最差闭合收盘回撤|单边换手单位|假突破/未知|卖飞均值/未知|
|---|---:|---:|---|---:|---:|---:|---:|---|---|
|A/old_ma60_5|71|36|{'not_matured': 33, 'pending': 2, 'closed': 36}|-4.57%|-11.22%|-19.93%|107|0/71|4.76%/50|
|A/structure_failure|71|29|{'not_matured': 42, 'closed': 29}|-5.17%|-13.19%|-15.60%|100|0/71|5.48%/49|
|A/structure_failure_atr|71|54|{'not_matured': 17, 'closed': 54}|-0.23%|-12.91%|-14.03%|125|0/71|6.05%/28|
|B/old_trigger|71|54|{'not_matured': 17, 'closed': 54}|-0.23%|-12.91%|-14.03%|125|0/71|6.05%/28|
|B/pullback|71|55|{'not_matured': 16, 'closed': 55}|-0.41%|-12.09%|-14.03%|126|0/71|6.27%/30|
|B/breakout|19|15|{'closed': 15, 'not_matured': 4}|0.43%|-7.18%|-11.30%|34|5/4|7.56%/9|

收益仅是各臂已闭合事件的因子调整价格净收益算术均值，不是资金组合收益、CAGR、真实下单盈亏或股息现金流总回报。
闭合数不同会造成严重删失选择偏差，不能直接比较上表均值认定因果优势。单边换手单位是每笔事件买/卖各 1，不是资金组合换手率。
最差交易是尾部样本观察，不是 VaR。卖飞＝退出后 20 交易日内最大收盘价/退出价−1，负值保留；不足完整 OOS 后续窗口为 unknown。
假突破仅对登记 breakout episode 且完整观察 10 个交易日的闭合样本判断；其余 unknown，不把非突破入场标“成功突破”。

## A 同 episode、相同实际入场的配对复核

|新版退出对旧退出|配对闭合/全部 episode|收益差（百分点）|回撤差（正数较好）|股票/时间簇|描述性双向簇区间（收益差）|
|---|---:|---:|---:|---|---|
|structure_failure|24/71|-1.48%|-1.00%|11/7|-3.27% ~ 0.11%|
|structure_failure_atr|35/71|1.47%|1.99%|12/8|-0.50% ~ 4.68%|

区间：固定随机种子 1201，2,000 次股票×OOS 折交叉重抽样，不把同股/同时间事件视为独立。仍受少量簇、市场共同冲击、幸存者及双闭合条件影响，不作为显著性或上线证明。
B 比较共同 scope、共同 OOS、相同退出，但信号日期/机会数本来不同；不把不同机会集的闭合均值差宣称为同入场处理效应。

## 成本与执行敏感性

|情景|每侧 bps|组/臂|闭合/信号|闭合均值|最差回撤|
|---|---:|---|---:|---:|---:|
|open_price_limit_base_v1|15|A/old_ma60_5|36/71|-4.57%|-19.93%|
|open_price_limit_base_v1|30|A/old_ma60_5|36/71|-4.86%|-20.05%|
|open_price_limit_base_v1|50|A/old_ma60_5|36/71|-5.24%|-20.21%|
|open_price_limit_base_v1|15|A/structure_failure|29/71|-5.17%|-15.60%|
|open_price_limit_base_v1|30|A/structure_failure|29/71|-5.46%|-15.72%|
|open_price_limit_base_v1|50|A/structure_failure|29/71|-5.83%|-15.89%|
|open_price_limit_base_v1|15|A/structure_failure_atr|54/71|-0.23%|-14.03%|
|open_price_limit_base_v1|30|A/structure_failure_atr|54/71|-0.53%|-14.15%|
|open_price_limit_base_v1|50|A/structure_failure_atr|54/71|-0.92%|-14.33%|
|open_price_limit_base_v1|15|B/old_trigger|54/71|-0.23%|-14.03%|
|open_price_limit_base_v1|30|B/old_trigger|54/71|-0.53%|-14.15%|
|open_price_limit_base_v1|50|B/old_trigger|54/71|-0.92%|-14.33%|
|open_price_limit_base_v1|15|B/pullback|55/71|-0.41%|-14.03%|
|open_price_limit_base_v1|30|B/pullback|55/71|-0.71%|-14.15%|
|open_price_limit_base_v1|50|B/pullback|55/71|-1.11%|-14.33%|
|any_limit_touch_conservative_v1|15|A/old_ma60_5|36/71|-4.57%|-19.93%|
|any_limit_touch_conservative_v1|30|A/old_ma60_5|36/71|-4.86%|-20.05%|
|any_limit_touch_conservative_v1|50|A/old_ma60_5|36/71|-5.24%|-20.21%|
|any_limit_touch_conservative_v1|15|A/structure_failure|29/71|-5.17%|-15.60%|
|any_limit_touch_conservative_v1|30|A/structure_failure|29/71|-5.46%|-15.72%|
|any_limit_touch_conservative_v1|50|A/structure_failure|29/71|-5.83%|-15.89%|
|any_limit_touch_conservative_v1|15|A/structure_failure_atr|54/71|-0.26%|-15.46%|
|any_limit_touch_conservative_v1|30|A/structure_failure_atr|54/71|-0.56%|-15.59%|
|any_limit_touch_conservative_v1|50|A/structure_failure_atr|54/71|-0.96%|-15.75%|
|any_limit_touch_conservative_v1|15|B/old_trigger|54/71|-0.26%|-15.46%|
|any_limit_touch_conservative_v1|30|B/old_trigger|54/71|-0.56%|-15.59%|
|any_limit_touch_conservative_v1|50|B/old_trigger|54/71|-0.96%|-15.75%|
|any_limit_touch_conservative_v1|15|B/pullback|55/71|-0.73%|-15.46%|
|any_limit_touch_conservative_v1|30|B/pullback|55/71|-1.03%|-15.59%|
|any_limit_touch_conservative_v1|50|B/pullback|55/71|-1.42%|-15.75%|
|open_price_limit_base_v1|15|B/breakout|15/19|0.43%|-11.30%|
|open_price_limit_base_v1|30|B/breakout|15/19|0.13%|-11.44%|
|open_price_limit_base_v1|50|B/breakout|15/19|-0.27%|-11.62%|
|any_limit_touch_conservative_v1|15|B/breakout|15/19|-0.03%|-11.30%|
|any_limit_touch_conservative_v1|30|B/breakout|15/19|-0.33%|-11.44%|
|any_limit_touch_conservative_v1|50|B/breakout|15/19|-0.73%|-11.62%|

## 未闭合暴露（不伪装成已实现收益）

|组/臂|可标记未闭合数|平均未实现价格标记|最差未实现收盘回撤|
|---|---:|---:|---:|
|A/old_ma60_5|35|5.76%|-14.97%|
|A/structure_failure|42|4.13%|-20.45%|
|A/structure_failure_atr|17|2.21%|-5.20%|
|B/breakout|4|3.84%|-5.20%|
|B/old_trigger|17|2.21%|-5.20%|
|B/pullback|16|2.67%|-6.86%|

未实现标记只扣入场成本，按 test 最后已知收盘估值，没有假设卖出。未与闭合收益混成单一绩效。

## 数据覆盖

|代码|日历|行情|缺口|停复牌记录日|因子变化次数|当前行业（非历史）|
|---|---:|---:|---:|---:|---:|---|
|600000.SH|1212|1212|0|0|6|银行|
|000001.SZ|1212|1212|0|0|8|银行|
|600009.SH|1212|1201|11|13|4|机场|
|600028.SH|1212|1212|0|0|11|石油加工|
|600030.SH|1212|1206|6|7|6|证券|
|600036.SH|1212|1212|0|0|5|银行|
|600050.SH|1212|1212|0|0|12|电信运营|
|600104.SH|1212|1212|0|0|5|汽车整车|
|600276.SH|1212|1212|0|0|5|化学制药|
|600519.SH|1212|1212|0|0|12|白酒|
|000333.SZ|1212|1212|0|0|7|家用电器|
|000651.SZ|1212|1212|0|0|10|家用电器|

逐股、逐折、市场、行业 unknown、波动、趋势阶段及当前行业诊断完整分层见 results.json 的 strata。没有伪造历史行业成员。

## 执行证据与未知

- 独立 historical model，不改 forward `next_fill` 门禁，不把采集时间/事后成交结果写成当年 `known_at`。
- 信号只读当日及此前完成的 OHLCV/宽基；先发信号，再由独立执行器检查下一 session。原始价比当日 vendor 涨跌停价；收益用 raw×factor。
- 基准情景：买入遇涨停开盘、卖出遇跌停开盘跳过；已知停牌（含日内停牌）保守整日跳过。不明缺口/限价/复权立即 unknown，不跳到后面挑有利价格。
- 压力情景：买入当日 high 触涨停或卖出 low 触跌停也整日不成交。这是使用事后行情的悲观成交敏感性，不是开盘前可知规则，也不保证收益一定更低。
- open_at 是交易所 session 时间标签，不是订单成交证明；daily 正成交量只证明当日有交易，不能证明开盘队列/成交容量。小额可成交开盘价是明确模型假设。
- CN T+1 按交易所 session 索引；跳空用真实下一 open，不按昨日收盘或日内止损线假成交。量化成交数量、冲击、税费时变均未核验。
- 缺行情不会插值，也不会压缩掉缺失交易日：信号状态重暖机120；已有持仓跨停牌后因不足暖机标 unknown，保守但会损失覆盖。
- 当前样本上市日期须早于开始日。没有历史退市总体、历史 ST/行业成员或历史指数成分 PIT 资格证明；当日限价使用供应商历史实际限价而非统一假设10%。
- 费用15/30/50 bps每侧是冻结模型情景，不是券商核验费率。港股独立可用性检查见 hk-feasibility.json / hk-alternative.json；不在本 CN 结果中凑数。

## 复跑与结论边界

`python -m mt1.history_research --out /home/emox/work/projects/market-tools/reports/mt12-history-20260912 --offline`
读取冻结合同和实际 HTTP 字节 checkpoint，校验 hash，重新生成全部信号与交易。首次采集去掉 --offline 并继承已有 TUSHARE_TOKEN（不得打印）。
这些结果可以支持是否继续 shadow 研究，不能证明历史选股有效、未来可获利、资金组合超额或实盘可交易。没有调参、没有晋级、没有下单。

## 来源

- Tushare 实际 HTTPS 响应：raw/*.json；请求参数、离线获取时间与 SHA256：raw/*.meta.json。
- [涨跌停数据](https://tushare.pro/document/2?doc_id=183)、[停复牌数据](https://tushare.pro/document/2?doc_id=214)、[复权因子](https://tushare.pro/document/2?doc_id=28)。
- 研究合同文件 SHA256：`10836dd31652c1e3ed1e2b285f065a0c327c90c79dd448239a0a7b3b33d1910f`。
