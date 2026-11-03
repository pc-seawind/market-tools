# 同单修订三：研究任务队列按实际时刻排序

Work：`work_9e5d2aecb544757c54b4`；基线 `d25d196`。
investment #3245 独立核验的混合时区排序问题属实，原触发绑定修复及已完成清理保持不变。

## 最小修复

`mt1/research_tasks.py::queue` 仍依次按：
1. 逾期优先；
2. `instant(last_attempt_at)` 实际带时区时刻；
3. `instant(due or created_at)` 实际时刻（沿用原有 due 未知时取 created_at 的契约）；
4. code 确定性排序。

同 instant 的不同 ISO 表示进入下一排序键；只改比较，不改返回时间文本或生产历史。
没有新增兼容分支、fallback、重试或配置，也未改成员过滤、任务/研究版本、原 due 和预算。

## 回归与只读核验

证据根：`reports/work-9e5d2aec-r3-20261103/`。

|证据|结果|
|---|---|
|tests-before-fix.txt|新增11项先验证原代码，6 failed / 5 passed，确证混合时区及 tie 失序|
|tests-after-fix.txt|新增11项 + 原相关84项，共95 passed / 7 subtests，5.26秒|
|readonly-queue-verification.json|真实只读队列中300725.SZ先于301132.SZ，原时间字符串完整保留|
|production-before.json / production-after.json|628事件及hash、scope hash、8持仓、2观察、监控集合、任务状态数均未变|
|protection-receipt.json|清理目录620份既有证据hash未变、16个无关dirty跟踪路径未变、前两轮artifact校验一致|
|manifest.json|本轮源码/测试/文档及证据hash，供独立复验|

新增测试覆盖 +08:00/+00:00 跨 UTC 日先后、相同 instant 的 due/code tie、due 及
原 created_at 排序键的时区比较、逾期优先、成员/持仓排除、显式 limit、原数据绑定和
旧任务优先预算（旧10/15/20、新10/5/0，总默认20）不变。实际队列只读诊断列出17项活跃成员，
并非新发17项研究任务；默认复核队列仍为10项。

测试为秒级本机合成队列和临时scope依赖回归，home-ubuntu串行，无模型/GPU、双机矩阵或全市场重算。

## 已完成清理及未发生事项

investment 已复核89项，新增72归档、保留17 active、原11 waiting不动；
本轮前后读回均为17 active / 11 waiting / 累计80 archived / 2 completed。
这是既有investment成果，本轮不重做，不重放八项补录，不写任何公司处置或历史时间。
原清理目录 `/home/emox/work/investment/reference/operations/pending-clearance-20261103/`
及旧交付证据全部保留。不重发报告、不改cron、不新增调度或重启服务。

提交push后交回原topic独立验收。自然周末尚未发生，不宣称全链路自然验收完成；
后续公司判断和自然调度消费仍由investment履职，不能把已完成的89项清理再次当待办执行。
