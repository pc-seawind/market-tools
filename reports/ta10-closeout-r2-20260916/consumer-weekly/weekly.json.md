**技术信号模拟周报｜不是个人盈亏或策略有效性认证**
真实数据前向观察；未接实盘。
|版本|本周BUY/SELL|跨周未执行|模拟持仓/退出|有效往返样本|净价格收益|
|---|---|---|---|---|---|
|signal-policy-v1|0/3|3|0/0|0|未成熟/无成交|

**signal-policy-v1｜未执行与限制**
[{'signal_id': 'dba3802b9ee75b1508124bd3e9cd1091f88e7f59d5b793595ca2a90f7358f788', 'code': '603986.SH', 'side': 'SELL', 'reason': 'no_virtual_position_real_holding_not_seeded', 'attempts': [{'number': 1, 'opened_at': '2026-09-14T09:41:08.619101+00:00', 'after_session': '2026-09-14', 'status': 'pending', 'reason': 'original_signal', 'recovery_policy': 'execution-recovery-v1'}], 'operator_paused': False}, {'signal_id': '524e049c094730b307a6068a7ebe495443d5a26e2de953883b01bbf1fe63ec0c', 'code': '001309.SZ', 'side': 'SELL', 'reason': 'no_virtual_position_real_holding_not_seeded', 'attempts': [], 'operator_paused': False}, {'signal_id': '533835fed968b184e95028e650a87c2a9d264f3eb2d1e0ce920ea97c866297a6', 'code': '300750.SZ', 'side': 'SELL', 'reason': 'no_virtual_position_real_holding_not_seeded', 'attempts': [], 'operator_paused': False}]
取消=0；过期=0；路径回撤=[]
假突破=0；退出后反弹/卖飞观察=[]
20/40/60信号复盘（价格观察，不是个人盈亏）=[{'signal_id': 'dba3802b9ee75b1508124bd3e9cd1091f88e7f59d5b793595ca2a90f7358f788', 'side': 'SELL', 'horizons': {'20': {'status': 'not_matured', 'return': None}, '40': {'status': 'not_matured', 'return': None}, '60': {'status': 'not_matured', 'return': None}}, 'not_personal_pnl': True}, {'signal_id': '524e049c094730b307a6068a7ebe495443d5a26e2de953883b01bbf1fe63ec0c', 'side': 'SELL', 'horizons': {'20': {'status': 'not_matured', 'return': None}, '40': {'status': 'not_matured', 'return': None}, '60': {'status': 'not_matured', 'return': None}}, 'not_personal_pnl': True}, {'signal_id': '533835fed968b184e95028e650a87c2a9d264f3eb2d1e0ce920ea97c866297a6', 'side': 'SELL', 'horizons': {'20': {'status': 'not_matured', 'return': None}, '40': {'status': 'not_matured', 'return': None}, '60': {'status': 'not_matured', 'return': None}}, 'not_personal_pnl': True}]
有效性/费用/路径限制=['small_forward_sample_not_efficacy_proof', 'costs_are_scenarios', 'daily_low_close_path_not_intrabar_order']
无交易时逐卡区分硬数据缺口、规则未满足、执行证据不足；不因低置信度封口。

按版本分账向前比较；旧MT12规则每日报并列观察，不虚构旧规则成交收益
人工提交reason/parent/version/frozen timing/effective_at；保留v1向前并行，禁止回填与自动调参
不自动调参；对新版本只作生效日之后的独立模拟。

🧪 **公司研究复核｜TA-1.0，独立于技术动作**
首轮仅为研究机制验证，不是公司签审或收益认证；短期目标交易日见冻结输入，短期及长期观察均未成熟。
|标的|协议检查|持仓研究方向|未持有研究方向|分歧/缺口|
|---|---|---|---|---|
|德明利 001309.SZ|blocked|未知，待补证与复核|暂不形成新研究结论|research_coverage_not_searched|
|兆易创新 603986.SH|blocked|未知，待补证与复核|暂不形成新研究结论|research_coverage_not_searched|
|腾景科技 688195.SH|blocked|未知，待补证与复核|暂不形成新研究结论|research_coverage_not_searched|
|德福科技 301511.SZ|blocked|未知，待补证与复核|暂不形成新研究结论|research_coverage_not_searched|
|宁德时代 300750.SZ|blocked|未知，待补证与复核|暂不形成新研究结论|research_coverage_not_searched|
|工业富联 601138.SH|blocked|未知，待补证与复核|暂不形成新研究结论|research_coverage_not_searched|
|小米集团 01810.HK|blocked|未知，待补证与复核|暂不形成新研究结论|B:volume_comparison_without_baseline；research_coverage_not_searched|
|美团 03690.HK|blocked|未知，待补证与复核|暂不形成新研究结论|research_coverage_not_searched|
|腾讯控股 00700.HK|blocked|未知，待补证与复核|暂不形成新研究结论|research_coverage_not_searched|

研究run_id：ta10-revision-d3947a666b4113873caa5ed1
归档hash：b978de19609345994e5ad20504ddcdbd572c4b946e4a1cd21f252217b2acb2dd
逐股语义复核见结构化quality回执，最终仍待独立验收；不计算个人盈亏，不将本轮计入正式荐股成功率。
