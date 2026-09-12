**第三段｜技术策略信号（模拟跟踪，非自动实盘）**
执行模型：strict-open-v1（observed-quote-v1仅报价估值模拟，不是可成交证明）。
前两段行情播报与趋势/观点对照由原日报原样保留。公司研究与技术动作独立；不指令真实金额/股数。
合成演示，非真实行情/成交。
|代码/名称|技术动作/适用对象|公司研究（独立）|信号回执/模拟执行|规则/风险线|硬缺口|
|---|---|---|---|---|---|
|001309.SZ |HOLD/隔离模拟仓|未提供/不由技术信号推断|129cf0392989:BUY/filled/isolated_simulation_next_observed_open|held_no_exit/{"atr_stop": 104.4025, "structure": 103.95}|无|

001309.SZ：{"change": {"from": "HOLD", "to": "HOLD"}, "next_review": "next_completed_market_session; pending execution requires contemporaneous open collector", "old_rules": {"entry": {"reasons": ["market_benchmark_unavailable_no_CSI300_fallback"], "status": "unknown"}, "exit": {"rule": "5_completed_closes_below_each_MA60", "status": "monitor"}}, "price_basis": "completed_raw_close; internal=raw*vendor_factor", "soft_annotations": {"RS": {"broad": {"RS_not_RSI": true, "reason": "benchmark_missing_or_incompatible_market_dates_membership", "status": "unknown", "value": null}, "industry": {"RS_not_RSI": true, "reason": "benchmark_missing_or_incompatible_market_dates_membership", "status": "unknown", "value": null}}, "confidence": 0.01, "confidence_is_veto": false, "efficacy": "experimental_not_validated"}, "source_sha256": "70ab35a2594a61888fe9b767b901e659b6e529f13f65e235e13d3c2748cc5668", "triggered_at": "2026-09-16T18:00:00+08:00", "validity": "signal immutable; unfilled order 3 completed sessions; next legal observed open only", "version": "signal-policy-v1"}
