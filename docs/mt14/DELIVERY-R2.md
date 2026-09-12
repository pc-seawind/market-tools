# MT14 R2 开发回传（待独立验收）

工单：work_5d6320876daa43b70a3b；worker：home-ubuntu。
原 R1 交付、独立 REVIEW/repro、失败材料均保留，不覆盖历史结论。

## 工程修复

1. **manifest 不是发布终态**：新增独立逐类别 release-stages 回执与 complete.json。manifest 后的任何中断仍恢复原 request-id 的归档评估，补齐尚未发布的类别；完成的类别不重复发布。归档前失败/输入过期仍按原合同另建 retry。失败回执保留，不以删 failure.json 获得绿灯。
2. **只读发布链验证**：校验 root schema/kind/production、候选数量/类别/白名单/ID、active 与 intent/committed、证据来源与 hash、重算评估、前序链、prepare/commit/recover 事件和逐类别发布终态。未知候选、production=true、错误类别/来源种类、未提交 intent 等拒绝。verify 不调用 recover、不初始化目录、不写锁或指针。空 root 单列 closed_loop_executed=false。
3. **补齐故障测试**：采集边界回放下实际执行 run、两类原引擎、validate、publish、verify；覆盖 manifest 后、发布证据写前/写后、第一类别完成及两类别完成但终态未落盘。另用完整共同 run 状态机、62 个 SYNTHETIC_ONLY 前向轮实际形成成熟评估，验证 prepare/switch 激活中断后同命令恢复，而不是只 mock publish/verify。所有合成数据不进入真实样本账本。

## 最终真实复跑

目录：`reports/mt14-r2/real-release`。

```bash
python3 -m mt1.iteration_loop verify --root reports/mt14-r2/real-release
python3 -m mt1.iteration_loop run --root reports/mt14-r2/real-release --request-id mt14-r2-release
python3 -m mt1.iteration_loop verify --root reports/mt14-r2/synthetic
```

实际当前数据：基本面股票池 5,562；原版 50、ROE 收紧候选 26；技术 9 只，原版/候选版均 HOLD。两类别成熟独立样本均 0，continue_shadow。真实 verify 与相同 run 均 exit 0；重复前后 11,341 个实验文件无新增、无内容改变。

## 已批准范围限制（不是工程 incomplete）

- 真实成熟样本 0，不要求制造 20/40/60 日成熟记录。
- 本轮无自然 fill、真实实验晋级/回退；不宣称提高收益。
- MT14 未部署定时器/VPS，scheduled=false；next_check_at 仅建议时间。不 push、不实盘。
- 开发完成不等于投资验收通过或策略有效。

## 保护与测量限制

- R2 开工集合 50,847 个文件（mt1、tests、docs/mt14、reports、投资域）复核无缺失；变化仅本轮获准代码/测试/文档。
- 另对原 R1 开工 47,165 文件复核，保护了顶层脏文件与本机调度配置；原脏文件 git 状态不变。R2 开工集合未单独重拍 .cron_state，使用原 R1 基线比较，不伪造 R2 前快照。
- 相对 R1 的差异：原获准 candidates.py、已披露 longitudinal/latest.json，以及本次 06:40 既有 mt13-observed-refresh.timer 正常刷新 observed-production-v1/latest.json。journal、指向 manifest 和六个原有 service/timer hash（均与原基线一致）已留证；未恢复/覆盖该并发正常更新，不能笼统声称所有生产索引字节未变。
- 开工前远端调度基线仍不存在；R1 后补两次快照仅代表当时测量窗口。本次未进行远端配置操作，不能回补或伪造开工基线。
- 开发中两个采集目录 real / real-final 在代码仍修改时触发 engine_or_contract_version_changed 并拒绝完成；保留原失败回执和材料，另用冻结版本 real-release 完成。没有重写旧 manifest/hash 以强行通过。

## 核验材料

- `reports/mt14-r2/pytest-final.txt`：625 项全量回归日志（227.12s）。
- `reports/mt14-r2/mature-tests-forward.txt`：新增完整成熟 run prepare/switch 测试：2 passed in 1189.02s。与前述回归覆盖当前收集的全部 627 项测试；不是声称单次 pytest 输出 627。
- `reports/mt14-r2/reviewer-repro.txt`：原无 mock 伪造 production active 复现现明确拒绝。
- `reports/mt14-r2/real-release-{cli,verify,repeat}.json`、`repeat-verification.json`。
- `reports/mt14-r2/synthetic-{cli,verify}.json`。
- `reports/mt14-r2/protection-comparison.json`、`original-protection-check.json`、`dirty-status-comparison.json`、`concurrent-refresh-*`。
- `reports/mt14-r2/code-version.json` 与代码快照；旧版本证据仍按旧 commit 核验，不绕过版本检查。

测试回放读取 R1 已交付 real-release 的归档输入。独立重跑测试需保留原目录，或从 R1 final-evidence.tar.gz 恢复；该夹具仅为测试回放，不是新增真实采集。

完整合成故障夹具归档：`reports/mt14-r2/mature-full-run-fixtures.tar.gz`；两根各 62 个共同 run 观测轮、各一次晋级提交。`mature-full-run-summary.json` 保留实际指针及失败回执，`mature-archive-check.json` 记录原 /tmp 根和逐字节归档校验。可按原路径恢复后用统一 verify 核验，或运行 `pytest tests/test_iteration_mature_run.py` 从头重建；不得把合成未来日期当成真实观察。

**工程 incomplete：[]。** 已批准范围限制与测量限制按上文单列；仅提交开发交付，不自行判定投资验收通过。
