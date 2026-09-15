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
|德明利 001309.SZ|pass|核查经营现金流改善、存货去化与应收回款，验证高增长能否转化为现金。|研究存储行业景气拐点信号与存货跌价计提对利润的潜在冲击。|缺少下一期经营现金流、存货去化与回款数据，新披露日程未知；净资产收益率供应商口径与半年报加权口径差异待核。；缺少估值对比与同业财务质量材料，无法评估安全边际。|
|兆易创新 603986.SH|pass|核验存储量价齐升与扣非盈利质量在后续季度报告中的持续性，重点关注存货与公允价值收益变化。|研究利基存储供给恢复情景下的盈利弹性回撤及估值周期顶部风险。|缺少估值水平、分产品毛利率及存储价格后续走势证据，无法判断当前价格是否已反映高增业绩。；历史假设文件仅为用户确认持仓，无可核验的投资逻辑支柱；三季报披露日程未知。|
|腾景科技 688195.SH|pass|验证光通信收入增长持续性及股份支付消退后净利率走势|核查完整公告中客户结构、直供进展与产能项目披露，补充估值证据|OCS及谷歌、英伟达供应链关系的公司披露原文未在选段出现，需查完整公告；缺少估值水平与同业比较证据，无法判断当前价格是否高估|
|德福科技 301511.SZ|pass|核验持仓逻辑：跟踪经营现金流改善、毛利率走势与杠杆约束，验证高增长可持续性。|研究锂电铜箔供需与加工费周期对盈利质量的影响。|缺少铜箔加工费、铜价及同业毛利率对比数据，无法判断毛利率趋势。；缺少下一期定期报告披露日程及管理层现金流指引，新披露日未知。|
|宁德时代 300750.SZ|pass|验证储能与动力电池收入增速及毛利率、现金流趋势能否延续，跟踪后续定期报告|补充同业估值与月度装机数据，检验估值安全边际命题|缺少同业估值比较与行业渗透率的可引用原文证据；缺少次日盘前消息与下一季度业绩披露日程|
|工业富联 601138.SH|pass|跟踪AI服务器收入占比、ASIC新案进展与存货周转，验证增长持续性。|补充同业估值对比与大客户资本开支指引，检验估值合理性与集中度风险。|缺少同业估值对比数据，无法评估安全边际。；下一季度披露日程与订单指引未知，业绩持续性验证时点待确认。|
|小米集团 01810.HK|pass|核查成本压力与竞争对毛利率的边际影响及回购执行进度|补充同业估值对比与行业景气数据，检验回购与现金流改善能否对冲利润下滑|缺同业估值对比、市盈率与行业景气数据，估值合理性无法判断；下一季度披露日程未知，成本压力缓解路径无证据|
|美团 03690.HK|pass|验证盈利转正的现金流含量与分部利润率走势，跟踪后续定期报告|收集同业估值对比、行业竞争强度与板块资金流向数据以检验估值合理性|缺少同业估值对比与行业景气数据，估值合理性命题无证据；缺少下一季度披露日程，盈利持续性与现金流收敛验证时点未知|
|腾讯控股 00700.HK|pass|验证业绩增长持续性：跟踪后续季度营收与净利同比、毛利率趋势及剔除算力预付款后的自由现金流。|研究人工智能资本开支强度对现金流与利润率的持续影响，以及回购节奏与估值安全边际的同业对比。|选段已含部分分部收入信息，但缺完整分部利润明细与估值同业对比，安全边际无法判断。；下一季度业绩披露日程未知，行业景气度与板块资金数据缺失，净现金表格列头缺失。|

研究run_id：ta10-revision-d3947a666b4113873caa5ed1
归档hash：b978de19609345994e5ad20504ddcdbd572c4b946e4a1cd21f252217b2acb2dd
逐股语义复核见结构化quality回执，最终仍待独立验收；不计算个人盈亏，不将本轮计入正式荐股成功率。
