# MT14 历史对比正式交付表（待投资域独立验收）

关联 work_9ad289b0ff80eb2f74c8；本次收尾 work_8cdf9bc302eb707e4cc3。

本表从 r5/results.json 直接生成。r5 与 r4 三核心文件 hash 相同；本轮只变更模块布局/独立源码指纹，无计算修正、择参或重采。

## 范围与解释

- 2021-01-04—2025-12-31，固定 12 A + 3 HK；基本面仅 12 A，与当前 9 持仓交集 0。不是当前持仓收益，也不是全市场 PIT。
- 仅 ROE ≥10→≥12、突破量比 1.2→1.4；其他冻结参数不变。CN/HK 单边 15/25 bps，另 2/3 倍压力。
- 表内事件指标是已知入选子集；共同机会差是两臂同一机会均有值的净差，不是事件均值相减。未知不当现金，不年化。
- 技术执行保留退出后现金；capital 是同初始资本账户已有净值的滚动窗口，并非从该窗口新建仓。路径回撤不是组合最大回撤。
- 基本面 60 日两个 ROE 差异事件尚未成熟，旧新已知均值相同不能解释为无效应。

## 20/40/60 日收益、风险和共同分母

|类别/天数|旧/新有值事件|旧均值/中位数|新均值/中位数|均值差(百分点)|旧/新正收益占比|旧/新最差路径回撤|共同已知/总机会|共同机会均值差(百分点)|
|---|---|---|---|---|---|---|---|---|
|fundamental/20|51/49|0.5942%/-0.9347%|0.5819%/-0.9347%|-0.0123|45.0980%/44.8980%|-17.6184%/-17.6184%|682/720|-0.0026|
|fundamental/40|49/47|2.2260%/2.7307%|2.2396%/2.7486%|0.0136|71.4286%/70.2128%|-21.8045%/-21.8045%|680/720|-0.0056|
|fundamental/60|45/45|4.3908%/2.5949%|4.3908%/2.5949%|0.0000|64.4444%/64.4444%|-22.6128%/-22.6128%|676/720|0.0000|
|technical_price/20|339/335|-0.3281%/-0.6126%|-0.3790%/-0.6126%|-0.0509|45.4277%/45.3731%|-25.5290%/-25.5290%|15798/18228|-0.0010|
|technical_price/40|334/330|1.0168%/0.3507%|0.8366%/0.1383%|-0.1802|51.4970%/50.3030%|-28.4907%/-28.4907%|15793/18228|-0.0040|
|technical_price/60|331/327|1.2533%/1.2270%|1.1061%/0.8944%|-0.1472|53.1722%/52.9052%|-31.1918%/-31.1918%|15790/18228|-0.0034|
|technical_execution/20|129/125|-1.3404%/-1.9033%|-1.3594%/-2.2261%|-0.0190|34.8837%/36.8000%|-18.7406%/-18.7406%|15445/18228|0.0004|
|technical_execution/40|128/124|-1.2075%/-2.4106%|-1.1921%/-2.5802%|0.0154|30.4688%/30.6452%|-18.7406%/-18.7406%|15444/18228|0.0011|
|technical_execution/60|126/122|-1.0257%/-2.4106%|-1.0464%/-2.5802%|-0.0208|31.7460%/31.9672%|-18.7406%/-18.7406%|15442/18228|0.0014|
|technical_capital/20|17536/17536|-0.1470%/0.0000%|-0.1372%/0.0000%|0.0098|9.8141%/9.8825%|-22.4420%/-22.4420%|17536/18228|0.0098|
|technical_capital/40|17236/17236|-0.2887%/0.0000%|-0.2691%/0.0000%|0.0197|11.1975%/11.2207%|-24.1224%/-24.1224%|17236/18228|0.0197|
|technical_capital/60|16936/16936|-0.4113%/0.0000%|-0.3815%/0.0000%|0.0298|12.1516%/12.2107%|-24.9227%/-24.9227%|16936/18228|0.0298|

## 每臂机会分解（基准成本）

现金仅 unselected_cash；unknown 含未成熟，下面另列以便审计，不可相加两次。occupied_slot 不是现金；open_mark 是未闭合标记，保留而不强造卖出。capital 的空仓收益属于账户窗口，详见逐日资本路径。

|类别/天数/臂|总机会|已知|未知合计|现金|未成熟|占用槽|未闭合标记|状态分解|
|---|---|---|---|---|---|---|---|---|
|fundamental/20/old|720|682|38|631|2|0|0|{"not_matured": 2, "price_observed_not_fill": 51, "unknown_fundamental_input": 36, "unselected_cash": 631}|
|fundamental/20/new|720|682|38|633|2|0|0|{"not_matured": 2, "price_observed_not_fill": 49, "unknown_fundamental_input": 36, "unselected_cash": 633}|
|fundamental/40/old|720|680|40|631|4|0|0|{"not_matured": 4, "price_observed_not_fill": 49, "unknown_fundamental_input": 36, "unselected_cash": 631}|
|fundamental/40/new|720|680|40|633|4|0|0|{"not_matured": 4, "price_observed_not_fill": 47, "unknown_fundamental_input": 36, "unselected_cash": 633}|
|fundamental/60/old|720|676|44|631|8|0|0|{"not_matured": 8, "price_observed_not_fill": 45, "unknown_fundamental_input": 36, "unselected_cash": 631}|
|fundamental/60/new|720|678|42|633|6|0|0|{"not_matured": 6, "price_observed_not_fill": 45, "unknown_fundamental_input": 36, "unselected_cash": 633}|
|technical_price/20/old|18228|15798|2430|15459|4|0|0|{"not_matured": 4, "price_observed_not_fill": 339, "unknown_warmup_or_history_gap": 2426, "unselected_cash": 15459}|
|technical_price/20/new|18228|15798|2430|15463|4|0|0|{"not_matured": 4, "price_observed_not_fill": 335, "unknown_warmup_or_history_gap": 2426, "unselected_cash": 15463}|
|technical_price/40/old|18228|15793|2435|15459|9|0|0|{"not_matured": 9, "price_observed_not_fill": 334, "unknown_warmup_or_history_gap": 2426, "unselected_cash": 15459}|
|technical_price/40/new|18228|15793|2435|15463|9|0|0|{"not_matured": 9, "price_observed_not_fill": 330, "unknown_warmup_or_history_gap": 2426, "unselected_cash": 15463}|
|technical_price/60/old|18228|15790|2438|15459|12|0|0|{"not_matured": 12, "price_observed_not_fill": 331, "unknown_warmup_or_history_gap": 2426, "unselected_cash": 15459}|
|technical_price/60/new|18228|15790|2438|15463|12|0|0|{"not_matured": 12, "price_observed_not_fill": 327, "unknown_warmup_or_history_gap": 2426, "unselected_cash": 15463}|
|technical_execution/20/old|18228|15464|2764|15335|3|2384|38|{"closed_then_cash": 91, "not_matured": 3, "occupied_slot_see_capital_table": 2384, "open_mark_not_closed": 38, "unknown_execution_state": 377, "unselected_cash": 15335}|
|technical_execution/20/new|18228|15460|2768|15335|3|2388|38|{"closed_then_cash": 87, "not_matured": 3, "occupied_slot_see_capital_table": 2388, "open_mark_not_closed": 38, "unknown_execution_state": 377, "unselected_cash": 15335}|
|technical_execution/40/old|18228|15463|2765|15335|4|2384|11|{"closed_then_cash": 117, "not_matured": 4, "occupied_slot_see_capital_table": 2384, "open_mark_not_closed": 11, "unknown_execution_state": 377, "unselected_cash": 15335}|
|technical_execution/40/new|18228|15459|2769|15335|4|2388|12|{"closed_then_cash": 112, "not_matured": 4, "occupied_slot_see_capital_table": 2388, "open_mark_not_closed": 12, "unknown_execution_state": 377, "unselected_cash": 15335}|
|technical_execution/60/old|18228|15461|2767|15335|6|2384|5|{"closed_then_cash": 121, "not_matured": 6, "occupied_slot_see_capital_table": 2384, "open_mark_not_closed": 5, "unknown_execution_state": 377, "unselected_cash": 15335}|
|technical_execution/60/new|18228|15457|2771|15335|6|2388|5|{"closed_then_cash": 117, "not_matured": 6, "occupied_slot_see_capital_table": 2388, "open_mark_not_closed": 5, "unknown_execution_state": 377, "unselected_cash": 15335}|
|technical_capital/20/old|18228|17536|692|0|315|0|0|{"funded_capital_window": 17536, "not_matured": 315, "unknown_signal_availability": 377}|
|technical_capital/20/new|18228|17536|692|0|315|0|0|{"funded_capital_window": 17536, "not_matured": 315, "unknown_signal_availability": 377}|
|technical_capital/40/old|18228|17236|992|0|615|0|0|{"funded_capital_window": 17236, "not_matured": 615, "unknown_signal_availability": 377}|
|technical_capital/40/new|18228|17236|992|0|615|0|0|{"funded_capital_window": 17236, "not_matured": 615, "unknown_signal_availability": 377}|
|technical_capital/60/old|18228|16936|1292|0|915|0|0|{"funded_capital_window": 16936, "not_matured": 915, "unknown_signal_availability": 377}|
|technical_capital/60/new|18228|16936|1292|0|915|0|0|{"funded_capital_window": 16936, "not_matured": 915, "unknown_signal_availability": 377}|

## 20/40/60 日费用压力

|类别/天数/费用倍数|旧净均值|新净均值|共同已知/总数|共同净差(百分点)|
|---|---|---|---|---|
|fundamental/20/1|0.5942%|0.5819%|682/720|-0.0026|
|fundamental/20/2|0.2929%|0.2806%|682/720|-0.0017|
|fundamental/20/3|-0.0075%|-0.0198%|682/720|-0.0009|
|fundamental/40/1|2.2260%|2.2396%|680/720|-0.0056|
|fundamental/40/2|1.9197%|1.9333%|680/720|-0.0047|
|fundamental/40/3|1.6144%|1.6280%|680/720|-0.0038|
|fundamental/60/1|4.3908%|4.3908%|676/720|0.0000|
|fundamental/60/2|4.0781%|4.0781%|676/720|0.0000|
|fundamental/60/3|3.7663%|3.7663%|676/720|0.0000|
|technical_price/20/1|-0.3281%|-0.3790%|15798/18228|-0.0010|
|technical_price/20/2|-0.7271%|-0.7765%|15798/18228|-0.0009|
|technical_price/20/3|-1.1243%|-1.1723%|15798/18228|-0.0007|
|technical_price/40/1|1.0168%|0.8366%|15793/18228|-0.0040|
|technical_price/40/2|0.6119%|0.4337%|15793/18228|-0.0039|
|technical_price/40/3|0.2087%|0.0325%|15793/18228|-0.0037|
|technical_price/60/1|1.2533%|1.1061%|15790/18228|-0.0034|
|technical_price/60/2|0.8472%|0.7018%|15790/18228|-0.0032|
|technical_price/60/3|0.4427%|0.2993%|15790/18228|-0.0031|
|technical_execution/20/1|-1.3404%|-1.3594%|15445/18228|0.0004|
|technical_execution/20/2|-1.6530%|-1.6678%|15445/18228|0.0005|
|technical_execution/20/3|-1.9644%|-1.9750%|15445/18228|0.0005|
|technical_execution/40/1|-1.2075%|-1.1921%|15444/18228|0.0011|
|technical_execution/40/2|-1.5645%|-1.5442%|15444/18228|0.0012|
|technical_execution/40/3|-1.9201%|-1.8950%|15444/18228|0.0013|
|technical_execution/60/1|-1.0257%|-1.0464%|15442/18228|0.0014|
|technical_execution/60/2|-1.3906%|-1.4088%|15442/18228|0.0015|
|technical_execution/60/3|-1.7540%|-1.7696%|15442/18228|0.0015|
|technical_capital/20/1|-0.1470%|-0.1372%|17536/18228|0.0098|
|technical_capital/20/2|-0.2021%|-0.1903%|17536/18228|0.0118|
|technical_capital/20/3|-0.2570%|-0.2431%|17536/18228|0.0138|
|technical_capital/40/1|-0.2887%|-0.2691%|17236/18228|0.0197|
|technical_capital/40/2|-0.3973%|-0.3735%|17236/18228|0.0238|
|technical_capital/40/3|-0.5054%|-0.4774%|17236/18228|0.0279|
|technical_capital/60/1|-0.4113%|-0.3815%|16936/18228|0.0298|
|technical_capital/60/2|-0.5729%|-0.5368%|16936/18228|0.0362|
|technical_capital/60/3|-0.7334%|-0.6910%|16936/18228|0.0424|

## 测量限制及保留失败

- 185 次成功补采、1 次 HK 复权权限拒绝；早轮 186 次 credential_missing 仍在原目录。本轮不发网络采集请求。
- 当前存续预选样本，不是历史全市场成分或当前9持仓收益。
- 财报为本次厂商修订版本，仅公告日期过滤，不是原始公告版本PIT；ROE为原screen披露口径，未改为TTM。
- 历史名称/ST缺口逐月留标记；当前上市元数据不证明无退市幸存者偏差。
- 目标2021—2025；不足120日预热的交易日保留unknown；缺口后原状态无法连续则保留unknown。
- 2025末无2026延伸价格，20/40/60未成熟不删；已知子集均值不代表全分母。
- 港股沿用三源价格核对和公告重建资格，非逐笔/队列/成交量保证。
- 复权价收益不是现金红利全收益；零息现金、相同名义资本槽，事件重叠不可当组合年化。
- 日级估计复用MT13 ensure/renew/expire/transition恢复函数，同SELL信号分次尝试；缺入场指标仍核对冻结风险线退出。
- technical_capital为每股每臂同初始10000资本逐日复投账本；窗口从下一收盘账户净值起量度，因此不是从零建仓的事件回报。已占用槽不再伪记现金。
- 2024—2025仅固定后段描述性检查，非严格从未见过的OOS。

年度/2024—2025后段表见 RESULTS.md；更完整统计含平均路径回撤、gross、各状态分解见 results.json；逐事件、订单恢复和90个账户路径见 samples.json.gz。

工程验证终态另见 DELIVERY.md；本表不自行宣布验收通过。
