# MT14 R3：仅测试时钟修复（待独立验收）

工单 work_5d6320876daa43b70a3b，home-ubuntu。R2 独立复验的 12 failed / 613 passed 为有效失败，旧日志和交付不覆盖。

## 变更范围

只修改 `tests/test_iteration_revision.py`，不修改 mt1 引擎、验证/发布实现、费用/候选合同或生产时效门禁。回放 fixture 同时固定 `action_loop.now` 和 `iteration_loop.now` 在归档决策时点（两模块各自绑定 now）；只在 pytest 的 monkeypatch 生命周期内生效，不改系统时间、不改归档 asof/fetched_at、不 mock 引擎、validate、publish 或 verify。

## 新回归

- 模拟底层墙上时钟已到 2040 年，归档决策时钟独立固定；完整 run/verify 仍可回放。
- 原 REAL_CURRENT 输入在 1,800 秒边界仍执行原 observe，在 1,801 秒被原 `live_bundle_stale_recollect_no_historic_order_backfill` 拒绝；拒绝时无引擎快照发布，输入字节不变。
- 同 request-id 的首次 run 消费过期 R1 归档，必须在真实引擎门禁失败。再次 run 必须重新调用采集边界、生成新的 retry 目录，并消费 R2 中另一份较新的原始归档；原失败材料 hash 不变、恢复链接存在、两类原引擎及完整 verify 成功、第三次 run 不再采集。
- 上述“重采”测试是两份保留真实采集的边界回放，不宣称本次又做了网络采集，也不把旧 bytes 改为新时间。

## 验证命令

```bash
/home/emox/work/homespace/.venv/bin/python -m pytest tests/test_iteration_revision.py -xq
/home/emox/work/homespace/.venv/bin/python -m pytest -q --basetemp reports/mt14-r3/full-test-fixtures
python3 -m mt1.iteration_loop verify --root reports/mt14-r2/real-release
```

最终实际结果见 `reports/mt14-r3/targeted-first.txt`、`pytest-full.txt`、`real-verify.json` 及结构化交付。全量命令不排除两个完整成熟合成测试。

## 版本与保护

`engine-version-proof.json` 比较本轮前后全部 code_hashes，并与 R2 已交付真实轮一致。现有真实材料只读 verify exit 0、incomplete=[]；按本次授权不重复抓取全市场，不改写旧 manifest 来放行。

本轮前保护集合覆盖 mt1、tests、docs/mt14、R1/R2 reports、.cron_state、投资域、本机 systemd 配置及仓库顶层文件；前后差异按实保存。所有 R1/R2 原失败包保留。没有 push、实盘、调度/VPS部署或生产配置变更。

## 范围与测量限制（不冒充工程失败或投资成功）

真实成熟样本仍为 0、无自然 fill 或真实晋级，MT14 scheduled=false；不声称收益提升。开工前远端基线缺失继续披露，未回补；R2 已记录的并发既有 MT13 索引刷新不撤销或覆盖。开发交付不等于投资验收通过。
