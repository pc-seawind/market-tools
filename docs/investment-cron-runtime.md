# investment cron：调度与产物交接

- 原 16 项调度不改时点和飞书目标。三项纯脚本在 home-ubuntu 的
  `investment-cron-*.timer` 启动；其 Gateway 同名配置禁用并留作回退，
  其余 13 项由 Gateway 运行。
- 17:00 预筛归档三源原文、候选及可用 HTSC 线索；17:15 radar 读本日已登记路径、
  负责原文核验及研究判断。HTSC 不是一手证据。
- 18:00 持仓跟踪运行已登记的 `mt1_job.py daily-track`，不调用模型；17:30
  thesis 保持 dry-run，并在完成归档后登记原 summary。18:30 晚报通过
  `investment_cron.py evening` 只读本日两个交接；真实缺失与不同采样口径
  按报告原契约处理，不扫旧目录，也不复制或补造 KDJ、成本与技术动作。
- 19:45 T+N 使用明确当日目标；行情失败同日最多再试一次，原有写入幂等
  跳过已成功的 pair。无异常不发消息；原静默任务的异常发送到原群，保留回执。
- 三个脚本任务的 `registered/YYYY-MM-DD.json` 是当日运行终态；
  结果目录按 job 和发生时点唯一，不补跑既有生产结果。
- 前瞻清单在配置发布时集中生成并登记到
  `investment/reference/operations/investment-cron/expected-executions.json`。
  仅覆盖登记的窗口；排程/scope epoch 变更或期满在部署侧更新，不追认历史。
  weekly 索引直接读取归档 manifest，不重新哈希已提交的 blob；
  开发排查可单独调用 `mt1.longitudinal.audit_archived_bytes`。
- 切换/回退：留存完整 Gateway 前态、计划及回执；仅在不临近触发的时段，
  用 Gateway cron lock 比对前态再切换。回退先确认三个本机 timer 停止且没有
  同名运行，再按记录恢复对应 Gateway 定义；不删已有结果，不补跑历史。
