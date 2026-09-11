# Revision3：港股真实执行模型与共同截止 A/B

同单 `work_9d513102eda442f41a04`，接续 topic 3082。只新增 HK，不改已独立复验通过的 A 股实现/结果。

## 唯一回放入口

```bash
cd /home/emox/work/projects/market-tools
python3 -m mt1.hk_history --out reports/mt12-history-20260912-r3 --decode
python3 -m mt1.hk_history --out reports/mt12-history-20260912-r3 --verify
/home/emox/work/homespace/.venv/bin/python -m pytest tests -q
```

回放完全离线，复用 r1/r2/r3 的原始响应；`--decode` 以已安装 akshare 的新浪解码函数解码原始压缩行情，**没有网络访问**。不前填、不去重平价日、不删除半日市。依赖 Python3、bs4、pdftotext；解码另需现有 akshare/py-mini-racer。普通回放省略 `--decode` 可使用已封存解码结果。

公开数据采集也可复跑（默认命中已封存缓存）：

```bash
python3 -m mt1.hk_history_collect \
  --plan reports/mt12-history-20260912-r3/public-collection-plan.json \
  --out reports/mt12-history-20260912-r3 --offline
```

删除 `--offline` 只抓缺失公开响应，不覆盖缓存；新vintage应输出到新的 sibling 目录，不混入本轮。计划不含 Tushare/凭证接口。r3另保留一次窄日期 `hk_daily` 限流原文，无重试；新浪第三源已解决争议，不再消耗此配额。禁止调用无权限因子接口。

## 数据及执行

- 样本沿用汇丰00005、港交所00388、中移动00941，2021—2025。原 timing 合同逐字段相同；相同训练/清洗/OOS边界及信号定义。不拟合、不改阈值。
- `frozen-contract.json` 在新价格裁决和回放前冻结价差容差 HKD0.011、正成交量门禁、第三源全OHLC支持规则、费用25/50/75bps和执行情景。没有因收益换源。
- `price-decisions.json` 覆盖全部3,684股票日；34个争议日全部新浪支持腾讯，逐值记录采用/拒绝依据。复用r1汇丰2021原价作为额外交叉证据，**不是把缺失凭空变可交易**。
- 新浪原始历史日K `klc2_kl.js` 真实字节归档、离线解码；三股因子用r2封存新浪链归一至2025末，r2与Yahoo归一因子交叉核验继续保留。更正错误raw close不再反向依赖该错误close构造因子。
- HKEXnews完整18个股票×年度公告（含2020期初前一年），共3,474条。每份校验股票、年份、NEWS_ID去重、recordCnt/loadedRecord、hasNextRow=false。三个股票各自再查Resumption/Suspension/Trading Halt，共9个分类请求，与全量分类核对均无对应公告。没有需要隐去的股票级停复牌时间。
- **资格是有条件的披露重建，不是交易所逐tick状态证明。** 非空全量公告＋强制停复牌披露规则＋专项分类复核＋开市日历/市场例外构成其依据，不能只用空搜索或正日成交量。发现正停复牌公告但未解出区间时整股unknown，不擅自放行；合成测试覆盖此类门禁。
- 五份年度官方假期表独立构造日历，剔除五个官方全日停市例外后，与已有授权1228开市日期/HSI完全一致。另10个半日市、三个延迟开市（20210628 13:30；20220825 13:00；20231009 14:00）、20221102 13:55提前停市逐session落表。20240923起恶劣天气不停市规则有明确版本，不向前套用。
- 日open是**该session第一价格的执行估计**；通常是开盘竞价而非保证9:30成交，正常时间窗口09:20—09:30，时间字段明确不是tick时刻。上一完成session信号→下一合资格session，不在信号close成交。跳空直接用raw open/因子，不按止损阈值补价。
- HK无CN固定涨跌停或T+1禁售；T+2清算不当作持有锁。事件假设自有现金足额，非可循环资金账户。没有券商/生产交易接口。
- VCM不是日涨跌幅限制。归档官方VCM历史有非空全市场记录，三股期间无命中；保守情景是有触发当日全部跳过的**事后执行压力测试**，本样本与基准相同，不解释为已经验证VCM场景收益鲁棒。

## 结果与证据入口

- `HK-RESULTS.md`：共同截止主比较、费用敏感性、B全27机会分母。
- `full-results.json`：762个组/臂/费用/执行情景事件行，510个闭合行；这是含费用/情景及A/B复用的行数，**不是510个独立交易**。基准按臂闭合：A旧13/结构8/ATR18；B旧18/突破8/回调20。
- `common-cutoff-results.json`：每个事件的现金/持仓完整财富路径、已实现/未实现分量、全路径回撤、未退出假设卖费、配对、全机会分母、分层及unknown。
- `session-eligibility.json`：3,684股票日开盘资格重建、可用性、半日市、延迟开盘、公告hash及来源。
- `protected-before.json`/`inputs-manifest.json`：A股与旧研究保护、此次输入封存；`verification.json`：逐成交/OOS/费用/同入场核验，**不是独立验收**。
- `reproduction.json`：两轮离线六份核心结果逐字节比对。

## 保持不变的结论边界

这是当前vintage、有幸存者偏差的小样本价格择时诊断，不是PIT选股/实际用户持仓/现金分红组合收益。日级模型不证明盘口容量、队列成交、券商最低佣金/手数/结算复用。公告数据不是官方OMD逐tick状态日志，披露遗漏/迟报风险仍在。未知不填0；本次可观测分母完整也不能证明无任何历史遗漏。只有3个股票簇，描述性bootstrap区间不可用于自动晋级。
