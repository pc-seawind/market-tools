# 研究任务出池与调度消费契约

续办 `work_9e5d2aecb544757c54b4`，接管旧单 `work_6098d526633febaab69b`。
基线为 `8aedcbd` / `fef94d0`；不是重做容量修复，也不替 investment 判断公司。

## 两本状态账，一个 append-only 存储

|对象|状态/入口|含义|
|---|---|---|
|证券研究/准入|`research-event`：pending/watch/recommended/rejected/exited|原来源、估值/风险签审及正式计划门禁不变|
|研究任务|`research-task`：active/review_required/unclassified/waiting/blocked/archived/completed|任务执行与处置，不是交易信号|

同一 `<scope>.research.sqlite3` 中 entity 分别为 `research:<epoch>:<code>`、
`research-task:<epoch>:<code>`，版本独立。读取只投影，不改 scope JSON。
所有真实持仓、有效观察准入、曾准入 episode 的行情风险监控不因任务归档停止。
已准入事件后来转 pending 时仍监控；真实退出必须独立 `research-event state=exited`。
有效 watch 的补研任务也可以搁置，不撤销该 watch 或现有有效正式计划绑定。

任务不能直接调用 `complete` 或伪造 rejected；原研究事件完成准入 watch/recommended、
证据充分不合格 rejected，或明确证券退出，才产生相应真实事件和任务完成记录。
rejected 仍需经营逻辑、风险、原文及 `research_outcome=completed_not_qualified`；
证据不足/优先级不足用 archived，不能当成公司差、证券退出或删除历史。

## 状态/动作

|动作|前提及必填字段|结果|
|---|---|---|
|start|此前无任务；open_gaps、next_action、未来 due|确实启动研究才记 active；扫描/排队不算启动|
|triage|旧 unclassified；open_gaps、next_action、due|investment 补齐任务字段，不做公司自动判定|
|attempt|active/unclassified；本次 reason，可附 sources/resolved_gaps/resolution_basis|新证据或解决缺口才计进展；否则累计无进展|
|review + decision=continue|活跃任务；具体 continuation_basis、open_gaps、next_action、due|明确处置复核后继续，不是自动延期|
|wait/block 或 review + decision=wait/block|活跃任务；disposition_basis、next_action、trigger_key、trigger_condition、未来 check_at|waiting/blocked，不占每日主动补研队列|
|archive 或 review + decision=archive|活跃任务；disposition_basis、next_action|搁置归档；证券准入及监控独立|
|reopen|waiting/blocked/archived/completed；新可核验来源、reason、open_gaps、next_action、未来 due|显式重开，generation +1；有 trigger_key 时必须匹配原触发标识|

连续两次无实质新增 hash / 无已知缺口解决进入 `review_required`，之后不能用 attempt
绕过处置复核；由 investment 明确继续的具体依据、等待、受阻或搁置。
更换 reason、只改 reviewed_at、重复旧来源不算进展。来源 hash 只证明原文 bytes 新增，
来源对公司的相关性、是否只是格式变化以及缺口确实解决，仍由 investment 负责，不由 code 代做语义判断。
解决缺口必须是当前 open_gaps 中的项，并附来源和 resolution_basis。

原 due 冻结在 `original_due`，attempt 不得改 due。确需继续并延长期限时，显式提供
`extension_reason` + `continuation_basis`，append-only 保存 from/to/reason/请求 ID；
原事件、旧理由和旧截止不覆盖。继续复核不能只写“下次再看”。
waiting/blocked 在 check_at（最迟检查日）到期只读投影为 review_required 并进入队列；
这次重开计入出入账，不需要新定时器。提前满足原触发条件时，investment 保存证明该触发的
新原文，再显式 reopen。到期检查不是自动继续、延期或拒绝。归档不会按日期自动唤醒。
停放时记录证据基线；停放后先保存到 research-event 的新材料也可用于显式重开，
但保存本身不会重开。重复旧证据无新理由的请求不生新处置；同 request_id 原内容重试幂等，改内容报错。

## CLI 与字段

```sh
cd /home/emox/work/projects/market-tools
SCOPE=/home/emox/work/investment/reference/tracking-scope.json
python3 mt1.py --scope "$SCOPE" research-status --out /absolute/new-run/status.json
python3 mt1.py --scope "$SCOPE" research-inventory --out /absolute/new-inventory
python3 mt1.py --scope "$SCOPE" research-task --input /absolute/task-action.json
python3 mt1.py --scope "$SCOPE" research-event --input /absolute/company-result.json
```

`research-inventory` 输出 inventory.json + report.md，目录必须未存在；只读零处置写入。
`research-status` 输出 JSON 和 `.md`。先读当前任务 version、research_version 再提交；
不要混淆队列上的 `expected_task_version` 与研究的 `expected_version`。
任务请求的 `expected_version` = 任务 version；`expected_research_version` = 研究 version。
scope_sha256 用 tracking-scope.json **原始字节**计算，不是重编码 JSON 的 hash。

以下是字段示意，尖括号必须替换为真实值，不可把示意公司写入生产：

```json
{
  "scope_epoch": "<当前 epoch>",
  "scope_sha256": "<scope 原始字节 SHA256>",
  "domain": "investment", "reviewer": "<实际责任人>",
  "code": "<真实代码>", "action": "review", "decision": "wait",
  "request_id": "<稳定唯一请求 ID>",
  "expected_version": 2, "expected_research_version": 4,
  "reviewed_at": "<真实带时区 ISO 时间>",
  "reason": "本轮缺口处置复核，等待指定新版财报，不是拒绝公司",
  "disposition_basis": "当前原文不足以拆解经营现金流，已穷尽现有披露",
  "next_action": "新版财报发布后核对经营现金流与应收桥",
  "trigger_key": "next-filing",
  "trigger_condition": "公司发布包含经营现金流明细的新版财报",
  "check_at": "<未来最迟检查时间>"
}
```

共用字段：scope_epoch/scope_sha256、domain=investment、reviewer、code、action、
request_id、两个 expected 版本、reason、reviewed_at（不早于 reset、不未来、不超过七天）。
`sources` 每项为 `{url,path,sha256,published_at,fetched_at}`，本地原文 hash 及
published_at ≤ fetched_at ≤ reviewed_at 校验，冻结原文到现有内容寻址目录。
`open_gaps` 为非空、不重复的具体缺口字符串列表；resolved_gaps 必须引用其中的字符串。
缺字段/过期、epoch/CAS 冲突显式报错；不会默认为通过。
同一账本串行写入，任务 CAS 与绑定研究版本的检查均在 `BEGIN IMMEDIATE` 内，
原研究版本与任务版本并发变化不可静默覆盖。生产状态只能由 investment 发布。

## 预算与公平推进

先消费 `research-reviews.jsonl` / `research-status.review_queue` 的旧任务，再看
`research-batch.jsonl` 新候选。旧任务独立十项优先，非持仓逾期数为 O 时，
借用量 T = min(10, max(0, O − 10))，旧任务默认上限 10+T，新批次最多 10−T。
总单轮候选预算不增加；旧任务逾期至少二十项时，start 明确拒绝额外新开研。
已完成研究结果无论预算仍可保存，不恢复 pending 存储上限。

旧队列按逾期优先、真实 last_attempt_at、due、code 排序；实做后追加事件推进排序，
不得同十项只刷新时间。等待/受阻仅到触发/最迟检查重新进入；归档永不被每日发现重入。
发现集合仍完整保存，batch 缩小不是拒绝保存发现或研究成果。新候选不是 actual_starts，
真实启动数和实际完成数从任务事件出入账读取，不用扫描、exit0 或 errors=[] 代替。

## 旧记录只读分流与报告

旧记录不做批量数据库迁移。没有任务事件的 pending 投影为 unclassified；历史已可
验证的连续两次无进展显示 review_required，未知字段仍明确列在 unknown_fields。
历史 next_review 的首个已知值保留为 original_due；原先没 due 就显示未知，
不能按年龄、来源受阻或字段缺失自动 rejected/archived。已准入到期也只触发补研复核。

首次旧单盘点为 102 项；investment 补录八项后的续办快照为 110 项（含已完成任务）。
可审查清单：`reports/work-9e5d2aec-20261103/readonly-inventory/{inventory.json,report.md}`。
逐项列年龄、原/当前 due、连续重复与累计尝试、缺口、未知字段、建议动作；
完整原理由/版本/旧截止/原文来源在 history。不是实际分流结果，investment 回传后执行。
原八项补录已完成（事件 448→456），不得重复写入，也不把本次工程盘点当作公司出池。

日报默认北京时间当日零点至当前；周末用北京时间本周一零点至当前，例如：

```sh
python3 mt1.py --scope "$SCOPE" research-status --since 2026-11-02T00:00:00+08:00 --out /absolute/week-run/status.json
```

中文正文样例（**仅示例数，不是生产已执行**）：

|期初活跃待办|新启动|重开|真实完成|转等待|转受阻|搁置|期末活跃待办|逾期|
|---|---|---|---|---|---|---|---|---|
|100|2|1|4|3|1|5|90|60|

对账：100+2+1−4−3−1−5=90。另列当前及历史 waiting/blocked/archived，
以及含归档的总未解决量，不用改名掩盖。转出后再转入按事件次数计数，不是假定不重复公司数。
CLI 内置对账检查，不一致报错；四个既有 phase 正文都嵌入任务表，周末按周窗口。

## 调度与交付边界

`python3 -m scripts.deploy_research_pool --apply` 仅更新现有四个 cron 的标记 prompt，
备份、双次字节 CAS、原子替换、读回并比较所有非 prompt 字段；不新增调度，不改群/时间/模型，
不重启服务。修改前后回执在本单 reports，不能把代码或 prompt 落盘冒充自然执行。
新 CLI 子进程直接加载工作区版本，未配置未知机器的部署/重启。

旧单 home-ubuntu 单测、独立 5090 与 Dual5090 冻结 32 seed 成功结果按 hash 追溯复用；
不重做成功计算展示分布，WSL 仍遵从 owner 暂停探测。新增边界/CLI 依赖测试为秒级，
在当前 home-ubuntu 临时 scope 串行验状态，不跑模型、不占 GPU、不写生产处置。
公司真实分流、下次自然日报/周末执行及最终发布由原 investment topic #3245 独立验收。
