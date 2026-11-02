# 研究任务出池：续办交付，交原 investment #3245 独立验收

交付容器 `work_9e5d2aecb544757c54b4`，追溯旧单 `work_6098d526633febaab69b`（#3420）。
旧单 Gateway 状态 failed、delivery_revision=0；本机旧执行已停后才接管，无 assistant 并行委派。
基于 `8aedcbd` / `fef94d0`，没有重做容量修复，没有重放已完成八项补录。
实现提交：`a72dc61`，已推送 `origin/main`。

## 工程结果与原九条验收目标的对应

|目标|本次工程交付/证据|
|---|---|
|1 状态分离|新增 mt1/research_tasks.py，复用原事件账本，独立任务 entity/version；investment 负责具体缺口与处置|
|2 出池与监控|真实准入/证据充分 rejected 仍走原 research-event；archive 不撤销有效准入、不删除持仓及曾准入 episode 监控；exited 仍独立明确|
|3 无进展处置|两次旧证据/文字刷新强制 review_required；due 不顺延，延长保存原 due 和具体原因；等待/受阻到触发/最迟检查才进主动队列|
|4 实际消积压|独立旧十项优先；逾期超过十项借新研究预算，最多旧20/新0；实做推进 last_attempt 排序；发现/候选批次不算启动，存储不拒写|
|5 显式重开|每日发现不能重开归档/等待；新可核验材料显式 reopen，触发标识匹配、generation/理由/version 留痕，重复原请求幂等|
|6 旧记录|110 项只读分流清单；年龄/原 due/重复/缺口/建议/历史完整可审查，未知字段明确待分流，零自动公司终结|
|7 中文对账|四个 phase 正文嵌入出入账、历史和总未解决；修正周末正文/JSON 窗口不一致；CLI、字段与中文样例见操作契约|
|8 测试/旧边界|71 tests + 7 subtests、关联87 tests +21 subtests（后者含两项重复部署回归）；pending 无限、watch50/recommended10、epoch/CAS/来源/签审边界保留|
|9 接管/调度/交付|115808 个无关 dirty 路径 hash 未变；复用旧双机32 seeds；仅现有四 cron 的 prompt 备份/CAS/读回；无新增调度、改群、改时间或服务重启|

以上是实现/验证覆盖，不是替请求方宣布九条已独立验收通过。

## 只读生产快照（不是本次执行的公司分流）

- 事件仍为 456，research-task 生产事件 0；scope/事件 hash 前后完全相同，8 只持仓未改。
- 证券状态 pending108/watch2/recommended0；任务投影：强制处置复核48、未分类60、真实完成2。
- 活跃待办108，逾期99，总未解决108；waiting/blocked/archived 均为0。
- 以北京时间 11/2 零点为期初，100 + 新启动10 − 历史真实完成2 = 108。
  这是现有历史事件的账，不是 code 本轮启动十家公司或完成两家公司。
- 110 项包含2项原已准入任务；9项 due 未知，全部未补写具体任务字段，不把未知当通过。
- 原八项补录已由 investment 完成（448→456）；本单没有再写。

## 可直接操作

完整契约：[research-task-disposition.md](research-task-disposition.md)。
逐项清单：[只读分流清单](../reports/work-9e5d2aec-20261103/readonly-inventory/report.md)，
[含原版本/原文引用的 JSON](../reports/work-9e5d2aec-20261103/readonly-inventory/inventory.json)。
先读当前 research-status，按任务和研究两个版本 CAS 由 investment 提交真实 triage/review/wait/block/archive/reopen。
不要以本清单批量制造 rejected 或按年龄归档。

```sh
cd /home/emox/work/projects/market-tools
python3 mt1.py research-status --out /absolute/new-run/status.json
python3 mt1.py research-task --input /absolute/真实任务处置.json
python3 mt1.py research-event --input /absolute/真实公司研究结果.json
```

## 验证及发布回执

本单证据根：`reports/work-9e5d2aec-20261103/`。
- tests-release.txt：当前 home-ubuntu 的71 tests/7 subtests；真实 CLI/幂等、无进展/延期、等待触发、队列轮转、归档监控、四 phase 日/周对账。
- tests-dependent.txt：87 tests/21 subtests；原绩效、正式计划绑定、tracking scope、parallel 和调度字段保护。
- reused-compute.json：旧 5090 主机 aaa-System-Product-Name 的0—15 seeds、Dual5090 主机 s-Z890-AERO-G 的16—31 seeds，均成功，保留 runtime pin/CPU 租约/checkpoint。
  不重跑成功计算；说明旧 pin 与最终 inventory/外部 list 校验两处差异，新增分支由本机验证，不把旧证据冒充最终整文件 hash 的物理运行。
- dirty-protection.json：无关115808路径未变；本机完整原始清单另存 dirty-before.json（未把31MB临时清单塞入git）。
- production-before.json / production-after.json：事件456、持仓8、scope与事件 hash 均未变。
- cron-deploy-receipt.json：四 cron 已实际更新且读回，所有非 prompt 字段相同；备份位于 Gateway `/home/emox/.homespace/cron/_research-pool-backup-20261103-013040410353`。
- artifact-manifest.json：本次证据文件 hashes；旧单原报告和失败证据仍保留，未覆盖。

测试使用 `/home/emox/work/homespace/.venv/bin/python -m pytest`（系统 python3 没有 pytest）；
全是临时合成 scope、不联网、不跑模型/GPU。秒级依赖断言本机串行，不为展示分布重算已成功任务。
生产 CLI 子进程在既有 home-ubuntu 工作区加载已推送代码，不要求重启长驻 homespace 服务。

## 尚未发生，明确交回 investment

1. 基于110项清单的真实公司分流/继续依据/等待触发/搁置及具体任务字段，由 investment 判断并写入。
2. 下次自然日报/周末实际消费、推进积压、最终中文发布与读回尚未发生，不能用 fixture 测试或 prompt 更新冒充。
3. 需要额外来源证明的公司研究及签审继续由 investment 负责；新 hash 是否实质新增证据不是 code 的公司判断。

请原 topic #3245 独立验收，未自行标记 accepted。
