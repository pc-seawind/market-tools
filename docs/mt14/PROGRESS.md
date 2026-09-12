# 实施进度（非验收声明）

工单 work_5d6320876daa43b70a3b；已批准 16 项，串行执行，无 push/部署/交易。

|任务|状态|证据/命令|
|---|---|---|
|01|completed|HEAD=fc0a8a7；baseline/git-status.txt；protected-before.json；系统 python 缺 pytest，改用现有 homespace .venv 跑原全量|
|02|completed|iteration_policy.py / CONTRACT.md / test_iteration_policy.py|
|03|completed|独立根/阶段事件/hash/锁/幂等；新增测试累计 8 passed|
|04—05|completed|原字节归档与来源/时间/scope 校验；c80fbd9|
|06—07|completed|两类原引擎执行与分账；abebda4|
|08—09|completed|前向60日重算、去重、同股不重叠、费用风险；98ab65c|
|10|completed|原子实验指针、prepare恢复及篡改自动回退；ae4b3c7|
|11—12|in_progress|统一CLI/阶段接续/归档重算/回执；已明确超过MT13 live时限的恢复限制|
|13|in_progress|新增故障注入及完整demo测试|
|14|in_progress|首个合成 CLI 实跑 exit 0；正式归档待最终代码后复跑|
|15—16|pending|真实首轮与最终全量核验|

原全量：现有 homespace .venv pytest，574 passed in 49.71s。保护清单涵盖仓库旧文件、.cron_state、投资域、当前 worker systemd/cron；VPS 调度未访问、未改动，未声称已核验远端 hash。
