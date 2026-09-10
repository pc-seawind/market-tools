# MT-1.0 精确历史数据：实测权限、已补数据和下一批任务

2026-09-11；不计算或宣传策略收益。新因子仍shadow。
建议首个完整历史验收窗口：2024-01-02至2024-06-28形成信号，行情暖启动至少覆盖2023年，
结算延伸到2024-09-30以覆盖60交易日；这只是数据开发窗口，不是挑选好看的收益区间。

## 已核对缓存，不再泛称“没有数据”

- fina_indicator Parquet **27,770行、1,583股**；其中 **21,867行缺ann_date**。
  `cache_parquet.py` 的主键为 `(ts_code,end_date)`，修订数据会覆盖同报告期旧版本。
  即使剩余行有ann_date，也不等于掌握了“当时看到的版本”。
- daily缓存Parquet 352,809行、adj_factor 1,284,792行、index_daily 35,786行；数量不代表
  目标历史universe每股每交易日均覆盖。缺行必须逐股/交易日做反连接检查，不能当停牌或收益为0。
- sector_picks_history.jsonl **13,641行**（2026-05-26—2026-09-10）；全部缺少
  channel/action/method_version/available_at/decision_at。旧verdict和当前映射可做代理研究，不能当精确历史通道。
- 现有score snapshots只有2026-06-01和06-09两份；旧backtest_dataset是v2.3短周期推荐记录，
  不能混入新账本或冒充MT-1.0样本外信号。
- 原始完整证据：`.cron_state/mt1/data-readiness/20260910T180818506822/audit.json`。
  财报空公告日统计：`.cron_state/mt1/data-audit/financial-cache-statistics.json`。

## 真实接口权限与数据（全部新请求绕过缓存）

|项目|本次请求和返回|结论|
|---|---|---|
|全市场单季财报|fina_indicator_vip(period=20260630)→40203：没有接口访问权限|不能用VIP批量路径；全市场当前扫描改逐股普通接口，不购买或改变账户|
|普通财报|600519.SH，20230101—20240930，fina_indicator=10行、income=9行|可补原始财务表及ann_date/f_ann_date/update_flag，但不自动证明修订历史完整|
|原公告接口|anns_d(600519.SH,20240301—20240430)→40203：没有接口访问权限|原文批量索引受限；可走公司/交易所公开披露逐份核验，不把API无权限说成全部数据不存在|
|历史universe|stock_basic(list_status=D)=339行；P=0行且请求成功|已保存退市清单；P空是该请求空响应，不是API失败；需结合L及上市/退市生效日重建|
|行业成员|index_member_all(600519.SH,is_new=Y)=1行；N=0行|当前接口可用；单股N空不能证明全部历史成分完整，更不能替代原生产概念池成员历史|
|日线/复权/涨跌停|600519.SH,20240101—20240930，各181行|具备原始面板切片；非完整universe|
|停牌|suspend_d(trade_date=20240930)=13行|接口有权限；须按全交易日补，并处理盘中停复牌，不把日涨跌停价当保证可成交|
|基准和日历|000300.SH同窗181行；SSE 2023—2024日历731行|已保存真实基准和交易日日历，可用于后续对齐|

官方接口依据：[财务指标](https://tushare.pro/document/2?doc_id=79)、
[全量公告](https://tushare.pro/document/2?doc_id=176)、
[申万成员](https://tushare.pro/document/2?doc_id=335)。文档说明与本账户实际权限分开记录。

**已继续补齐而非只探测**：`docs/MT-1.0-backfill-batch-001.json` 的15个任务全部成功，
600519.SH/000001.SZ/600000.SH × daily/adj_factor/stk_limit/fina_indicator/income。
每股日线/复权/涨跌停181行；财报分别10/9/8行，income分别9/9/10行。
原始响应及参数/hash位于 `.cron_state/mt1/backfill/e043b8ebe657ea08de43/`。
第二批manifest为 `MT-1.0-backfill-batch-002.json`，保存anns_d权限错误及成员Y切片。

**公开原文替代通道也推进了一份**：从贵州茅台官网财报列表获取2023年年报PDF并验证文件头，
保存3,563,819字节及SHA256，路径 `.cron_state/mt1/data-audit/original-filings/`。
[官网财报列表](https://www.moutai.com.cn/mtgf/tzzgx/cwbg/82bde676-2.html)列日期2024-04-03，
[当前PDF](https://www.moutai.com.cn/mtgf/articleFileDir/2024-05/07/e7471b99e4e84c7e9dcf1e591174c08e.pdf)
的URL路径却含2024-05/07；PDF有追溯调整前/后列。因此只标“真实原文已获取”，
**不能认定这个hash在4/3已经公开**，首次披露与更正版本链仍待核。

## 可执行补齐工作单

所有API任务用 `mt1_job.py data-backfill --input <manifest> --max-requests 100`，
在独立systemd unit、RuntimeMaxSec与请求数预算内执行；同manifest断点续跑、每任务至多2次。
格式以已跑通的两个manifest为模板。以下任务有数据侧和代码侧两个出口，不能把下载完成当模型验证完成。

|ID / 所有者|具体下一步与产物|验收条件 / 当前缺口|
|---|---|---|
|D1 财报版本 / code＋investment证据审查|为三股2023—2024每份报告记录ts_code,end_date,ann_date,f_ann_date,update_flag、原PDF URL/hash、首次披露与更正关系；由income查询结果生成filing索引，补公司/交易所原文|先补“首次发布版本”和更正链；ann_date不能缺，决策日不得使用未来或之后更正数据。当前VIP/anns_d无权限，原文只完成一份获取，未形成完整PIT|
|D2 存续/退市 / code|L＋已下载D清单，以list_date<=决策日、delist_date界定生存区间；为退市样本逐股取daily及最后可交易价/终止上市公告|输出逐交易日universe及退市结算依据；不得删除没行情的退市股票或默认按0/最后价结算。已获取339退市项，未补全历史结算|
|D3 行业/概念 / code|对历史universe逐股请求index_member_all Y/N，按in_date/out_date构建区间；另对sector_picks实际概念数据来源单独建立版本化成员快照|申万不是原生产concept的替身。需逐项证明历史概念成员/板块分数可得；现有两个score snapshot不够|
|D4 原始行情 / code|把batch-001日期扩到20230101—20240930，覆盖暖启动；扩展symbols到冻结的历史universe；另按每交易日补suspend_d、基准和交易日历|用日历×universe减去实得行，生成holes.json。空行/停牌/退市须分别解释；新鲜下载不等于每股齐全|
|D5 三通道重算器 / code|将sector_picks._evaluate输入改为显式asof数据适配层（先独立shadow适配器，不改当前评分），保留当时规则版本、价格/估值/财务/行业/量价全部输入hash；输出action、channel、veto、decision_at、available_at|当前sector_picks取最新行情/财报，不具备历史asof入口；旧verdict转换只能标proxy，不能填exact_channels。此开发仍未完成|
|D6 可成交 / code|按目标日历对齐daily+stk_limit+suspend_d，逐笔入场取决策后第一可成交开盘；定义复权基准及分红口径、费用和滑点配置|涨跌停边界/开盘一字板/盘中停牌需保守不成交或更精细数据，不能用日线保证成交；退市结算缺失阻断相关样本|
|D7 独立样本外 / code＋investment审查|在D1—D6满足后冻结入场样本、20/40/60三个窗、费用、现金/再投资口径和滚动切分；退出vs持有同入场配对|输出收益/超额/回撤/尾部/换手/占用及样本依赖；全部来源hash审计后才考虑validated，不用这次三股接口切片报告收益|

D1/D3/D5是当前主要阻断；D2/D4/D6已补基础切片但远未全量。接口权限、原文版本核查与
历史通道适配开发是不同问题，不能全都归咎于“缺权限”，也不能用单一接口可用宣称目标已完成。
