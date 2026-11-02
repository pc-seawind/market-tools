# 同单修订二：堵住 reopen 省略原触发标识的漏洞

Work：`work_9e5d2aecb544757c54b4`；修订基线 `8ad4139`。
原 investment #3245 独立验收发现的阻断属实；此前缺少 missing/empty 触发标识测试，
不能把匹配标识和错配标识测试通过视为边界已完整覆盖。原交付及审核证据保留，不覆盖。

## 最小修复

- `mt1/research_tasks.py` 在原 `apply` / CAS 事务内，将有条件比较改为请求触发标识与
  原任务标识直接相等比较。原绑定存在时，省略/空字符串/null/错配不能绕过。
- 新原文 hash 校验、epoch、任务版本与研究版本 CAS、原 due 和幂等契约不变。
- 原无触发的 archive 请求不传 trigger_key 即可凭真实新证据 reopen，不虚造标识。
- check_at 到期仍投影为 review_required；走原 investment 显式 review，不自动继续，
  不用 reopen 绕过。已归档但留有原触发标识的任务仍必须匹配该标识。
- 原对账测试的“等待→到期复核→归档→重开”补上实际绑定的 filing 标识，不放宽校验。
- 操作契约的 reopen 行同步澄清；不增加配置、fallback 或语义模型判断。

## 回归与保护证据

证据根：`reports/work-9e5d2aec-r2-20261103/`。

|文件|含义|
|---|---|
|independent-review-repro.json|原请求方隔离真实 apply 的漏洞复现副本，未写生产|
|tests-before-fix.txt|新测试先验证旧代码：6 failed / 7 passed；确证 wait/block 的 missing/empty/null 绕过|
|tests-after-fix.txt|13项新增 + 原相关71项：84 passed、7 subtests passed，5.47秒|
|production-before.json / production-after.json|生产仍456事件，scope/事件 hash 完全相同|
|protection-receipt.json|16个既有无关 dirty 跟踪路径未变，原12项 artifact hash 全部一致|
|manifest.json|本轮源码、测试与证据文件 hash，独立验收可核对|

测试文件：`tests/test_research_task_trigger_binding.py`。
分别覆盖 waiting/blocked 的 missing/empty/null/mismatch 拒绝、matching 成功、
check_at 到期进入 review_required 和原 review 路径；另测 archive 无原触发成功，
以及没有新证据仍拒绝。拒绝后任务状态、版本、历史和队列均未变化。

使用 `/home/emox/work/homespace/.venv/bin/python -m pytest`，运行上述新增文件及
上一轮六个相关文件。全部为秒级临时合成 scope 依赖回归，本机串行，不跑模型/GPU。
未重跑双机矩阵、全市场计算，未重复部署无变化 cron，未新增调度或重启服务。

原已通过成果和全部其他未提交改动保留。修复提交推送后，交 #3245 独立复验；
真实公司分流、积压处理以及自然日报/周末执行仍由 investment 履职，本轮不制造公司终结。
