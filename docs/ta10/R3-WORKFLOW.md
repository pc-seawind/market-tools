# R3：现有 investment 日报/周末 agent 的研究接续合同

本文件是已部署 REPORT-CONTRACT.md 的强制接续步骤，不新增 cron/群/时刻。执行者是当前 investment 日报或周末 agent，不转嫁用户复制命令；不得只渲染研究层而忽略待审队列。

## 每次既有报告的职责

1. 执行现有 `report-consumers.sh` 后，读取输出目录 `research-work-inbox.json`。其 items 每项绑定原 run/manifest/股，requests 给失败启动/worker恢复状态。队列异常必须报告 owner 与下次检查时间，原三段仍发布。
2. 按 `due_now` 处理到期项目；未到期项目保留 `next_check_at`。负责人固定 `investment-agent:existing-daily-and-weekend`，reviewer 写实际执行者/原topic，不冒充独立验收。
3. 待审 run：读冻结 input、原公司PDF/HTML/text与实际模型输出；对相关缺口有限补采公司原文。新原文先确认代码、年份、发布日期及原文件hash，更新 `reports/ta10-r2-20260915/company-sources/<code>.meta.json`（file/text/url/published_at/fetched_at/publication_precision/extract_status/sha256），不要改scope/thesis/技术参数。新证据应产生新revision，不塞回旧冻结输入。资料拿不到写 source_work blocked 原因和 next_check_at，不能永久静默。
4. 源文签审用 `ta_quality.checked_review` 现有格式：run/input/manifest/result hashes，九股 facts/short_term/thesis/gaps/B_vs_C 逐项 evidence_ids/rationale/status。已知错误股 blocked，不因引用hash正确刷pass。实质错误用 `ta_revision`；论点变化会使cross/C依赖失效并真实重调，纯缺numbers=[]仅可用确定性schema等价复用。
5. 到期跟踪：下一交易日条件是否已到、有没有原报价/公告、原条件是否可检验分别说明；季度命题只能凭新公司/财务证据判断。收益仅使用独立价格/日历面板；数据/逻辑/时机/定价/意外事件分别判断，未知就unknown。未成熟用not_matured，已到期缺材料用blocked，不能自动凭涨跌判经营。
6. 写 JSON batch，通过同一个 report-consumers 入口回写：`TA10_REVIEW_FILE=<本轮batch绝对路径> MT13_LEDGER_ROOT=<原root> bash .../report-consumers.sh <原phase> ... <新的输出目录>`。无需改投递。apply 会先校验整批，再不可变落盘；重复批次幂等。读回 stdout apply回执及新 consumer-receipt/inbox，确认记录hash和next_check_at；失败要显式披露，不写已签审。
7. worker 每次新revision完成会自动执行 due_queue，把上述 proposition_checks/short_term/error_categories 写回实际核验结果；agent可在本轮运行 `python3 -c "from mt1.ta_pipeline import due_queue; from mt1.ta_research import now; print(due_queue('<root>',now()))"` 即时重算。它不制造新行情、不调用模型。周末应覆盖所有逾期未决项。

## 批次最小格式

### 只补证据、不重跑模型（2026-09-16）

`research-work-inbox.json.evidence_supplements` 是独立于旧 `items` 的补充证据待审区。
消费者读取默认研究根目录 `evidence-supplements/*.json` 的不可变描述符：
`collection/sha256/parent_collection/parent_sha256/work_id`。校验父子 collection hash、
逐正文 hash 及九股 scope 集合；错误描述符就地隔离，不阻断其他报告。

读取其逐股 `research_coverage`、`evidence_ids` 和 `terminal.findings`，再回 collection
审核原文。`pending_independent_source_review` **不代表质量签审通过**，也不替代旧冻结
模型输入、六角色或默认方向。补充资料不得追写旧 manifest，不能声称旧模型已引用新来源。
本次终态里的“有界工作完成”与“研究 unknown/blocked”必须分开解释。

当前真实样例：`reports/ta10-closeout-20260916/supplement-pointer.json`。
正常消费者会自动读取，无须新 cron、重启 worker 或额外模型推理。

### 原质量/到期审核批次

```
{
 "owner":"investment-agent:existing-daily-and-weekend",
 "reviewer":"当前实际agent/topic",
 "reviewed_at":"实际ISO时间", "next_check_at":"明确下一次ISO时间",
 "items":[
   {"kind":"quality","manifest_hash":"...","quality_path":"已逐源核对的质量JSON"},
   {"kind":"followup","manifest_hash":"...","code":"001309.SZ",
    "post_evidence":[],
    "source_work":{"status":"blocked","rationale":"具体未获原文及补采方向"},
    "short_term":{"status":"not_matured","rationale":"未到目标交易日收盘","evidence_ids":[]},
    "proposition_checks":[{"status":"not_matured","reviewer":"同批reviewer","rationale":"等待哪个后续披露","evidence_ids":[]}],
    "error_categories":{
      "data":{"status":"unknown","rationale":"缺后续证据"},
      "logic":{"status":"unknown","rationale":"经营命题待检验"},
      "timing":{"status":"unknown","rationale":"时段未成熟"},
      "pricing":{"status":"unknown","rationale":"尚无后续价格验证"},
      "unexpected_event":{"status":"unknown","rationale":"尚无后续事件证据"}}
   }
 ]
}
```

这是格式说明而非已执行证据。真实首轮接续批次/回执在 `reports/ta10-r3-20260915/`。confirmed/refuted需新证据、时间与原文类型门禁，短期未成熟不得判命中；经营判断不可引用价格当证明。quality写入不改变原manifest，只更新绑定该manifest的消费质量指针。blocked/not_matured都要具体理由和负责人，next_check_at不得留空。

## 故障恢复职责

- 同key启动失败：60/300/900秒退避，默认最多3次。下一次原evening请求或agent恢复入口触发；不是后台新增轮询cron。
- 活跃/启动中/状态未知不重复拉worker；已完成不重复模型调用。worker本身另有进程锁与完成checkpoint。
- systemd明确退出/failed后恢复同checkpoint，保留launch历史；硬超时不永久缓存。网络超时的launch_unknown须先监督确认退出，不盲目再发。
- 请求达到上限：agent调查原因后 `python3 -m mt1.ta_workflow recover --request ID --reason '实际修复原因'` 授予下一组最多3次尝试。保留理由，不无界重试。
- queued_inputs_changed：恢复入口重新计算当前identity，返回新request_id，旧失败留存。不能复用旧input伪称恢复成功。

自然调度实际完成与线上投递仍待后续回执，不因上述操作规约已接入就宣称未来事件完成。

## 主动取证修订（2026-09-16）

新 worker 在 freeze 前执行 `ta_evidence.collect`；配置与验收边界见 [ACTIVE-EVIDENCE.md](ACTIVE-EVIDENCE.md)。原财报 catalog 仍保留，但不再作为研究覆盖完整的证明。研究协议与实质覆盖分别返回；补证须另开 revision，保留实际发现时间。
