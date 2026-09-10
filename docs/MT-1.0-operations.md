# MT-1.0 全量扫描、投研交接与真实链路操作契约

工单 work_2584297bb8bd6304e99f，第二轮。以下不改变已经通过的四项准入/退出修复。
所有命令工作区 `/home/emox/work/projects/market-tools`。线上入口 `mt1_job.py` 从本机 worker
进程内存继承必要环境，不打印密钥。长任务独立 systemd cgroup，非前台长 Bash。

## 1. 全 universe 闭环

```bash
# 单次独立运行；内部自动分批直至全量闭环，无需agent一批一批再调用
systemd-run --user --unit=mt1-full-20260910 --property=RuntimeMaxSec=7200 \
  --working-directory=/home/emox/work/projects/market-tools \
  /usr/bin/python3 /home/emox/work/projects/market-tools/mt1_job.py \
  sweep --asof 2026-09-10 --batch-size 100 --workers 4 --attempts 2 \
  --calls-per-second 3 --max-seconds 6600
```

- `asof` 是目标市场**已完成交易日**，不等于机器当前自然日。9/11 凌晨的最新 CN 完成日是9/10；不冒充9/11盘后。
- 每日固定完整 stock_basic L universe，不切前100；daily_basic 要求目标日期完全匹配。
- 先全 universe 每股至少一次，再重试不可用项，每股至多2次；成功且字段完整者不反复请求。
- 每股请求25秒硬超时，整任务6600秒软退出、独立unit 7200秒硬上限；请求频率3次/秒，并发上限4。
- 强制禁用长期 JSON / Parquet 缓存取数，避免旧财报缓存冒充这轮查过；原始结果在本轮日期目录保存。
- `.cron_state/mt1/sweeps/<asof>/symbols/<code>.json`：每股状态、缺失原因、次数、时间及history。
- `financial-<code>.json`：本轮原始财报；`batches.jsonl`：每批具体股票列表和统计；`summary.json`：最新闭环统计。
- `usable` = 字段完整足够运行当前shadow筛选，**不等于通过筛选、更不等于最终BUY**。
  缺PE（例如亏损公司）、缺日行情、缺财报字段、接口失败都分别留原因，不能为提升覆盖率补造。
- `attempted_unique/universe_size` = 本轮真实请求覆盖；`usable/universe_size` = 数据可用覆盖。
  `closed` = 可用或已经耗尽本轮尝试；只有 `closed==universe_size` 才 `complete=true`。
  全量闭环允许 unavailable>0，其含义是每一股都有明确结论，不是假称数据全可得。
- 进程中断后同命令从逐股文件恢复；并发运行由 flock 拒绝，不会重复推进两套游标。
  不改旧的小批量游标与既有原始候选评分。

**日常自动路径**：`mt1_job.py run evening --collect` 在日历验证后调用 `sweep.launch()`。
每日期一个 deterministic unit；已有活任务或 complete 结果不会重复启动。独立unit设置
Restart=on-failure、RestartSec=60、StartLimitBurst=3 / StartLimitIntervalSec=infinity（unit生命周期最多3次启动）：失败/软超时自动从断点续跑，
不会无限重启；超过预算保持pending并报具体缺口。入口每次收割重读全量snapshot，不把首批快照缓存成永久结果。
`manifest` 的 universe_sweep_launch 只是发起记录，不是全量完成证明；以 sweep summary 为准。
早盘按expected_date复用该交易日全量snapshot；周末读取最近一份snapshot并保留其asof，不冒充周末当日行情。
`--max-stocks` 为旧CLI兼容参数，生产新路径不再用它限制全市场覆盖。

## 2. 64项逐批投研交接（公司判断归investment）

```bash
python3 mt1.py coldstart-export --out .cron_state/mt1/coldstart --batch-size 10
```

只读生产SQLite；输出日期+快照hash目录，`items.json` + `gaps.md` + 7个batch文件。
本次64项中 **63个证券标识＋1个 `_bootstrap_state` 元数据误迁移项**，后者明确标记不是股票，
不删除历史事件、不生成建议。每项包含原始snapshot/legacy_source、plan_id、expected_version、
原始事实未知字段、中文缺口及英文校验码，投资域无需猜哪项缺什么。

输入：`batch-NN.json` 为不可变工程交接，不在原文件上填答案；复制 response_template 到新文件。
输出：
- 不能核验公司原文：`review_items=[{plan_id,expected_version,status:"pending",note,evidence:[]}]`。
  note 写清仍缺什么；`reviewed_plan_ids=[]`，`plan_events=[]`。工程缺口审计不是投研已审查。
- 有真实结论变动：使用原有 `plan_events` 合约，必须完整有效证据/审查人/版本；不能把同一项
  同时放 pending 与 plan_events/reviewed_plan_ids。投资资格没有简化通道。
- 无变化但确实已审查：投资域依据已核验来源填写reviewed_plan_ids，并保留研究来源。
- `phase` 与 `source_run_id` 必须匹配实际run报告；reviewer必填。
- pending版本过期在写任何新事件前拒绝，重新导出最新快照；未知原始事实不能改成今天或当前价。
- 历史EXIT保持历史状态，报告中的已有持仓方向EXIT不是本轮新退出；只有事件差异才是新结论。

```bash
python3 mt1_job.py finalize --input /absolute/actual-review-bundle.json
```

finalize即使0 final、0 changes也会输出真实pending WATCH/历史EXIT表、未审查范围与缺口，
可用它完成真实报告链路，不需要为验收制造BUY。不写旧rec，不写第三方自选，不执行交易。

## 3. 真实链路逐层留证

`chains/<run_id>/` 保存不可变分阶段记录：

|阶段|实际证据|不能冒充|
|---|---|---|
|dispatch|gateway journal firing记录＋当前job topic/worker绑定＋时间关联|本地四阶段单测、手工run不是自然调度|
|run|进程/hostname/worker、git HEAD、源文件hash、manifest快照与report hash|exit0不是数据无缺口|
|review|完整实际review-bundle及hash、finalize结果与Markdown hash、pending/已审查ID|0 changes不等于全部已审查|
|delivery|真实工具响应文件、doc URL或message_id、读回文件、对应报告hash|工具没返回不能编造回执|

run自动写run证据；含source_run_id的finalize自动保存review证据。无source_run_id的旧调用仍可运行，
但不产生已关联链路声明。人工安全验证请用非日期phase形式run_id；collect-dispatch会拒绝把它当自然调度。

发布后先保存**实际工具返回**为 provider-response.json，再用读文档/读消息工具回读，保存readback.json。
回读必须包含原run_id。创建下面的 receipt 文件（字段值取真实结果，不写示例ID）：

```json
{
  "run_id": "本轮真实run_id",
  "document_url": "真实文档URL（或使用message_id字段）",
  "report_sha256": "finalize报告文件的sha256",
  "provider_response_path": "/absolute/provider-response.json",
  "readback_path": "/absolute/readback.json"
}
```

```bash
python3 mt1.py evidence --kind delivery --run-id "$RUN_ID" --input /absolute/receipt.json
python3 mt1.py evidence --kind collect-dispatch --run-id "$RUN_ID"
python3 mt1.py evidence --kind inventory
```

收集器通过只读SSH拉gateway firing日志；只允许自然 `<日期>-<phase>` ID，并要求当日唯一firing、
同worker、run落在触发后6小时窗口。记录的是**时间相关证据**：日志不含唯一dispatch_id，绑定是
查询时快照，因此仍需独立核验，不把该相关性当密码学证明。缺日志/不唯一/没回执均保持pending。
`inventory`列四phase自然链路待验，并检查已引用文件sha256；文件丢失/被改写显式报integrity_errors。
未部署collector前的旧run不追补“已自动留证”的假记录。

第二轮已用生产只读一致性副本、真实日历与行情、首批10个真实计划，完成手工
run→pending bundle→finalize→飞书创建→读回；未修改生产计划。
这仅证明真实数据和发布接口能走通，**仍不能替代四个自然时段的实际调度验收**。

## 4. 历史数据缺口与有界补齐

```bash
systemd-run --user --unit=mt1-readiness-$(date +%s) --property=RuntimeMaxSec=600 \
  --working-directory=/home/emox/work/projects/market-tools \
  /usr/bin/python3 /home/emox/work/projects/market-tools/mt1_job.py data-audit
# 首个可重复数据补齐批次（3个真实股票×5接口，2024样本原始面板；不计算收益）
systemd-run --user --unit=mt1-backfill-$(date +%s) --property=RuntimeMaxSec=600 \
  --working-directory=/home/emox/work/projects/market-tools \
  /usr/bin/python3 /home/emox/work/projects/market-tools/mt1_job.py \
  data-backfill --input /home/emox/work/projects/market-tools/docs/MT-1.0-backfill-batch-001.json
```

data-audit逐项保存现有缓存文件数/Parquet行数和字段、历史信号缺列统计，以及绕过缓存的真实接口请求。
data-backfill只接受白名单只读接口任务，响应hash原始归档，断点/最多2次失败尝试，不覆盖旧缓存。
结果不声明“财报修订可追溯”“空返回代表从未变更成分”“停牌之外一定可成交”。
具体审计结果和后续工作单见 `MT-1.0-data-gap-tasks.md`。

## 5. 部署与剩余外部时点

四cron当前持久绑定home-ubuntu；gateway在该worker暂离线时保持绑定等重连。
但绑定陈旧或不兼容时存在重选worker分支，不是强制硬pin。未擅自部署其他worker；若实际路由到别机，
应停止报部署缺口，不能从本机成功推断所有worker可用。此限制在四cron prompt中保留。

必要配置仅改四既有cron的prompt，先备份、CAS、回读，保留所有非prompt字段；不新增cron，不改时点。
下一自然验收时点（北京时间）：9/11 07:15早盘、18:30晚盘、9/12 07:15周六、9/13 05:00周日。
这些时点未到就写待验；0 final和真实自选无候选不阻断报告交付，不扩权做交易或自选真写入。
