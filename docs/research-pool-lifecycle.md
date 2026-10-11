# 研究观察池生命周期与 investment 交接（2026-10-11）

## 边界

本功能不改变真实持仓、交易、外部自选或方法参数。新标的在取得
scope 准入之前就能被研究；只有正式计划继续受 scope / epoch / 当前
research_event_id 门禁。所有发现通道仍为 shadow，推荐池不等于 BUY。
旧 watchlist / thesis / 老推荐不导入。

- `research-queue.jsonl`：本轮非持仓且没有风险 reject 的发现全集；未知不淘汰。
- `research-batch.jsonl`：最多 10 个需要新研究/补核验的非持仓标的。
  优先尚未记录研究的标的；已有效准入的不重复当“新机会”，退出/拒绝不自动重入。
- `holdings-review.jsonl`：所有确认持仓，未在 A 股 panel 中的港股等保留缺口占位。
- `summary.json.research_coverage`：扫描、发现、队列、批次、持仓复核数量、epoch、行情日。
  这不是公司研究完成证明，completed 字段为未知时必须说明未签审。

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
|milestones|准入至少一个 `{description,at}`；研究后 28—93 天内的业务节点|
|next_review|准入必须未来且不超过研究后 93 天；到期自动失去活跃投影，不自动延期|
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
