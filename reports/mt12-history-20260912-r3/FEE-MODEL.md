# HK费用与历史规则版本

本轮沿用原HK基准**单边25bps混合费用**，新冻结的50/75bps为压力情景。全部臂同成本、不搜索收益最优值。25bps不是某券商报价，也不是任意小金额订单的费用上限；单位事件资本只用于标准化，不代表真的以HKD1下单。

已归档原始官方规则：
- `raw/stamp_rates.raw`：税务局历史税率。2021-08-01至2023-11-16股票印花税每边0.13%，之前及之后0.1%；不足HKD1部分向上取整。
- `raw/transaction_fees.raw`：2022-01-01起AFRC每边0.00015%；2023-01-01起交易费0.005%→0.00565%，取消每宗HKD0.50交易系统使用费；SFC每边0.0027%。
- `raw/settlement_change.raw`：2025-06-30起股份交收费移除上下限，改0.0042%；此前0.002%、最低HKD2最高HKD100。

这些变更不被错误替换成A股印花税/单边征税。25/50/75bps是混合成本情景，**没有另加上述费用导致重复收费**，也没有伪称精确的历史手数/券商账单。固定金额最低费、税额取整、经纪佣金和冲击取决于实际规模，本轮不实现资金组合，不能就小额实盘成本作保证。

HK T+2是清算而非T+1禁售；自有现金足额的独立事件允许同session持股转售，策略仍坚持上一完成session信号/下一session执行，无同bar偷跑。未退出事件不假造卖出：共同截止只扣实际买入费，假设卖费另列敏感性。

历史规则依据：
[税务局](https://www.ird.gov.hk/eng/pdf/sd_stock_rates.pdf)、
[港交所交易费用](https://www.hkex.com.hk/Services/Rules-and-Forms-and-Fees/Fees/Securities-%28Hong-Kong%29/Trading/Transaction?sc_lang=en)、
[2025交收费变更生效说明](https://www.hkexgroup.com/-/media/HKEX-Group-Site/Ir/Key-Revenue-Drivers/1H-2025-Key-Revenue-Driver.pdf)。
