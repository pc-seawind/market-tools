# Revision2：共同fold截止全事件估值

**修正结论：全71个A入场episode的共同截止估值中，结构＋ATR相对旧退出为负；不能沿用只看双闭合的较好倾向。**
这不是重跑策略或修改参数，而是只读revision1订单，补齐退出资金持有现金与未退出持仓的共同终点。
已退出：已实现净回报＋其后零息现金。未退出：精确fold末收盘估值，只扣已发生入场费。未成交/未知另列，不伪造卖出。
路径为fold内所有收盘及真实入/出场开盘成交估值点；前后现金纳入。不是日内高低排序回撤；持仓期间任何缺价则全路径回撤unknown。

|基准15bps组/规则/口径|总数/有值/未知|共同截止均值|已实现贡献均值|未实现贡献均值|最差截止估值|全路径平均/最差回撤|回撤已知/未知|
|---|---|---:|---:|---:|---:|---|---|
|A/old_ma60_5/entered_events|71/71/0|0.5217%|-2.3182%|2.8399%|-11.2194%|-7.7274%/-19.9261%|71/0|
|A/structure_failure/entered_events|71/71/0|0.3291%|-2.1129%|2.4420%|-13.1930%|-7.9337%/-20.4466%|71/0|
|A/structure_failure_atr/entered_events|71/71/0|0.3567%|-0.1735%|0.5302%|-12.9121%|-6.0636%/-14.0254%|71/0|
|B/old_trigger/entered_events|71/71/0|0.3567%|-0.1735%|0.5302%|-12.9121%|-6.0636%/-14.0254%|71/0|
|B/old_trigger/capital_opportunities|108/105/3|0.2412%|-0.1173%|0.3585%|-12.9121%|-4.1002%/-14.0254%|105/3|
|B/breakout/entered_events|19/19/0|1.1473%|0.3389%|0.8084%|-7.1754%|-5.0910%/-11.3049%|19/0|
|B/breakout/capital_opportunities|108/104/4|0.2096%|0.0619%|0.1477%|-7.1754%|-0.9301%/-11.3049%|104/4|
|B/pullback/entered_events|71/71/0|0.2803%|-0.3203%|0.6006%|-12.0885%|-5.3805%/-14.0254%|71/0|
|B/pullback/capital_opportunities|108/105/3|0.1895%|-0.2166%|0.4061%|-12.0885%|-3.6382%/-14.0254%|105/3|

均值若有unknown只是已知子集均值；全分母均值字段保持null，不能把缺失填0。B机会口径含全部108个股票×fold；无信号且完整可观测者为零息现金，不可观测者unknown。
B各臂触发日期不同；共同截止/机会分母差不是同入场因果效应，也不是资金组合收益。

|基准15bps配对（新版−旧版）|全/已知/未知|估值差|股票×fold描述性95%区间|全路径回撤差（正较好）|
|---|---|---:|---|---:|
|A/structure_failure|71/71/0|-0.1926%|-1.0439%至0.6323%|-0.2062%|
|A/structure_failure_atr|71/71/0|-0.1650%|-1.8275%至1.4318%|1.6638%|
|B/breakout|108/104/4|-0.1288%|-2.7119%至1.7608%|3.0872%|
|B/pullback|108/105/3|-0.0517%|-2.1052%至1.7909%|0.4619%|

revision1双闭合35/71的ATR差约+1.47个百分点；本次71/71约−0.165个百分点。两者回答不同条件问题，不能选择有利的子集结论。
固定种子1201、2000次股票×OOS折交叉重抽样，完整保留未知配对。区间仅描述性，少簇/市场共同冲击/当前vintage偏差不因此消失。

## 全部成本/执行情景及未退出假设卖出费敏感性

|情景/成本/组/臂/分母|已知/总数|共同截止均值|未退出假设卖出费后的均值（非成交）|
|---|---|---:|---:|
|open_price_limit_base_v1|15|A|old_ma60_5|entered_events|71/71|0.5217%|0.4435%|
|open_price_limit_base_v1|15|A|structure_failure|entered_events|71/71|0.3291%|0.2367%|
|open_price_limit_base_v1|15|A|structure_failure_atr|entered_events|71/71|0.3567%|0.3200%|
|open_price_limit_base_v1|15|B|old_trigger|entered_events|71/71|0.3567%|0.3200%|
|open_price_limit_base_v1|15|B|old_trigger|capital_opportunities|105/108|0.2412%|0.2164%|
|open_price_limit_base_v1|15|B|breakout|entered_events|19/19|1.1473%|1.1145%|
|open_price_limit_base_v1|15|B|breakout|capital_opportunities|104/108|0.2096%|0.2036%|
|open_price_limit_base_v1|15|B|pullback|entered_events|71/71|0.2803%|0.2456%|
|open_price_limit_base_v1|15|B|pullback|capital_opportunities|105/108|0.1895%|0.1661%|
|open_price_limit_base_v1|30|A|old_ma60_5|entered_events|71/71|0.2988%|0.1426%|
|open_price_limit_base_v1|30|A|structure_failure|entered_events|71/71|0.1209%|-0.0636%|
|open_price_limit_base_v1|30|A|structure_failure_atr|entered_events|71/71|0.0928%|0.0195%|
|open_price_limit_base_v1|30|B|old_trigger|entered_events|71/71|0.0928%|0.0195%|
|open_price_limit_base_v1|30|B|old_trigger|capital_opportunities|105/108|0.0627%|0.0132%|
|open_price_limit_base_v1|30|B|breakout|entered_events|19/19|0.8771%|0.8116%|
|open_price_limit_base_v1|30|B|breakout|capital_opportunities|104/108|0.1602%|0.1483%|
|open_price_limit_base_v1|30|B|pullback|entered_events|71/71|0.0146%|-0.0547%|
|open_price_limit_base_v1|30|B|pullback|capital_opportunities|105/108|0.0099%|-0.0370%|
|open_price_limit_base_v1|50|A|old_ma60_5|entered_events|71/71|0.0026%|-0.2572%|
|open_price_limit_base_v1|50|A|structure_failure|entered_events|71/71|-0.1556%|-0.4626%|
|open_price_limit_base_v1|50|A|structure_failure_atr|entered_events|71/71|-0.2579%|-0.3798%|
|open_price_limit_base_v1|50|B|old_trigger|entered_events|71/71|-0.2579%|-0.3798%|
|open_price_limit_base_v1|50|B|old_trigger|capital_opportunities|105/108|-0.1744%|-0.2568%|
|open_price_limit_base_v1|50|B|breakout|entered_events|19/19|0.5181%|0.4091%|
|open_price_limit_base_v1|50|B|breakout|capital_opportunities|104/108|0.0946%|0.0747%|
|open_price_limit_base_v1|50|B|pullback|entered_events|71/71|-0.3384%|-0.4537%|
|open_price_limit_base_v1|50|B|pullback|capital_opportunities|105/108|-0.2288%|-0.3068%|
|any_limit_touch_conservative_v1|15|A|old_ma60_5|entered_events|71/71|0.5217%|0.4435%|
|any_limit_touch_conservative_v1|15|A|structure_failure|entered_events|71/71|0.3291%|0.2367%|
|any_limit_touch_conservative_v1|15|A|structure_failure_atr|entered_events|71/71|0.3301%|0.2933%|
|any_limit_touch_conservative_v1|15|B|old_trigger|entered_events|71/71|0.3301%|0.2933%|
|any_limit_touch_conservative_v1|15|B|old_trigger|capital_opportunities|105/108|0.2232%|0.1984%|
|any_limit_touch_conservative_v1|15|B|breakout|entered_events|19/19|0.7818%|0.7490%|
|any_limit_touch_conservative_v1|15|B|breakout|capital_opportunities|104/108|0.1428%|0.1368%|
|any_limit_touch_conservative_v1|15|B|pullback|entered_events|71/71|0.0334%|-0.0013%|
|any_limit_touch_conservative_v1|15|B|pullback|capital_opportunities|105/108|0.0226%|-0.0009%|
|any_limit_touch_conservative_v1|30|A|old_ma60_5|entered_events|71/71|0.2988%|0.1426%|
|any_limit_touch_conservative_v1|30|A|structure_failure|entered_events|71/71|0.1209%|-0.0636%|
|any_limit_touch_conservative_v1|30|A|structure_failure_atr|entered_events|71/71|0.0662%|-0.0071%|
|any_limit_touch_conservative_v1|30|B|old_trigger|entered_events|71/71|0.0662%|-0.0071%|
|any_limit_touch_conservative_v1|30|B|old_trigger|capital_opportunities|105/108|0.0448%|-0.0048%|
|any_limit_touch_conservative_v1|30|B|breakout|entered_events|19/19|0.5127%|0.4472%|
|any_limit_touch_conservative_v1|30|B|breakout|capital_opportunities|104/108|0.0937%|0.0817%|
|any_limit_touch_conservative_v1|30|B|pullback|entered_events|71/71|-0.2316%|-0.3009%|
|any_limit_touch_conservative_v1|30|B|pullback|capital_opportunities|105/108|-0.1566%|-0.2034%|
|any_limit_touch_conservative_v1|50|A|old_ma60_5|entered_events|71/71|0.0026%|-0.2572%|
|any_limit_touch_conservative_v1|50|A|structure_failure|entered_events|71/71|-0.1556%|-0.4626%|
|any_limit_touch_conservative_v1|50|A|structure_failure_atr|entered_events|71/71|-0.2843%|-0.4063%|
|any_limit_touch_conservative_v1|50|B|old_trigger|entered_events|71/71|-0.2843%|-0.4063%|
|any_limit_touch_conservative_v1|50|B|old_trigger|capital_opportunities|105/108|-0.1923%|-0.2747%|
|any_limit_touch_conservative_v1|50|B|breakout|entered_events|19/19|0.1551%|0.0462%|
|any_limit_touch_conservative_v1|50|B|breakout|capital_opportunities|104/108|0.0283%|0.0084%|
|any_limit_touch_conservative_v1|50|B|pullback|entered_events|71/71|-0.5836%|-0.6989%|
|any_limit_touch_conservative_v1|50|B|pullback|capital_opportunities|105/108|-0.3946%|-0.4726%|

敏感性仅从仍持仓的终点估值扣假设卖出费，不创建exit、不改成交日期、不叫已实现净收益；已退出不重复收费。
事件记录保留完整财富路径、已实现/未实现分量、不能成交及缺价原因。事件统计不是有资金约束的组合收益。
