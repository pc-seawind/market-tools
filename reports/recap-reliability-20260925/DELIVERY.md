# recap 采集可靠性修复交付（待原 topic 独立验收）

工单：work_61180bc0afd04c2148c0；源 topic #3108。
代码提交：45e3ef0；未 push。执行主机 home-ubuntu。

## 结论

- 不是靠延长 timeout：单板块上限仍 180 秒，总预算仍 2280 秒，pipeline 外层仍 2400 秒。
- 原版存储芯片板块冷缓存实跑达到 systemd 210 秒硬超时仍未完成；修复版同板块冷缓存 128.943 秒，7/7 股票完整采集。
- 实际脚本有限范围验证：3.884 秒被预算打断 → 13.953 秒恢复 → 再次执行 picks 次数 0。
- 原 9/23、9/24 partial 经实际 mt1.pipeline 在隔离状态目录重放，分别保留 3、4 个成功板块；没有重算历史数据、改原归档或生产账本。
- 167 项回归通过。原投资判断规则、技术参数、持仓 scope、华泰全文、单 doc 发布逻辑、群及 cron 调度未改。
- 采集完成不等于公司研究完成；复核 0/9 仍未完成。也没有宣称全周 material_gap 62 已消除。

## OBSERVE / HYPOTHESIZE / ISOLATE

已查 gateway work-items：本工单为唯一 running 项；相关已完成的华泰接入 work_8589a04c66187c547536、单 doc work_2f810291d3d8361702aa、scope work_849ab6618c2d18055563 和 TA 恢复 work_854551e568a8903434de 均保留，不重复开发。
读过 8e70ce8 的完整改动和测试：它解决共享预算及落盘，不负责 partial 恢复或消费者分层。本次在其上增量修改。

|日期|已证实表现|边界|
|---|---|---|
|9/21|report.json recap subprocess 超时 2400 秒|原始逐 RPC trace 不存在，不能事后指认某一 RPC|
|9/22|同上|不以今天取数伪造该日缺失数据|
|9/23|41 评分、selected 18、attempted 12、成功 3；timeouts + budget exhausted|fresh=true 不代表逐股完整|
|9/24|41 评分、selected 21、attempted 13、成功 4|错误整批被消费者拒绝，文件存在又阻止重采|

证据和原件 SHA256：timeline-evidence.json。修复后核对原件 hash 全部未变。

排查的三个方向：
1. “一个 RPC 挂住”——有限冷测完成的最慢调用约 9.18 秒，不支持这个解释；历史是否偶发单 RPC 超时仍未知。
2. “provider 重试退避”——代码存在 35/45/55 秒重试，但本次冷测没有命中限流错误，不能说它是这次复现的主因。stock RPC 现在只尝试一次，失败交由同输入恢复，总 sector attempts 上限 2。
3. “串行累积 + 重复取数”——已复现。冷测停止前完成 75 个 CLI 调用，累计 206.38 秒：daily_basic 108.63、daily 43.71、adj_factor 40.04 秒；七股串行总量超过 180 秒。暖测 85 调用累计 8.05 秒，存在重复 ETF/财务请求，证明 cache 状态显著影响耗时。

首次 systemd 未继承 token 的无效探针留存 baseline.*，不纳入性能结论；修正为仅按变量名传入现有 provider 环境。无 token 内容落盘。

## 实现

- sector_picks.py：股票线程池固定 2，稳定输入顺序；通过 RECAP_SCORE_FILE 复用本轮 Tier1，不重复计算 score；不调整四层评分和技术参数。
- recap_rpc.py：stock RPC 同时最多 2 个、启动间隔至少 0.5 秒；记录板块/股票/API、开始与结束、耗时、错误、trade_date/ann_date/end_date/requested_trade_date。provider 内部重试关闭；成功非空结果按输入 hash 做 6 小时缓存，失败不缓存；过期、未来时间及未来数据拒绝复用。
- evening_recap_data.sh：同输出文件非阻塞 flock；同日、同输入和代码 hash 才恢复。成功板块复用，失败/未执行项接续，单输入最多尝试 2 次。snapshot_created_at 不随重复运行延长；原失败内容寻址归档到 .history。预算耗尽仍输出 partial。
- recap_runtime.run：独立 process group + 超时冻结并清理子孙进程，包括工作线程产生、另起 setsid 的 RPC；有真实子进程延迟写入回归证明不会泄漏。
- mt1.pipeline：文件存在但 partial 不再永久跳过；已完成 recap stage 重新读覆盖；只允许当日 evening 重采，不补造历史。消费者独立保留成功板块与失败原因；逐股 trade_date 校验；内部 RPC 缺失板块不能冒充成功。
- 原评分仍作为诊断展示，缺少逐行 vintage 的旧分数不计完整事实；neutral/proxy/stub 显式分级。machine candidates 不等于投资指令。
- 所有生产 ledger/scope/发布代码未动。真实测试禁止 _append_history，没有重跑九股华泰。

## VERIFY

解释器：/home/emox/work/homespace/.venv/bin/python（系统 python 无 pytest）。
regression-verified.txt：167 passed in 6.89s，覆盖：
- 原 recap 预算回归；同日 partial 恢复、输入变化失效、成功缓存幂等、失败最多两次；
- 缓存过期/未来、未来 payload、部分失败、预算耗尽、并发/限速；
- nested setsid 子进程清理；pipeline 已 done 的 partial 不再固化；
- MT-1、归档/时点、并发、tracking scope、华泰及 cache union 相关回归。

真实数据结果：verification-summary.json、systemd-evidence.txt、*-baseline.jsonl、fixed-rpc.jsonl。
- 冷测原版：RuntimeMaxSec=210，Result=timeout（原始失败保留）。
- 修复版：128.943 秒，7/7；sector_score 和所有 verdict 与暖测一致。唯一观测值差异是 provider 再取数导致 300223.SZ turnover 4.3072→4.3079，没有改变判定。
- 最终脚本恢复：verified-real-partial/restored/idempotent.json 及其 consumed.json。第三次 n_picks_run=0；恢复成功但 complete_score_facts=0（proxy 评分不冒充事实）。
- 真实旧 partial 消费：archived-23-consumer、archived-24-consumer，实际 pipeline / calendar / 原始数据，独立状态及文档目录，无生产发布。
- 当下完整 pipeline：verified-consumer。实时 cn_calendar 返回 9/25 market_closed，pipeline 正确未消费；没有伪造交易日绕门禁。恢复后的逐项数据已通过实际分层消费者 view 验证，但这不等于自然交易日报发布闭环。

成本：仅一个慢板块的有限测试、缓存恢复及单次日历获取，无华泰/LLM咨询、无外部发送。API 计费接口不可见，不能捏造金额。CLI 次数不是远端 HTTP 次数（可能命中 provider cache）。冷测修复版 systemd 记录 memory peak 2.3G，不能据此断言同量匿名内存；本次不进一步增加并发。

## 研究复核 0/9：独立缺口

读取 .cron_state/mt1/reviews/d06cc9f449b36b3e-20260923-232204514964.json：
- reviewed_plan_ids=[]；research.fetch_status=partial；仅德明利解禁 PDF 一条源。
- 明确 gaps：“九股估值及完整公司签审未完成；不把新增解禁事件当经营证伪”。
- 九条 pending 被写在 research.review_items；finalize 只读取 bundle 顶层 review_items，所以 pending_review_ids=[]。errors=[] 只是事件执行无异常，不代表九股审查完成。
- 最小接续由 investment research owner 处理：逐项绑定 plan_id + expected_version，把未完成原因放顶层 pending review_items；补经营传导/财务/估值及合格源，再提交经过既有 validator 的研究事件。不能仅把 reviewed_plan_ids 填满。此工单没有修改 finalize 门禁或完成这些研究。

## 未完成 / 后续 owner

1. investment 日报 owner：下一个正常交易日晚报自然验证（日历当下休市）；确认真实投递仍保留华泰全文/单 doc 及 coverage，未新增 cron。
2. code 采集 owner：没有全跑 21 个板块或整个日报，不能承诺全量都在预算内。HTSC refresh/Tier1 的细粒度 RPC 尚未全部纳入 stock trace；保留既有整体预算，后续若该阶段仍慢应单独测，不能无证据增并发。
3. code 采集 owner：限速作用于本 collector 的 stock RPC，不是其他独立工具/进程的 provider 全局配额协调器；达到两次失败上限须读 trace 定位后明确接续，不能无限重试。
4. investment 研究 owner：九股签审、8 项复核日未知及全周 material_gap 62 均未补齐；不属于本次采集修复成果。
5. 历史失败没有逐 RPC 日志；今日冷测证明机制可复现，不证明所有历史超时都同因。

不自行标记验收通过；以原 topic 独立验收为准。
