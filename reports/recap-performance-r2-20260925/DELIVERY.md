# 性能专项 R2：冷路径最小候选、默认阶段计量、MT13 fixture 收尾

工单 `work_44a640f764a00aa83f9f`，基线 `8659cd7`。实现 `d3499f3`，测试时钟修复 `f4eff9b`；均未 push。本轮按独立验收要求有限收尾，不重跑 21 板块，不扩大并发、预算或调度，不重查华泰。

## 1. 已验证并接入一个冷路径候选

选 **按交易日批量 daily_basic 的四个当日字段**：`pe_ttm,pb,total_mv,turnover_rate`。不触碰历史 PE、daily、adj_factor、财报字段/窗口/修订合并。

### 有限真实接口验证

`recap-daily-basic-probe-20260925.service` 独立 systemd、RuntimeMaxSec=180；按本机已验证日历只探测 9/24 已完成交易日，没有执行或绕过 9/25 生产门禁。预算最多 8 请求，实际第一轮 5 个，后续修订对照 1 个，**总计 6 个 Tushare HTTP、华泰 0**。无重试、单请求 timeout=20s、串行。

|真实请求|结果|
|---|---|
|trade_date=20260924, limit=6000, offset=0|成功，5557 行，3.166s，has_more=false|
|limit=2, offset=0 / offset=2|各 2 行，与大页对应位置一致，分页实际生效|
|603986.SH、688525.SH 当日四字段单股对照|与批量投影逐字段一致，分别 0.082s/0.080s|
|300223.SZ 修订对照|0.107s；当前逐股结果与新批量一致，旧缓存换手率不同|

111 只待扫 A 股中覆盖 109，`002013.SZ`、`600260.SH` 未出现在该日批量返回；**不猜停牌/退市原因，不视为零值、不跳过股票，保留原逐股回退**。不能把 5557 行说成已证明覆盖所有证券。服务端冷热未知；真实探针没有使用本地 query cache。

### 发现并处理真实时点差异，而非刷等价绿灯

第一次用七股旧快照比较失败，保留失败 systemd 回执及 `snapshot-mismatch.json`：`300223.SZ.turnover_rate` 旧逐股缓存 **4.3072**，新批量 **4.3079**。补一次当前单股查询返回 **4.3079**，支持期间数据修订，不是删字段/四舍五入掩盖差异。

因此接入优先级是：
1. 原同输入 run-cache 优先；
2. 任何有效原逐股 exact-query cache 优先（包括前一天落盘、仍符合原历史缓存策略的快照；新批量不能覆盖）；
3. **仅 exact-query miss** 且批量同输入/同数据日/本日创建、30 分钟内有效、所需字段完整时投影；
4. 缺股、过期、字段错、页失败、无权限或页预算用完，一律原逐股路径；不会把失败板块删掉。

批量 artifact 只是当前 run 的输入面板，原 RPC cache 和 single-flight 继续复用；没有再造长期跨日缓存。批量最多 3 页，总子任务最多 45s 且受原共享剩余预算限制。不缓存不完整面板。若终末空页不能区分失败与真实为空，保守回退，不宣称完整。

真实面板一次约 3.17s，小批未必划算，所以默认只在**至少 12 只尚未完成且没耗尽两轮尝试的 A 股**时准备批量面板；重试复用，不重采成功股票。单小板块和全部已完成输入不为此额外请求。原每股 API 数量上，消除的是五类中的“当日 daily_basic”这一类，**其余四类尤其历史 PE/daily/adj_factor 仍保留**。

### 同输入冷 miss 对照（冻结数据，不冒充真实全量网络）

独立 `recap-batch-benchmark-r2-20260925.service`，RuntimeMaxSec=180，exit 0。真实网络 0；采用实际面板原件，七股逐股取数独立原件（六份旧数据相同，一份改用本轮真实修订单股回执），全指标 hash 前后一致。35 份原始查询快照已按 SHA 校验并固化到 `frozen-inputs.json.gz`，离线复现不依赖未来可能刷新的全局 cache。模型重放：面板 3.1657s、逐股当日 81ms，其他 API 模拟 10ms；保留原 0.5s 启动间隔，缓存均设为 miss。

|范围|前→后请求数（模拟）|前→后 wall|输出|
|---|---:|---:|---|
|七股完整 compute_stock，强制试验 batch（低于生产启用阈值）|35→29|17.025→16.763s|7/7，全部指标 hash 相同；收益很小，不能据此强推小批|
|109 股的**当日 daily_basic 子阶段**|109→1|54.113→3.340s|109/109，四字段 hash 相同|

第二行中除七股有独立逐股原件外，其余字段来自同一冻结面板，属于编排/限速成本及投影等价验证，**不是 109 次真实单股查询对照**。本轮不为测试再付 109 次请求。两只缺失证券的逐股 fallback/失败覆盖由测试验证，不能加到 109 的“批量成功”里。

这是冷请求数量从逐股变为按日共享的进展，不只是 R1 暖缓存启动节省；但不能据此把后台 40 分钟直接减成某个承诺数字。自然全链会受其他历史 API 和 provider 延迟支配。

## 2. 默认实际入口已补阶段计量

`mt1_job → pipeline → recap_collection → evening_recap_data.sh` 默认不需要新增参数：

- 自动落盘 `<OUT>.performance.jsonl`。
- HTSC refresh、score、picks 各有 stage_id、UTC start/end、monotonic wall、退出码；复用/跳过显式记录。
- Tushare 真实 `urlopen` 路径记录 HTTP start/finish（含实际 retry 次数）；query-cache hit/miss/stale/negative/invalid 单列，score 进程内 fina memo 命中另列（空数据不冒充可用）。
- HTSC concept/raw indicator cache 计数、桥接调用/尝试计数单列。**桥接调用不等于已掌握其服务端内部 HTTP 数量**。
- recap 逻辑 RPC、CLI 启动、run-cache/exact-cache/batch 投影命中单列；这些不是互斥 HTTP 指标，不相加成远端请求总数。
- 原 per-sector attempts / RPC trace 保留，stock 子阶段不会覆盖 collector 的 stage 标签。
- 多进程追加用 flock，SIGKILL 没有结束事件就保留 `unfinished`、wall 未知，不用 RPC 累积时间代替 wall。
- `recap-recovery.json` 自动包含 performance trace、汇总及 inspect 命令；最多两轮、原 2280s 共享预算、失败隔离不改。
- stage status 只表示进程退出；`exit 0` 仍可能数据 partial，**覆盖质量继续以原 checkpoint coverage 为准**。

检查示例（自然轮产物，以实际 OUT 为准）：

```
python3 recap_observability.py summary /tmp/evening_recap_2026-09-25.json.performance.jsonl
```

已验证实际 shell → 新 stage runner → 实际 Tushare main（仅传输替换为 synthetic）→ 实际 recap RPC → batch 投影路径。回执 `default-path-receipt.json` 三阶段均有完整边界；picks HTTP=3（freshness、日历、面板各一次），另有 batch 命中/run-cache 命中。默认完整 mt1 CLI 两轮恢复测试也断言三阶段数据进入 recovery receipt，成功/终态场景都覆盖。**这是默认代码路径集成证明，不冒称下一自然交易轮已经发生。**

## 3. MT13 失败精确根因与归属

Owner：`code:MT13 tests / fixture clock isolation`，不属于本次缓存优化的生产回归。

精确链：
- `action_demo.fixture()` 固定从 2026-09-14 起；测试推进至 **9/22 18:00+08**。
- `cancel()` 合理地以 `max(now(), state.asof)` 记录操作时间，本次真实 now=**9/24 18:48 UTC（9/25 北京）**。
- 测试随后又提交固定 **9/23 18:00+08** 的下一个 fixture。
- `action_loop.observe: out_of_order_no_backfill` 正确拒绝：这条观测早于取消操作。失败会随真实日历跨过 fixture 日期开始出现，与本轮优化无关。

只修测试：取消操作时使用 fixture 时间+1分钟，作用域限于该调用，随后恢复真实 clock。新增反向测试：操作时间显式 2030，提交旧观测仍必须拒绝。**生产 action_loop / 策略 / 时点门禁一行未改**。真实前后 asof 与异常回执在 `mt13-clock-rootcause.json`，不是仅说“既有失败”。

## 4. 验证与边界

本轮最终完整相关回归包含 R1 曾失败的同一组 MT13 action/execution/windows，未排除该测试；还包括 batch 缺股/失败/分页/字段/缓存修订优先级/过期/未来时点、完整 sector 7/7 和失败 6/7 的评分/信号/排序/覆盖等价、stock trace、默认两轮恢复、预算和华泰全文。

最终 **194 passed in 67.32s**，见 `final-related-regression.txt`。生产历史原件和四份 investment engine-contracts hash 均未变化，见 `protected-originals.json`；测试传输隔离，无生产账本/源文写入。首轮等价失败与 synthetic fixture 导入冲突均保留诊断记录，不删除失败证据冒充一路绿灯。

**本轮有限收尾无待修必需项**。正常范围边界单列：
- 没全跑 21 板块冷网络，没得出 P95；这是明确不要求的范围，不冒称整条 40 分钟问题已完全消失。
- 原历史 trace 缺口不可恢复，9/24历史发布时戳仍未知；下一自然轮现在会有阶段 trace。
- 未实现历史增量/财报修订合并/其他批量 API；本轮只选了一个有证据的最小候选，不要求列举方案全做。
- 主日报仍沿 R1 已验证独立路径，以 18:30 后 25 分钟完成回读作观察目标，不保证 SLA；本轮收益主要在后台采集，不能归功为主日报发布提速。

交原 topic 独立验收，不自行标 accepted。
