# R3：清理四项测试缺口、核实生产路由

工作项 `work_61cc3c6457c57eb9d551`。本轮不改 narrative 生产算法、不重新运行已验证的供应商请求、不发布或改绑 cron。

## 测试修复：改隔离边界，不改业务断言

1. `tests/conftest.py::isolated_protected_repository` 是**显式 opt-in** 夹具，仅供 mature-run 两个参数和 timing 长生命周期测试使用。将 `mt1.timing_cli.HERE` 指向临时保护仓库，而不是每个合成观察都递归扫描操作者不断增长的生产 archive。
   - 六个非空保护文件覆盖 watchlist、推荐、嵌套自选、plans.db、嵌套 forward-cohort；再加测试自身 scope，共七个真实哈希。
   - 没替换 `protected_hashes`，没有将它 mock 成空字典；没有改生产文件、状态机、校验、fsync 或收集数据。
   - `test_narrative_test_isolation.py` 验证完整文件枚举，并实际修改临时 forward-cohort，确认前后哈希差异被检测；代码来源仍是原生产模块。
   - mature-run 的 **62 轮 × 20 标的**、两个故障点、恢复验证、幂等与 release 数量断言均保留；timing 的 **146 轮**、成熟收益保持、周索引和重复观察断言均保留。
2. `test_live_pipeline_launches_once_but_refreshes_sweep_snapshot` 之前 mock `subprocess.run`，但真实 collector 已改走 `recap_runtime.run` → `Popen.communicate`，于是测试意外触发生产供应商收集。
   - 现在只在 `mt1.recap_collection.run` 的实际 IO 边界抛出明确的合成 `OSError`；collector 的预算、回执、异常处理及 pipeline 本体仍真实执行。
   - recap 临时输入重定向到本测试目录，不读取/改写共享 `/tmp/evening_recap_*.json`。
   - 保留原“只 launch 一次、两次刷新 snapshot”断言，并新增“实际调用隔离 IO 两次、路径/timeout 正确”断言，防止通过完全绕开 collector 让测试假绿。

运行日志见 `four-tests.txt`、`regression.txt`、`related-tests.txt`，最终状态以末尾汇总为准。长测试使用 systemd transient unit 隔离，硬预算 900 秒；曾尝试调整测试 unit 的 RuntimeMaxSec，被 systemd 拒绝，**实际仍为 900 秒**，没有修改任何生产 service/cron。`four-test-budget.txt` 保存实际预算。

## 生产路由：不是只证明“本机代码在磁盘上”

### 实际绑定与执行证据

`gateway-routing.txt` 来自 VPS 上真实 `~/.homespace/cron-managed-threads.json` 的只读提取：

| job | worker | topic | context_mode |
|---|---|---|---|
| narrative-track-daily | home-ubuntu | 3138 | fresh |
| narrative-track-weekly-doc | home-ubuntu | 3150 | fresh |

- VPS 日志记录 2026-10-01 19:45 实际触发 narrative-track-daily，并处理 topic 3138。
- `worker-dispatch.txt` 是 **home-ubuntu 本机 worker journal**：19:45:00 为 #3138 重置 fresh context，并以 investment/native-code 处理本轮。这与持久绑定、原事故所在 worker 一致。
- `gateway-process-routing.txt` 核查实际 gateway PID、cwd 及路由相关环境；没有覆盖 registry 路径，因此使用上述默认路径。
- R2 `deployment.json` 的生产 CLI 文件哈希与 `63cd761` 相符；R3 不改这些文件。此项目由新 CLI 进程读本地工作区，不是等待 worker 热加载一份 Python 模块。
- 两份 cron 文件 SHA256 与 R2 完全相同：daily `f226da977ede3542521b02c615b4d2b1076d0ee559074192a339f571f9970572`；weekly `9b6e0fa883df5287a82d2bafb1138cfe16410391d1b4b627c67b86f9a0efa3ce`。

### 未 push 的边界与回退

- 提交只在 home-ubuntu 工作区，本轮没有 push、跨机复制或配置写入，不宣称已做多 worker 部署。
- `wsl-readonly.txt` / `wsl-tunnel-readonly.txt`：WSL 的 LAN SSH 及 VPS 反向隧道均拒绝连接，**只证明 SSH 不可达，不据此判断其 worker WebSocket 在线状态，也不声称已核对其磁盘版本**。
- `office-tunnel-readonly.txt`：成功只读登录 office，确认 `/home/emox/work/projects/market-tools` 不存在。
- 因为这两项 cron 的当前持久绑定是 home-ubuntu，其他主机没有收到本地提交不影响**本次已确认的生产路径**。绑定若将来被删除/改动或发生不兼容重建，不得假定其他 worker 已拥有同版脚本和异常清单。`gateway-routing-code.json` 留存所读实现中保留 worker affinity 的片段，但不将“磁盘实现”冒充另一条实际发生过的路由。
- 未来跨机迁移前，需先在目标机器同步修复与异常清单、核对账本/归档、跑同一日期验证，再另行授权改绑。目标验证失败时保持现有 home-ubuntu 绑定即可，无需动原账本或 scope。本轮没有执行迁移。
- R3 只有测试夹具/测试及证据产物，若需撤销 R3，可在授权后仅 revert 本轮提交；保留 `63cd761` 的日期门禁与异常 overlay。不得回滚成旧算法继续跑，也不得删除事故行或重写旧 archive。

只读 `/api/health` 探测返回 400，因此未将其用作路由/其他 worker 在线性的证据；决定性证据是持久绑定与实际 dispatch journal。

## 补跑暴露并修复的时间隔离错误

首轮 `regression.txt` 为 **922 passed / 1 failed**：`test_archive_to_weekly_maturity_and_rolling_preservation` 只冻结 `timing_cli.datetime`，而 archive 使用 `longitudinal.datetime.now()` 写真实 10 月时间，再查询 9 月截止周报，自然得到空 events。此前超时使这一断言未跑到。本轮同步冻结两个模块的**合成测试时钟**，并按迭代递增秒数，保证最新事件排序确定；没有修改生产日期或 weekly_index 过滤逻辑。

- `regression-final.txt`：排除两个超长 mature-run 参数，其余 **923 passed，269.78 秒，exit 0**。
- `timing-pipeline-final.txt`：上述周报测试、collector 隔离测试及保护夹具自检 **3 passed，4.98 秒，exit 0**。
- `related-tests.txt`：narrative / 周报及 IO 隔离相关 **69 passed**。
- 全量收集 `test-inventory.txt`：**925 项**（原 924 项 + 1 项隔离保护自检）。
- `four-tests.txt`：`after_prepare` 已完整 PASSED；随后 `after_switch` 尚未完成时整组触及 900 秒，`four-tests-unit-result.txt` 如实保留 timeout。不是 5 项全绿，也不是忽略超时。
- 将尚未完成的 `after_switch` 单独放入 **1800 秒硬时限** unit，结果见 `after-switch-final.txt`。不重复已经通过的 62 轮状态机。

### 可复现命令

在 market-tools 根目录，使用 system Python + 已安装 pytest 路径（system Python 含生产供应商依赖）：

```bash
export PYTHONPATH=.:/home/emox/work/homespace/.venv/lib/python3.12/site-packages
# 常规全集，不跳过任何其他模块
 timeout 900 python3 -m pytest -q --tb=short --ignore=tests/test_iteration_mature_run.py tests
# 两个超长参数分别运行，每项给 30 分钟，勿合用 15 分钟预算
 timeout 1800 python3 -m pytest -vv --tb=short 'tests/test_iteration_mature_run.py::test_full_mature_run_pointer_crash_same_command[after_prepare]'
 timeout 1800 python3 -m pytest -vv --tb=short 'tests/test_iteration_mature_run.py::test_full_mature_run_pointer_crash_same_command[after_switch]'
```

长任务实际执行用独立 `systemd-run --user --property=RuntimeMaxSec=...`，避免占用生产 worker cgroup。夹具只重定向测试临时文件，所有生命周期、崩溃恢复、哈希保护和幂等断言真实运行。

`final-protection.json` 对照 R2 的 32 项保护文件，事故四条、原 archive、当前 scope 等全部未变。`deployment-final.json` 再次核对生产脚本和排除清单与 `63cd761` 相同；`cron-hashes-final.txt` 再次记录两项 cron 原哈希。本轮不需要等待未来周六自然调度作为证明，也没有重复真实供应商查询或重新 append 记录；R2 已复核的真实只读证据继续有效。

## 最终汇总

`after-switch-final.txt`：**1 passed / exit 0 / 525.71 秒**。结合常规 923 项与先前明确 PASSED 的 `after_prepare`，当前收集的 **925 个唯一测试节点全部有通过记录，未完成 0**。这是分批全量覆盖，不是声称单次全仓运行 exit 0；首轮失败和整组超时已原样保留。机器可读汇总 `test-summary.json`。最后再次对照 32 项保护哈希全部不变。
