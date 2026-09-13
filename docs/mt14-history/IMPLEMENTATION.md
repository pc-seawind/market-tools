# MT14 历史双规则执行记录

批准依据：投资域 MT14-historical-comparison-{spec,plan,tasks}.md。
冻结于 reports/mt14-history-20260913/frozen-contract.json，HEAD dd8a5f8。

- 规则唯一差异：ROE10→12、breakout_volume_min1.2→1.4，未改生产引擎。
- 历史样本：既有12只CN和3只HK原材料；当前9持仓交集0。不是全市场PIT。
- 基本面调用原screen；仅使用 ann_date < 月末，保留全部其余字段及门槛。
- 历史技术使用原timing.step及MT13动作优先级；真实采集时刻留在raw元数据，不回填成历史observed报价。
- 价格观察：下一交易日收盘后20/40/60日。路径任一缺价则unknown，末端未成熟保留。
- 日级估计：复用CN/HK原历史next_open资格适配器，不把日K伪造成实时可成交证据。
- 费用单边CN15/HK25bps，另2倍/3倍，未按结果调参。
- 财报最新修订版本、历史ST名称缺口、港股2020复权权限不足都是测量限制，不包装成严格未知OOS。

## 已保留的失败

1. 系统python无pytest：baseline-tests.txt，不是测试通过。
2. systemd首次未继承TUSHARE_TOKEN：186个credential_missing回执，没有发出网络请求；原目录保留。
3. 第二轮正确继承既有凭据，185请求成功、hk_adjfactor明确权限拒绝1次，原响应保留；不购买数据。
4. 基线测试运行中新增模块变化触发2个engine_or_contract_version_changed；不是原631回归通过。完整日志保留。
5. 初版r2数值及r3修正均保留；r3离线复算相同，但订单恢复/资本槽审计尚未完备，不能用测试绿替代研究验收。
6. r3全量测试在进一步工程修正前主动停止，避免一边改代码一边让code_hash等价测试失真；保留中间输出，最终稳定代码另跑全量。

不push、不实盘、不改active/持仓/scope/自选/调度/VPS。后续由投资topic独立验收。

## r4 工程修正（规则、数据、名单、费用不变）

- 历史订单直接调用原 action_recovery.ensure/renew/expire 及 action_loop.transition。
- SELL 到期后保持同一个signal_id，重确认日之后才允许新尝试开盘，禁止过期后回填先前开盘。
- 决策/执行顺序对齐MT13：先当日决策、再处理早于当日收盘的合法开盘、最后风险取消未成交BUY。
- 增加每股每臂10000初始资本逐日复投账本：一笔占用时不允许再次分配资金；已占用槽不再伪称现金。
- technical_capital单列共同股票×日账户净值窗口，起点为下一交易日收盘账户净值，终点为其后h日；持仓可能早于窗口，故不是新建仓事件收益，也不年化。
- 资本路径记录每一天的cash/units/wealth/unknown；资金未知不填0。事件净收益与资本窗口收益分表。
- 已增加完整合成BUY→下一开盘→SELL→下一开盘，以及SELL到期重试同signal和禁止重复资金占用测试；不mock核心引擎和恢复函数。
- r2/r3初版结果保留，不覆盖；仅r4作为本轮最终诊断，原始输入manifest完全相同。
