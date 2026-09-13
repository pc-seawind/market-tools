# 独立离线复算（r5 子包布局）

收尾工单 work_8cdf9bc302eb707e4cc3，关联原 work_9ad289b0ff80eb2f74c8；原单未 accepted。
正式候选目录 reports/mt14-history-20260913-r5；原 r4 不覆盖。

本次仅将新增三个模块移到 mt1/iteration_history/{__init__,data,report}.py，增加 __main__.py 保留 CLI。
旧 iteration_*.py glob 不再纳入新功能；旧引擎、旧 code_hashes、原 manifest 不改。
历史功能使用独立 history_code_hashes，覆盖子包及原 mt1 顶层 Python 依赖，verify 拒绝缺项、增项和修改。

## 本机复验

```bash
python3 -m mt1.iteration_history verify --out reports/mt14-history-20260913-r5 --offline
python3 -c "from mt1.iteration_loop import verify; print(verify('reports/mt14-r2/real-release'))"
/home/emox/work/homespace/.venv/bin/python -m pytest tests/test_iteration_history_core.py -q
/home/emox/work/homespace/.venv/bin/python -m pytest -q
```

完整测试需约数十分钟，使用独立 systemd-run --user、RuntimeMaxSec=7200、日志/exit 文件；不前台堵塞、不重复启动。
合成测试只验证正确性，不作为收益证据；既有 tests/test_iteration_revision.py 在时钟边界固定回放时刻并覆盖未来墙钟，不改生产门禁。

## 不依赖原结果的独立复建

1. 新建空目录，将 r5/code-snapshot.tar.gz 解包到该目录。
2. 新建 report/，仅复制 r5 的 raw/、inputs/、checkpoints/ 和下列文件：
   frozen-contract.json、inventory.json、financial-data.json、warmup-data.json、collection-receipt.json、source-index.json、input-manifest.json。
3. 在独立目录运行：

```bash
python3 -m mt1.iteration_history run --out report --offline
python3 -m mt1.iteration_history verify --out report --offline
sha256sum report/samples.json.gz report/results.json report/RESULTS.md
```

已实际执行，两命令 exit 0。r4-comparison.json 与 independent-rebuild-comparison.json 证明三核心文件 byte hash 相同；冻结合同、原始输入索引、费用、名单无变化。
不运行联网 collect。本轮复用185成功响应/1权限拒绝；首轮186缺凭证记录仍保留。
r4 用其原 code-snapshot 复验，不用新子包伪称旧源码指纹仍相同。

## 验收入口

- USER-RESULTS.md：从 results.json 自动派生的 20/40/60 收益/中位数/正收益/路径回撤/差额及现金、未知、未成熟、成本压力表。
- RESULTS.md：原格式完整统计及年度/2024—2025后段表，与 r4 原字节一致。
- samples.json.gz：逐事件、决策、订单恢复、90个同初始资金账户路径。
- protection-check.json / protection-explanation.md：两阶段保护核对及差异说明。
- old-r2-current-verify.json：当前仓库直接复验旧 R2 真实根 exit 0。
- DELIVERY.md：完整测试终态、提交和交付边界。

technical_execution 是入场事件及退出后的现金；占用槽不是现金。technical_capital 是已有账户净值的窗口，不是事件新建仓收益；两者不年化，不是用户组合。
基本面60日受ROE变更影响的两个事件未成熟，不能将已知均值相同解读为无效应。
HK预热、财报修订、ST/历史池和幸存者偏差仍披露；仅探索性历史诊断，非严格未知OOS，不自动晋级。
