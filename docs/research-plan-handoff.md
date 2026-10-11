# 研究观察池 → 正式 WATCH 签审交接契约

本契约补充 `research-pool-lifecycle.md`。研究准入不等于交易计划，不需要升级 BUY。
本轮不改变既有 reduce_plan / Store.apply 门禁，不自动提升 shadow 方法。

## 真实研究者操作顺序

1. 读取当前 scope 原字节 SHA256、scope_epoch、研究最新 version；按生命周期契约准备
   `research-event`。材料未知写 pending，不能填写虚假的 watch / final。
2. `python3 mt1.py --scope SCOPE --state-dir STATE research-event --input RESEARCH.json`。
   保存回执 request_id（不是 episode_id）：这是正式计划的 `research_event_id`。
3. 使用既有自然 `run sunday/saturday` 产出的 `source_run_id`，不能为通过验收补跑自然任务。
   在下述完整 bundle 中填写 `plan_events`，调用：
   `python3 mt1.py --scope SCOPE --state-dir STATE finalize --input BUNDLE.json --investment-dir INVESTMENT`。
4. 必须检查返回 `errors`、`changes[].after`、计划版本、报告和 source run 的 review 链。
   finalize 可部分成功且 exit 0 不等于签审通过；`research_status=fetched`仅代表材料哈希匹配，
   不是公司研究结论。`reviewed_plan_ids`本身也不是签审证明。
5. 观察计划可 `state=WATCH / unheld_direction=WATCH / held_direction=WATCH / qualification=final`，
   表示观察结论已签审，不是 BUY、真实持仓或外部自选写入；未完成签审则 pending_review。
   不得凭空填写价格阈值。WATCH 可令 price_condition/reference_price/actual_cost 为 null。

## 完整填写模板

`examples/research-plan-watch.template.json` 是完整 finalize bundle（占位符不可直接执行）。
真实材料填写后必须保留其来源/时间/hash。研究事件字段模板见
`examples/research-watch-event.template.json`。以实际已有研究 version/plan version 为准，勿盲用 0。

- `plan_events[].id == payload.plan_id`；新计划 expected_version=0；后续使用账本实际最新 version。
- `request_id` 为本次事件唯一且重试稳定的幂等键；同键不同内容会拒绝。
- `scope_epoch`、`research_event_id` 必须匹配**当前**准入投影。研究重新复核后 request_id
  改变，旧计划仍在 append-only 库但 `plans` / daily 对应研究状态 / 周末可见计划各用当前投影。
  **失效不是删除**，不能把 filtered plans 空集误认为计划账本 version=0。
- 旧计划原始 version 从原 finalize 回执或只读 `Store.latest('plan:'+id)` 获取；
  使用 `examples/research-plan-rebind.template.json` 更新当前绑定并重新签审。
- plan_id/code/market/episode/original_reason/original_date/reference_price/original_deadline/
  legacy_source 是既有计划 immutable 字段。重绑不改这些；原计划 EXIT 后需新 episode。
- `qualification=final` 验证 reviewer、有效 evidence、VALUE/TREND/REVERSAL channel、期限、风险、
  节点、review_due。研究池时间戳 milestones.at 映射计划日期 milestones.date，
  description 映射 condition；不是直接照搬字段名。
- final 计划 review_due 为今天起 0—31天；节点今天起 0—100天；原始期限 <=100天且未到期。
  研究池额外要求1—3月周期，二者取交集，不能修改事实日期绕过验证。
- 若修改 review_due，patch 必须同时提供 extension_evidence（url/date/claim 列表）；
  延长原始期限使用既有 deadline_extension 契约，不覆盖 original_deadline。
- 未持仓用 not_held，不填真实成本/持仓证据。用户 scope 是持仓唯一来源。
- pending 材料缺口写顶层 review_items：`[{"plan_id":"...","expected_version":1,
  "status":"pending","note":"待核验原因"}]`；不得同时列入 reviewed_plan_ids/plan_events，
  不得放入 research.review_items。
- BUY 是另一独立门禁：需通过估值等 checks、有效价格依据、已注册 active method/version、
  当日市场门禁；shadow 方法不得升 final。**本对接验证不使用 BUY，也不修改方法账本。**

## 隔离端到端验证与证据

`python3 -m scripts.verify_research_plan_handoff --out NEW_UNIQUE_DIR`：只写新隔离目录，
真实 subprocess 执行 research-event → run fixture → finalize（内部 Store.apply）→ plans，
然后第二个研究版本使旧计划失效，旧 finalize 重试被 scope 拒绝，新绑定以 version=2 签审。
同一日 daily 的真实 track 函数仅注入合成行情 provider；周六/周日真实 run CLI 使用 fixture，
核对正文 WATCH 与当前计划版本。全程无联网、无持仓、无 BUY、无方法升级、无第三方写入。
回执验证计划账本恰好两条事件，重试未重复，scope 原 bytes 不变。

输出的 research-v1.json / finalize-v1.json / finalize-v2.json 是**完整可执行合成示例**，
只供字段参考，不得将合成代码或 example.invalid 来源复制进生产。
receipt.json 保存命令、返回码、原始输出和材料hash；这不是自然调度证明。

## 自然执行待验（不得工程代做投研）

保留 revision2 的成功数据和跨机回执，不为新文档重跑已完成任务。
下一次既有调度后只读核对 source_run_id、实际消费正文、dispatch 和 delivery/readback；
无自然发生则 pending，不新建cron、不改时间、不重启gateway。
10只公司研究、9持仓复核及生产入池签审仍由 investment 执行。
