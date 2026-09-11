# Revision2：共同截止全事件估值，取代双闭合子集主结论

工单 work_9d513102eda442f41a04；新增分析，不改trial1参数、scope、订单或revision1结果。

核心结果及限制在 `reports/mt12-history-20260912-r2/DELIVERY.md`。旧报告保留历史原文，
其中根据双闭合子集提出的ATR较好倾向不应再作为主结论。

## 唯一新增A股分析入口（离线、只读原结果）

```bash
cd /home/emox/work/projects/market-tools
PY=/home/emox/work/homespace/.venv/bin/python
$PY -m mt1.history_cutoff \
  --source reports/mt12-history-20260912 \
  --out reports/mt12-history-20260912-r2
$PY -m pytest tests -q
```

运行约数秒。source读取已提交的panel gzip并校验压缩/解压hash，不依赖可能变动的
派生panel.json。original full-results/冻结策略合同hash必须与新增analysis-contract相同；
同时校验revision1-before.json的605文件。输出仅写新目录，拒绝写入原目录/原目录子树。

## 会计口径与分母

- 每个入场事件初始资金=1，实际买入股数=1/(调整开盘价×(1+买入费))。
- 已真实模型退出：扣原卖出费，剩余资金直到同fold截止保持零息现金，不再投资。
- 未退出：用**精确截止session收盘**标记，不扣未发生卖出费，不生成exit。
- realized_component 是已闭合事件完整净回报对全事件均值的贡献；未闭合事件贡献放
  unrealized_component（含已发生的入场费），不是税务/会计账簿分类。
- 额外提供每笔实际买卖费占初始资金金额；未退出假设卖出费敏感性明确不是成交净收益。
- 每个事件路径含入场前现金、实际买/卖open估值、持仓各session close、退出后现金。
  不猜日内high/low先后。持仓期内部缺价则full_path_drawdown=null，即使终点价已知；
  只观察到部分路径的回撤另列。退出后缺股票报价不影响已有现金。
- 执行未知不能假定仍然持股/已经卖出；估值和全路径回撤null。未入场且确知保持现金
  在capital_opportunity口径为0，但不伪造为0收益入场交易。两类状态单列。
- A按episode配对；实际入场不一致则配对差unknown，不能归因为退出规则。
- B补齐全部108股票×fold×每臂：无信号且完整可观测=现金0；因缺价/暖机不可观测的
  无信号=unknown；同时保留原有信号/入场事件分母。B不是同入场因果比较。
- unknown不填0、不删除配对；mean_known仅已知子集均值，有缺口时全分母均值保持null。
- 所有原成本15/30/50bps、两执行情景保留；同股×时间簇固定seed1201、2000次重抽样。
  同时给请求/有效重抽样数、股票/时间簇数和已知/未知配对覆盖。均为描述性区间。

## 港股免费路径审计入口

```bash
$PY -m mt1.hk_free_probe --source reports/mt12-history-20260912 \
  --out reports/mt12-history-20260912-r2/hk --offline
```

离线复算已归档Yahoo、Sina、Tencent、Tushare日历及原2021Tushare raw的交叉核验。
`free-path-audit.json` 是首层可得性；**`extended-free-audit.json` 与HK-DECISION.md是
最终补充结论**：因子/宽基/日历已找到可用免费路径，不能继续笼统称缺权限。

HK完整执行回放仍未交付：具体跨源价格冲突、零量半日市、个股开盘停复牌证据缺口
与尚未实现HK执行adapter见HK-DECISION.md；不把“取到价格”冒充“真实执行已验证”。
不建议购买宽泛日线/因子包；若必须采购，只有最小开盘资格/更正价格缺口需要询价，
须用户另行授权。本脚本不会调用曾被拒绝的hk_adjfactor/hk_daily_adj，也没有交易接口。
