# MT-1.1 独立发现与中期择时（shadow）

2026-09-11 用户批准结构修正。本实现不晋级参数、不改生产评分、不交易、不写自选、不新增 cron。

## 决策结构

|阶段|实现|无法确定时|
|---|---|---|
|VALUE 发现|年度报告 ROE≥10、0<PE≤25、0<PB≤3；独立 shadow|年度数缺失或冲突 unknown；不把半年 ROE 年化|
|TREND 发现|120 日完整面板，收盘在上升 MA60 上、60 日涨幅>5%、相对沪深300强|只发现，盈利/收入/订单等经营核验仍 pending；无 PE 门槛|
|REVERSAL 发现|120 日最高价回撤≥20%|深跌不是反转证据，经营改善仍 pending；无盈利白名单|
|共用风险|身份/日期/换手/ST；非金融负债 shadow；金融不套负债70/现金流门槛|金融资本与资产质量、重大事件、实际可交易性需独立研究|
|通道证据/估值|可选 `--reviews` 哈希绑定研究包；公司原文、里程碑、情景/同业/资产/金融资本适配|不能可靠估值就 pending，不因成长身份豁免|
|择时|突破/回调承接、量比、相对强弱、MA20偏离、ATR20、持续破MA60|先给真实诊断；上游未通过时不得解释成全流程通过|
|动作|未持有等待/等待签审；已有持仓仅假设分支，持有/減仓退出复核|推荐不等于持仓；单日破线或禁止追高不自动卖出|
|签审|本入口永远 pending_review；既有 ledger/finalize 仍是独立入口|新 shadow origin/method_status 不能借已 active 的旧 method 洗成 final|

全部阈值为 `mt11-parallel-shadow-1` 探索参数。120 日仅适用于此新诊断方法；**没有更改历史 D5 的250日暖启动或 PIT 门禁**。
技术条件有效到下一已完成交易日；触发须重新计算。ATR/MA60不是统一止损指令，必须结合原始持仓理由及期限审查。

## 一体化操作

从工程根目录，在独立 cgroup 内运行（不把 credential 放命令行）：

```bash
systemd-run --user --unit=mt11-<unique-run> \
  --property=RuntimeMaxSec=3900 \
  --working-directory=/home/emox/work/projects/market-tools \
  /usr/bin/python3 mt1_job.py parallel-current \
  --panel .cron_state/mt1/parallel-panel-<run-date> \
  --out .cron_state/mt1/parallel-result-<unique-run>
```

- 必须已有同一已完成交易日的 `sweeps/YYYY-MM-DD/{stock_basic,daily_basic,financial-*}.json`；缺少则报错，不复用旧日行情。单股财报缺失记 unknown。现有 `sweep` 是全市场财报采集入口；本次复用已完成的 2026-09-10 快照。
- panel 每日新目录；断点复用仅限同日。collector 最多250请求、3600秒软预算、45秒单请求；系统硬限3900秒。失败后同日恢复，已成功文件不重复请求。
- `parallel-collect` 仅采集；`parallel --panel ... --out ...` 仅重放诊断，不联网；`parallel-current` 固化二阶段，agent 不手拼 JSON 管道。
- `--out` 必须不存在，绝不覆盖旧产物。影子结果不自动注入生产 cron；未部署。
- 来源财务快照的原采集时刻不明处明确 `unknown_original_snapshot`，不伪造 PIT；新价格采集记 API、参数、时间。原始观察、价格、复权、日历冻结为 gzip，源hash/代码hash保存在 summary。

## 产物与审计

- `stages.csv`：每股×三个通道完整记录，代码/名称/板块/理由/未知/诊断/研究排序。
- `funnel.json.gz`：每股完整财务口径、技术特征、通道证据和估值阶段、条件动作。
- `research-queue.jsonl`：去重、保留所有来源通道、研究优先级；不是期望收益排序。
- `summary.json`：各阶段数量、板块与行业分布、旧49对照新增/移除列表、来源与hash。
- `report.md`：中文流程及三通道真实代表卡。
- `forward-cohort.json`：当前诊断队列20/40/60交易日待观察；不是已执行组合，无收益验证结论。

完整名单的数量恒等式：每通道 pass + reject + unknown = universe；多通道重复保留来源，但研究队列按代码去重。
风险拒绝后的证据/估值/技术仍可保留**旁路诊断**，不代表重新准入。公司研究未完成的0 BUY必须称“未签审”，不能称“全市场审查后无合格标的”。

## 分批研究包

`--reviews` 接收 JSON 数组，每项：`code/channel/kind/reviewer/reviewed_at/valid_until/conclusion/reason/sources`。
`kind` 为 `risk/evidence/valuation`，结论 `pass/reject`。
`sources` 每项 `url/published_at/path/sha256`，读取实际文件验hash；拒绝未来/过期/重复/错股票/错通道。
- risk：`checks` 必须有 financial/liquidity/major_event/tradability；全部pass才能声明pass，不能覆写已有数值风险否决。
- evidence：`milestone/milestone_date`（未来100天内）。
- valuation：`basis`（earnings_scenarios/peer_comparison/asset_value/financial_capital）、`assumptions/downside_case/price_below`。
研究包接纳仍只是记录证据，不是最终BUY；真实研究缺失不得用合成包顶替。

## 已实现与未完成分界

已实现工程旁路：独立三通道、财报口径修正、行业风险分流、真实价格择时、双持仓条件说明、归档和待研究队列。
尚未完成：逐公司事实研究/价格论证/签审；金融细分定量校准；生产调度接入；实际持仓及原期限绑定；20/40/60实盘前瞻收割与策略有效性研究。
旧2024主题档案恢复仍独立incomplete，绝不以当前规则回填冒充旧版本有效性。

## 数据定义参考（2026-09-11核验）

- 财报报告期与公告日期：https://tushare.pro/document/2?doc_id=79
- 日线价格与成交量：https://tushare.pro/document/2?doc_id=27
- 复权因子：https://tushare.pro/document/2?doc_id=28

文档只支持字段定义，不证明本策略阈值有效。

## Revision 2（2026-09-11，优先于上文旧措辞）

- P1：价格日期 `price_asof` 只约束完成的K线；研究有效性用显式、带时区的 `decision_at`。`reviewed_at` 不得晚于决策时点，来源发表不晚于审查/决策，`valid_until` 覆盖决策。早盘当天审昨行情、隔夜公告、周末研究可用；精确时间未来1微秒也拒绝，naive时间及超出±14小时时区拒绝。旧date-only审查记录保留日期精度，不伪造实际时间；date-only来源保守按当日末可知，date-only到期日包含当日。新writer写实际带时区时间。没有显式decision_at的旧review_gate调用仍视为历史行情日末，不自动改成现在。
- P2：原 `pullback_supported` 改为 `near_MA20_low_volume`（均线附近缩量诊断），**没有实现真正回调/承接历史路径识别**；数值门槛未改。新版本 `mt11-parallel-shadow-2`，全程shadow。
- `discovery-pool.jsonl`保留宽泛发现；`research-queue.jsonl`已排除风险否决；`research-batch.jsonl`按既有排序截取最多10条作为工作批次，不是改阈值精筛。逐条`channel_readiness.blockers`包含剩余原因。风险×择时交叉统计突出所有trigger均不是可买池。
- “研究未完成”和“研究已齐，仅待方法与签审批准”分开；任何技术诊断触发本身不等于等待最终签字。
- `parallel-cycle <phase>`：采集/复用→并行分析→读只读plans.db→冻结版本/原日期/原期限/参考价/持仓状态→产出`bound-review.json/.md`。原EXIT不自动重入；确认持仓到期仅复核，不延期；无holding_evidence不称确认持仓。`assert_binding_current`在使用观察前检查原计划所有绑定字段和版本，冲突拒绝。此接口仅输出安全研究观察，**不写权威计划状态，不交易**，后续签审仍使用既有finalize。
- 默认研究包入口 `.cron_state/mt1/parallel-review-inbox.json`。新增`partial`必须带真实facts与remaining_checks；可接收投资域已有不完整研究，不假造pass。`scripts/import_mt11_research_notes.py`仅格式适配，保留原研究日期、来源层级与primary文件缺失，当前导入时间不是新的公司事实签审。
- `bound-ledger-snapshot.json`冻结真实账本（包括未进发现池的证券）；`review-sources/<sha256>`冻结研究源字节；报告从产物中取全部通道，禁止手抄漏掉多通道。
- 生产接入仅通过`scripts/deploy_mt11_sidecar.py --apply`给**现有四条**cron追加标记块；先备份、CAS、回读验证其余字段完全不变。不新增cron。自然下一次触发、飞书投递必须另取真实回执，CLI成功不能冒称自然调度已验收。
- 20/40/60日只建立冻结队列，**自动收割功能未实现**与“窗口尚未成熟”是两个不同缺口，均保留。

## Revision 3：自动前瞻收割（优先于上文“未实现”状态）

`parallel-cycle` 现在自动调用 `mt1.forward.run`：注册稳定cohort→核验日历→20/40/60成熟判定→归档收割。可单独运行：

```bash
python3 mt1_job.py forward-harvest --source <真实parallel-run目录> \
  --panel <本轮panel目录> --out <全新回执路径>
```

长调用仍须独立cgroup+硬超时。源目录不能是合成fixture，生产输入与tests/tmp完全隔离。

- 稳定身份：`price_asof + method完整对象 + forward contract`；同日同方法重跑不产生新cohort，不重置初始decision；多通道同股只出现一次。成员变化在相同身份下拒绝，不偷偷覆盖。
- 冻结起点：复制原始funnel.gz并验hash，冻结初次decision、方法、源代码hash、成员及风险/诊断状态。观察对象是**宽泛诊断发现池（含风险否决）**，不是推荐或持仓。
- 价格口径：初次decision之后下一交易所开盘作为观察起点；第20/40/60个后续交易日收盘作为终点，明确用entry index+h。日历必须有逐自然日开闭状态，不按周一至周五推算；预计成熟日冻结，后续交易所修订不同则blocked等待核验，不默默移期限。
- 成熟前不计算任何收益：每股每窗口 `not_matured`，收益字段null。没有未来日历则unknown/block，不猜日期。
- 成熟时需要完整窗口日线/复权因子/CSI300基准以及**逐公司、覆盖窗口、有来源hash的上市/停牌/公司行动clearance**。缺日、零量、退市、行动未知、复权变化、未来/重复/错误代码、基准缺口全部blocked。不会以空事件列表代替“无事件已核实”。
- `forward/issuer-clearances.json` 为代码→clearance的输入；schema见`source_clearance()`及隔离测试。证据适配/取得不完整时可自动产出blocked，但不称成熟收益已齐。当前不自动下载并认定全部公司行动/退市证明；此数据依赖显式保留。
- 净收益情景：`exit_close*(1-10bps)/(entry_open*(1+10bps))-1`；每边10bps为固定演示的全成本情景，不是已校准税费或成交假设。基准为CSI300同起点开盘→终点收盘的价格指数，非含息/ETF可交易回报。
- 资金口径：每股独立一单位，退出后无息现金、不再投资、不轮换；只有冻结全体股票都可观察时才输出描述性等权净均值，不删除停牌/缺失股票提高均值。个股诊断回报和实际账户/策略有效性严格分开。
- 幂等：同cohort、同评估日开闭边界、同日历内容/行情/证据/代码生成同artifact；重跑回读不覆盖。修订输入生成新artifact，旧记录保留。成熟输入gzip、证据字节、原始日历及各hash持久归档。
- 产物：`forward/cohorts/`、`forward/harvests/<id>/`、`forward/calendars/`、成熟时`forward/inputs/`与`evidence-blobs/`；本轮`forward-harvest.json`是入口回执。
- 状态分开：**注册/日历/成熟判定/幂等/安全收割功能已实现**；真实窗口未成熟；行动/停牌/退市clearance未齐时未来成熟会blocked；有效性尚未验证。

首次注册必须发生在冻结的观察起点开盘之前；错过起点后不能事后登记为“向前cohort”。已存在的cohort可按原身份正常收割。收益仅是报价路径诊断，不证明涨跌停时可成交；停牌/公司行动清单的来源还须有决策前的发表时间。
