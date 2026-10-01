# R2：真实供应商只读验证与有界回归

工作项 `work_61cc3c6457c57eb9d551`；补齐 R1 的验证证据，不自行宣告验收通过。

## 真实供应商 + 隔离 verify（已实际执行）

2026-10-01 20:43:22–20:43:48 北京时间，在生产 worker `home-ubuntu`、生产项目目录，使用 `/usr/bin/python3` 与提交 `63cd761` 的取价及 verify 实现执行。不是重放模拟行情，不使用假价格/假日历。

可复现入口（输出目录必须是新目录；需现有 TUSHARE_TOKEN 环境，不打印凭据）：

```bash
cd /home/emox/work/projects/market-tools
timeout 120 python3 scripts/narrative_date_fidelity_probe.py \
  --out reports/narrative-date-fidelity-20261001-r2/live-NEW
```

脚本只对 IO 边界加记录和隔离：

- Tushare 子进程仍运行生产 `tushare.py`，追加 `--no-cache`、禁重试，避免使用本地缓存或改写生产 JSON/Parquet 缓存。
- `quote_sources.daily_bars` 仍调用原实现；记录实际返回日期和价格，不替换响应。
- 原事件和账本复制到临时目录，`verify_all` 使用复制件；行业缓存也隔离。append 路径另加断言，禁止写到临时目录之外。
- 原账本、事故目录、原纵向 archive、推荐账本、watchlist 及当前持仓 scope 都做前后哈希保护。

| 实际场景 | 数据/运行结果 |
|---|---|
| 20261001 CN 日历 | `is_open=0`, `pretrade_date=20260930` |
| 20261001 HK 日历 | `is_open=0`, `pretrade_date=20260930`；不是因 CN 日历推定 |
| 原事故两个事件、四只 CN 股票 | `verified=0`, `fetch_failed=4`, `skipped_dup=0`；隔离账本前后 SHA256 完全一致，零新增 |
| 20261001 两市场指数 | CSI300/HSI 精确日查询成功但无 bar，取价均为 null |
| CN 前日价格对照 | 300274.SZ=82.49，601126.SH=36.49，601179.SH=11.61，301308.SZ=301.93；与原错误行 current_price 一一吻合 |
| HK 20261001 单独验证 | `verified=0`, `fetch_failed=1`，零新增 |
| 20260925 独立交易日正对照 | CN 日历休市、HK 开市；同一 verify_all 中 CN 无新增，HK `00700.HK` 成功生成一条隔离记录，总计 `verified=1`, `fetch_failed=1` |
| HK 有效验证的实际值 | 20260924 close=438.4，20260925 close=436.6；HSI 分别为 24761.13、24510.09；verify_date=20260925，excess_pct=0.60，strict=null |

正对照和 HK 单独验证仅事件描述为显式 `ISOLATED_LIVE_PROBE_*`，价格、日期、指数与 baseline 解析均为实时供应商调用，结果只写临时账本，不进入生产策略样本。

### 供应商限频如实披露

`live-01/provider-calls.json` 留存 25 次底层调用的时间、参数、原始 stdout/stderr 或日线数组。HK 当日 Tushare 请求出现 40203；备用腾讯日线实际只到 20260930，所以没有被当作当日行情。HK 09/25 正对照的个股数据由生产 `tushare.py` 的 akshare fallback 实际取得，HSI 是 `index_global` 实际响应。

另一次 `hk-target-supplement.json` 仍报小时级限频。**不声称 HK 主源当日请求成功空返回**，也不把限频当作休市证据；休市证据来自独立成功的 HK 日历，日期门禁证据来自实际备用日线，正验证来自实际开市日行情。生产 fallback 全链已验证；未绕过主源限额。

## 消费方覆盖

67 项 narrative 定向测试通过，见 `targeted-tests.txt`。新增一条“账本只有四条异常”的测试，补齐所有旧记录复用入口。

| 消费路径 | 对应验证 |
|---|---|
| 全市场 snapshot / 行业 benchmark 缓存 | 精确日期、缺失/错误日期拒绝、旧缓存隔离 |
| CN/HK 个股 daily_bars / Tushare、指数 current/base | date/trade_date、两市场独立可用、baseline 日期与 session、缺指数不写 perf |
| report/doc 主统计及详情 | 4 周实际生成器 + 52 周扩大窗口；排除量和审计说明 |
| event_report、_existing_perf_keys | 异常事件无可用 perf、异常不堵幂等重试 |
| _baseline_for、_benchmark_baseline_for、_prewarm_verify_state | 异常不作为价格锚点或已成功观察复用 |
| narrative_backtest.exact_outcomes | 异常不入回测结果 |
| narrative_radar_review.load_perf_window | 异常不入审查窗口 |
| backtest_signal_compare.load_perf | 异常不入信号对比 |

四条异常默认就处于当前 4 周窗口外，不能说它们污染了当周 4 周数值；更大窗口/单事件消费风险已由全局读取 overlay 解决。没有重写原 archive 的 `verified=4` 历史证据。

## 生产脚本现状

- `deployment.json` 记录实际 hostname、解释器、import 路径及哈希；三个模块全部匹配提交 `63cd761`。
- VPS 只读配置快照 `production-cron-readback.jsonl`、`production-cwd-readback.jsonl` 确认 daily/weekly 的 prompt 先 `cd /home/emox/work/projects/market-tools`，再新起 `python3 narrative_track.py verify/doc`。
- 因为每次是新的 CLI 进程，**home-ubuntu 的这个生产脚本路径已是修复版本，不需要重启 worker/gateway**。本轮不是只测另一份 /tmp 代码。
- 没改 cron 时间/配置；没改持仓、推荐 scope。未 push、未同步其他 worker，不能据此宣称其他主机工作区也已更新。
- 不以未来周六自然调度作为完成证据；本轮依据实际生成器、实时供应商和隔离 verify。

## 回归执行口径

逐文件 20 秒上限遍历全部 54 个测试文件，全部有结果/日志，而不是让首个长测试挡住后续 narrative 和其他消费方。

第一轮记录在 `regression-by-module/summary.json`：44 文件完成通过，6 文件累计时间超限，4 文件因借用的 homespace venv 缺 requests/bs4 报错。后四文件改用生产系统 Python、仅通过 PYTHONPATH 借用 pytest 后重跑，107 项全部通过（`dependency-corrected-tests.txt`），未安装依赖或修改环境配置。

对 6 个超时处的具体测试，再逐节点 25 秒诊断，保留 10 秒 faulthandler 栈，区分“单测试耗时”与“整个文件累计耗时”。完整未完成清单、对应日志与最后运行位置见后续回归汇总产物；不以时间预算导致的未完成冒充断言失败或通过。

### 已定位的长测试，而非含糊的“全仓太慢”

- `test_iteration_mature_run.py::test_full_mature_run_pointer_crash_same_command[after_prepare/after_switch]`：源码第 34 行起逐个执行 **62 轮**完整状态机，第 61 轮再注入故障并恢复、校验全目录哈希。`after_prepare` 的 25 秒单节点诊断仍未结束，采栈停在 pathlib 目录扫描；`after_switch` 同为 62 轮，未再次单独运行。原 180 秒全仓也首先停在此处；未调低 62 轮或绕过验证来制造绿灯。
- `test_mt12_timing.py::test_archive_to_weekly_maturity_and_rolling_preservation`：源码第 419 行起 **146 次**观察/归档循环，每次触发 `mt1/timing_cli.py:38 protected_hashes` 对既有 `.cron_state/mt1` 做递归扫描；10 秒采栈实际落在该路径。25 秒未结束，属于目录规模相关的长 IO 验证。
- `test_mt1_operational_closure.py::test_live_pipeline_launches_once_but_refreshes_sweep_snapshot`：测试只 stub 了 `pipeline.subprocess.run`（第 183 行），实际新路径调用 `recap_runtime.run` 的 `Popen.communicate`（第 59 行）。采栈确认进入真实 recap 子进程等待，25 秒未结束。是旧测试的隔离边界与现有 collector 路径不一致，本轮未改 unrelated pipeline/测试隔离来掩盖该问题。

此外，iteration_revision 与 mt13 两文件原来是**整文件累计 20 秒**预算用尽，并不能说最后那条测试自身超时。逐条诊断中的对应节点分别在 11.2 秒、1.4 秒、4.5 秒通过。后续对尚未运行的节点逐条补跑，避免把整文件后半段都算作未验证；结果以最终汇总为准。

## 最终有界回归结果

全仓清单 **924 项**，通过逐文件与逐节点补跑，最终 **920 项具有实际通过证据，4 项未完成**；没有未解决的断言失败，但绝不标全仓全绿。精确 node ID 清单见 `regression-summary.json`（包含所有已通过项及未完成项）。未完成的四项就是上述 mature-run 两个参数、146 轮 timing 测试、真实 recap 子进程等待测试。

补跑期间有一条 `test_completed_real_fixture_readonly_tampering[production]` 在 faulthandler 采栈过程中以 -11 退出；原日志保留，关闭诊断采栈后同节点在 10.46 秒通过（`trace-disabled-retry.txt`）。只陈述复跑结果，不据此认定 Python 崩溃根因已修。

原请求方的两文件复现命令本轮为 **46 passed**（`requester-repro-tests.txt`），完整 narrative 相关组为 **67 passed**。所有 32 个保护文件最终哈希仍一致（`final-protection.json`）。

本轮补齐的用户要求：真实供应商日历/取价、实际隔离 verify 零误写、真实 CN 休市/HK 开市正对照、所有新读取逻辑消费方覆盖、生产路径与部署状态、全仓不能完成的确切测试及原因。上述 4 个 unrelated 长测试仍作为边界明确披露，不改变本次修复与生产数据。
