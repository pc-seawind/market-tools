# 研究观察池生命周期与 investment 交接（2026-10-11）

## 边界

本功能不改变真实持仓、交易、外部自选或方法参数。新标的在取得
scope 准入之前就能被研究；只有正式计划继续受 scope / epoch / 当前
research_event_id 门禁。所有发现通道仍为 shadow，推荐池不等于 BUY。
旧 watchlist / thesis / 老推荐不导入。

- `research-queue.jsonl`：本轮非持仓且没有风险 reject 的发现全集；未知不淘汰。
- `research-batch.jsonl`：最多 10 个需要新研究/补核验的非持仓标的。
  仅新发现占用此批次；已有效准入的不重复当“新机会”，退出/拒绝不自动重入。
- `research-reviews.jsonl`：独立最多 10 项到期/受阻/未完成复核，不占新发现名额；
  即使已不在发现筛选结果也可进入。按真实上次尝试时间排序；重复出队不改 due。
- `holdings-review.jsonl`：所有确认持仓，未在 A 股 panel 中的港股等保留缺口占位。
- `summary.json.research_coverage`：扫描、发现、队列、批次、持仓复核数量、epoch、行情日。
  这不是公司研究完成证明，completed 字段为未知时必须说明未签审。

**每轮同时交接新发现批次与独立复核批次**；先读 `research-reviews.jsonl` 或
`research-status` 的 review_queue，不得只消费新发现的 10 项。复核尝试要记录真实 pending/完成事件，
其 last_attempt 排序会推进其他待办，但原 due 不因排队或源站受阻而后移。

## 唯一研究事件入口

```sh
cd /home/emox/work/projects/market-tools
python3 mt1.py --scope /home/emox/work/investment/reference/tracking-scope.json research-event --input /absolute/research-event.json
python3 mt1.py research-status --out /absolute/new-run/research-pool.json
```

`research-status` 同时生成 `.json.md` 中文三表。这只是工程读取，不算研究完成。
`research-event` 必须由 investment 根据实际研究填写，不得由 code 制造生产入池事件。
输入为单个 JSON 对象，字段契约如下（不是可直接写入生产的样例股票）：

|字段|约束|
|---|---|
|scope_epoch / scope_sha256|当前 tracking-scope.json 的 epoch 和**原始文件字节** SHA256；不符拒绝|
|code / domain / reviewer|真实代码；domain 必须 investment；实际研究者标识|
|request_id / expected_version|稳定唯一请求 ID；首次为 0，后续用 research-status 当前版本；重试不改内容|
|state|pending / watch / recommended / rejected / exited|
|reason / reviewed_at|本次理由、真实带时区研究时间；不早于 reset、不未来、距提交不超过 7 天|
|business_logic / risk|经营逻辑及明确风险；准入/研究后拒绝必需|
|sources|至少一个原文对象：url/path/sha256/published_at/fetched_at；时间顺序及文件 hash 校验|
|research_horizon_end|初次研究周期终点：研究后 28—93 天；省略采用明确记录的 93 天默认值。同 episode 不自动延长|
|milestones|准入至少一个 `{description,at}`；实际未来节点且不晚于周期终点。明天/下周财报节点合法，不修改事实日期|
|next_review|准入必须未来且不晚于本 episode 终点；到期退出正式 scope 投影，但行情/风险监控不停。已准入转 pending 保留原 due|
|valuation|重点推荐额外必需：研究者填写的估值论证（不是机器阈值）|
|risk_signoff / valuation_signoff|重点推荐额外必需：`{status:"pass",reviewer,at}`；实际签审时间|
|gap_or_exit_basis|待核验、拒绝、退出必需；明确缺口或证伪依据|
|research_outcome|pending 限 unresearched/data_blocked/partially_researched；rejected 必须 completed_not_qualified 且有来源和逻辑|

升级 watch→recommended，降级 recommended→watch，维持同 state，退出 active→exited
都追加新事件。退出后重新准入必须新请求、新版本和比退出更晚的新研究时间。
原理由、原期限、原节点与来源保留在 `original`；重入新 episode 的原材料不覆盖历史。
研究者声明/signoff 是审计输入，不是身份认证；仍需 investment 独立验证研究质量。

## 状态存储与并发

事件账本：`<scope-path>.research.sqlite3`。原文按内容 hash 冻结在
`<scope-path>.research-sources/<sha256>`，事件保留原始路径及归档路径。
SQLite `BEGIN IMMEDIATE` + expected_version CAS + request_id 幂等，UPDATE/DELETE
触发器拒绝历史改写。scope 文件在验证和原文归档后再次核对字节。

**不重写用户 scope JSON**：`scope.load()` 动态投影当前 epoch 的事件。
持仓取原文件，研究事件永远不能改持仓。并发 reset 后旧 epoch 自然隐藏；
过期、退出、研究版本更新让旧正式计划绑定失效。
snapshot 另按 `episode_id` 保留历史准入/首个推荐/明确退出。已准入转 pending 是待核验，
不是退出；行情仍监控但正式计划 fail-closed。退出后重新准入得到新 episode，旧退出
仍在 93 天内有界监控并永久可由绩效核验器核验，daily 同代码取一次报价而保留所有 episode 引用。

所有正式计划写入仍经既有 `Store.apply` → `scope.filter_plans` 及原签审流程。
推荐事件不会自动创建正式计划、调用 finalize 或激活方法。

容量：观察 50、重点推荐 10、显式 pending 100。容量是研究工作量限制，不是交易阈值。
退出历史永久保留；daily 对最近 93 天内最多 100 项退出作行情后验证，超出数量在
exit_monitor_omitted 明示，不能宣称完整收益覆盖。

## 消费链路

- `parallel-cycle` → `run_parallel` 调用独立研究队列；只读 bridge 仍绑定正式 scope。
- 旧 `parallel-review-inbox.json` 没有当前 scope_epoch 的包在 review_gate 返回 pending；
  旧文件 mtime/exit0 不算新研究，按当前事件及原文签审结果核对。
- `daily-track` 实际读取观察、重点推荐、已有持仓及有界退出后验证队列。
  输出 `research_pool_state`、`research_event_id`、`research_only`，无成本/个人收益推断。
  pending 未准入者不当正式监控推荐；过期已准入者仍可继续行情核验。
- 既有 morning/evening/saturday/sunday `run` 中文正文附当前池三表。
- 周末必须将 `research-status` 与**本轮同 epoch/同数据日** parallel report 一起消费；
  仅初稿没有持仓外覆盖时明确故障，不能用初稿/exit0/errors=[] 冒充研究完成。
- 周六：持仓复核 / 新推荐及原判断 / 退出后表现。周日：持仓 / 持仓外机会 /
  新增升级降级退出维持。收益没有已验证价格基准则写待核验，不编收益。

## 离线验收（不是自然执行）

```sh
python3 mt1.py research-replay --source .cron_state/mt1/parallel-runs/20261010T210329576158Z-sunday --out /absolute/new-unique-run
```

只复用 20261009 已完成 funnel 与冻结 cohort，验证原 hash 与成员一致性，
重建研究队列；不重新采集或重算已成功的全量价格/财报任务。
产物含原 summary、scope、funnel、cohort、各文件 SHA256、新旧批次对照、中文正文。
工程 replay 研究完成数为 0，不覆盖其他 investment 历史研究结论。

## 既有自然调度接线与回退

```sh
python3 -m scripts.deploy_research_pool --apply
```

仅原有 4 个 cron 的 prompt 做增量更新：备份、双次字节 CAS、原子替换、读回，
所有非 prompt 字段相等。不存在的 watchlist-decay-weekly 不新增，不重启 gateway。
代码在下一次 CLI 子进程加载；不代表已跑完下次自然调度。
回退只能移除本次标记段及恢复明确改动的旧句子，先比较当前内容；不要整文件覆盖
并发更新。账本和失败证据不删除，持仓文件无需回退。

## 验收仍需 investment 完成

实际公司研究事件、估值/风险签审、真实入池和正式计划绑定实例；下一自然日报/周末
正文三表、daily 真实非持仓消费、用户可见发布/读回。没有合格者可 0 新推荐，
但必须明确 10 个任务各自未研究/受阻/不合格原因，不能给全绿结论。


## Revision 2：独立推荐/退出绩效核验器

不是发现 cohort 收益、不是交易盈亏；不复用旧 watchlist。绩效只遍历当前 epoch
真实研究事件形成的 episode，首次 `recommended` 事件固定推荐基准，`exited` 固定退出基准；
后来 maintain/重入不会改写历史。无基准的原事件永久显示 unknown，不能从后来的报价倒填。

```sh
python3 mt1.py research-performance --observations /absolute/observations.json --out /absolute/new-unique-audit
```

输出 `performance.json`、中文 `report.md`、原事件、观察输入、冻结来源原 bytes、hash receipt。
完全只读：不入池、不创建计划，不改持仓。`research-status` / `scope.load()` / `daily-track` /
周末代码正文也自动消费 `<scope-path>.research-performance-input.json`（若有）；缺文件保留 blocked，
没有推荐/退出 episode 显示“无样本”，不伪造收益。原 cron 已要求读本契约与 research-status，
故本轮无需再次修改调度或 prompt。

### 原事件可选的冻结绩效契约

在**首次推荐**附 `performance_contract`；明确退出事件附 `performance_exit`。
两者同 schema：

```json
{
  "anchor_date": "YYYY-MM-DD",
  "target_dates": ["YYYY-MM-DD"],
  "anchor_policy": "close_observation_not_execution",
  "basis": "unadjusted_close",
  "currency": "CNY 或 HKD",
  "benchmark_code": "明确价格指数代码",
  "fee_scenario": "研究者显式指定的费用情景说明",
  "fees": {"entry_bps": 0, "exit_bps": 0}
}
```

这里的 0 仅为字段示例，不是生产费用默认。缺费用/价格口径/基准/目标日期不计算。
anchor_date 不能早于该事件实际研究日期（北京时间），target_dates 唯一且递增、晚于 anchor。
当前只支持 CNY/HKD；同日目标在北京时间 17:00 前为 not_matured，同日收盘数据若在 17:00 前抓取则 blocked（保守 CN/HK 完成界限）。
选择已知交易日；遇休市、停牌、退市或缺报价不会取最近一天凑数。契约缺失不阻断公司研究，
但该历史绩效只能 unknown。收益代表“指定收盘基准的假设价格情景”，不是宣称该收盘能成交。

### 观察包与原文格式

`observations.json`：`scope_epoch`、`asof`、`series[]`、`corporate_actions[]`。
两类数组每个元素都为 `{code, source:{path,sha256,url,fetched_at}}`；源 JSON 原 bytes 校验 hash，
抓取时间必须带时区且不未来，价格日期不能晚于 asof 或抓取日期。相同 code 重复来源拒绝。

价格源 JSON：`{code,currency,basis:"unadjusted_close",kind:"stock"或"index",rows:[{date,close,status:"traded"}]}`。
基准指数另需 `index_type:"price_index"`。股票和基准币种、价格口径必须相同，无 FX 偷换。
公司行动源 JSON：`{code,status:"verified",from,through,events:[{date,type}]}`。
`events:[]` 必须是原文核验后的明确空清单，不能因没有文件/没有查就填 verified。
核验区间必须覆盖 `(anchor_date,target_date]`；股票缺行动核验、区间缺口、分红/拆股/合并等
一律 blocked 并列原因。当前不实现公司行动调整适配器，不用未复权价格硬算这些区间。
只有显式价格指数免除个股公司行动核验，不能把股票假标成指数绕过。

### 算式与状态

- 毛价格回报 = 终点收盘 / 基准收盘 − 1。
- 净情景回报 = 终点收盘 × (1 − 卖出 bps/10000) / [基准收盘 × (1 + 买入 bps/10000)] − 1。
- 指数使用同一费用情景计算；净超额 = 股票净情景 − 基准净情景。不同币种不合并。
- `not_matured`：目标日期未到，回报 null。
- `blocked`：已成熟但缺精确日期报价/公司行动核验/来源错误/币种口径不符，回报 null。
- `unknown`：原事件没有合法冻结绩效契约，回报 null。
- `closed_before_horizon`：原推荐在该 horizon 前明确退出；不继续假装持有。
- `recommendation_to_exit`：推荐基准至明确退出基准的独立核验。
- `post_exit_counterfactual`：退出基准之后继续持有的反事实，不算真实持仓；重入后旧段仍保留。

不声称含分红总回报或个人盈亏。split/dividend 等尚未有适配器时明确 blocked 属于
核验器的 fail-closed 边界，不是把行情列表称作核算器。
