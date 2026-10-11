# work_7eeaa1f0a07c8921da50 工程交付，等待 investment 独立验收

## 本轮已接线

1. 修复 `parallel.py` 研究准入死锁，独立持仓外队列/十只批次/全部持仓复核。
2. 独立 append-only 研究事件账本：pending/watch/recommended/rejected/exited，
   幂等、版本 CAS、scope 字节 CAS、epoch 隔离、证据冻结、准入及签审字段、容量、过期、退出/重入。
3. scope **只读投影**，不改用户 JSON。正式计划仍校验当前研究事件；不会自动 BUY/激活 shadow。
4. daily 实际读观察/推荐/持仓及有界退出后验证；周末代码正文附三表。
5. 现有四个自然 cron 的 prompt 已备份、CAS、原子替换、读回；所有非 prompt 字段不变。
   `watchlist-decay-weekly.json` 现场仍不存在；没有新增调度，没有重启 gateway。

## 实际数据与中文正文

- 原不可变 run：`.cron_state/mt1/parallel-runs/20261010T210329576158Z-sunday`。
- 数据日 20261009：5573 扫描、4856 发现；4375 持仓外 backlog、10 任务、9 持仓复核。
- 旧批次：腾景/工业富联/宁德/兆易，均为持仓；新批次 10 项均非持仓。
- `offline-final-20261011T094717Z/`：funnel/cohort 原 bytes、hash、scope、旧新对照、批次/队列。
- `reader-report.md`：可读中文正文；不是 CLI 成功替代报告验收。
- `daily-offline-consumer.json/.md`：真实 daily 函数消费同日 A 股 panel，9 行，6 成功，
  3 港股因不在此 panel 明示失败；没有用旧数据补齐，没有产生生产入池事件。
- 生产 scope SHA256 始终为 `8c4eb6f3f6e59bab7beeee42dbc4e35033bf88cf1a2b32130764c1d92c19a3d4`，9 持仓/0 观察/0 重点推荐。
- 所有运行均标注手工离线验收，不是自然任务。旧材料与失败日志保留。

## 测试与真实主机

- `targeted-final.txt`：198 项相关回归，另 7 个 subtest。覆盖九持仓零候选、
  daily 观察/退出消费、旧计划/epoch/过期/错误 hash、并发版本竞争、scope 并发变化、
  原逻辑保留、自然 prompt 备份/字段不变、流水线重试签名。
- home-ubuntu：协调、代码、只读冻结 cohort 重放及主回归。
- WSL `DESKTOP-OAE94K7`：确实执行隔离目录中的 8 项 stdlib 生命周期测试，
  Python 3.12.3，tar/源文件 SHA256、hostname、日志和 exit0 留于 `wsl-commit-662448f.txt`（主体实现版本；之后 pending→退出边界补丁由本机回归验证）。
- 无 LLM/GPU 计算，不涉及模型 pin；两机 CPU 运行时均 3.12.3。WSL 核验负载低/内存空闲，
  未发现已知位置的资源租约目录，任务用独立目录、flock 和 60 秒硬超时，未驱逐作业。
- standalone5090 / Dual5090：本机 SSH 配置、VPS workers.yaml 没有对应可验证入口，
  主机名也未解析；无法核验租约/运行时，不猜地址派任务。这部分四机审计未完成。
- `regression.txt` 保留过宽集成测试选择在约 2 分钟被协调器终止的记录，不计通过；
  随后只跑相关回归并设 90 秒硬限。第一次 system python 缺 pytest，改用已有 homespace venv，未安装依赖。

## investment 交接及未完成

- 字段/CLI：`docs/research-pool-lifecycle.md`；真实任务：最终 replay 的 `research-batch.jsonl`。
- 工程没有代写公司研究、没有准入新股票、没有修改实际持仓、计划账本/交易或外部自选。
- 请 investment 对 10 项逐项研究，未研究/源站受阻/部分研究分别写 pending；
  研究不合格需证据，不得把未知直接淘汰。正式准入只写真实研究事件。
- 重点推荐的估值/风险签审、真实入池→正式计划→daily 非持仓自然消费链路仍待实证；
  合成测试与离线队列不替代这个验收。
- 退出后已接后续行情监控，**未实现独立的研究池收益核算器**；当前缺已签审基准，
  中文表明确收益待核验，不能把 discovery cohort 的 forward 收益冒充推荐/退出绩效。
- 下一自然周末三表/每日监控、最终公司结论、飞书正式发布与 readback 尚未发生。
- 不自行宣告验收通过，由原 investment topic #3245 独立验收。
