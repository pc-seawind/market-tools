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
