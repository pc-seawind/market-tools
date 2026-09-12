**技术信号模拟周报｜不是个人盈亏或策略有效性认证**
合成演示，所有行情/成交均非真实。
|版本|本周BUY/SELL|跨周未执行|模拟持仓/退出|有效往返样本|净价格收益|
|---|---|---|---|---|---|
|signal-policy-v1|1/1|0|0/1|1|[-0.2740704611285836]|

**signal-policy-v1｜未执行与限制**
无待执行订单。
取消=0；过期=0；路径回撤=[-0.2697247706422018]
假突破=0；退出后反弹/卖飞观察=[None]
20/40/60信号复盘（价格观察，不是个人盈亏）=[{'signal_id': '5077b9c6d7e5b3a8736fb39735f0f3dc029ea77d44c09ab8e619a5e79cd50a54', 'side': 'BUY', 'horizons': {'20': {'status': 'not_matured', 'return': None}, '40': {'status': 'not_matured', 'return': None}, '60': {'status': 'not_matured', 'return': None}}, 'not_personal_pnl': True}, {'signal_id': '30b58e1d605e798af49f3b51a6df12f830564b5d3819e055dab14bafaa25cea4', 'side': 'SELL', 'horizons': {'20': {'status': 'not_matured', 'return': None}, '40': {'status': 'not_matured', 'return': None}, '60': {'status': 'not_matured', 'return': None}}, 'not_personal_pnl': True}]
有效性/费用/路径限制=['small_forward_sample_not_efficacy_proof', 'costs_are_scenarios', 'daily_low_close_path_not_intrabar_order']
无交易时逐卡区分硬数据缺口、规则未满足、执行证据不足；不因低置信度封口。

按版本分账向前比较；旧MT12规则每日报并列观察，不虚构旧规则成交收益
人工提交reason/parent/version/frozen timing/effective_at；保留v1向前并行，禁止回填与自动调参
不自动调参；对新版本只作生效日之后的独立模拟。
