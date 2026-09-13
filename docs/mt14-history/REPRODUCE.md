# 独立离线复算

最终候选数值目录：reports/mt14-history-20260913-r4。
当前交付存在新增模块自动纳入旧MT14指纹的兼容性问题，布局隔离待批准；不可称全部验收通过。

## 现有仓库

```bash
python3 -m mt1.iteration_history collect --out reports/mt14-history-20260913-r4 --offline
python3 -m mt1.iteration_history run --out reports/mt14-history-20260913-r4 --offline
python3 -m mt1.iteration_history verify --out reports/mt14-history-20260913-r4 --offline
```

collect离线只核验/复用既有响应，不重试失败；线上补采只允许既有TUSHARE_TOKEN，不打印token。首次systemd需 `-E TUSHARE_TOKEN` 继承已授权环境。不要把token写入命令值或报告。

## 不依赖原报告目录的隔离复建

1. 创建空目录，将r4/code-snapshot.tar.gz解包到该目录。
2. 创建report子目录，仅复制r4中的raw/、inputs/、checkpoints/及以下文件：
   frozen-contract.json、inventory.json、financial-data.json、warmup-data.json、collection-receipt.json、source-index.json、input-manifest.json。
3. 在新目录执行：

```bash
python3 -m mt1.iteration_history run --out report --offline
python3 -m mt1.iteration_history verify --out report --offline
sha256sum report/samples.json.gz report/results.json report/RESULTS.md
```

实际已执行隔离复建，比较见r4/independent-rebuild-comparison.json。每个输入及原字节压缩副本都在input-manifest和source-index中可核验；所有收益由原始价格面板+本次财务历史重新计算，未读取旧MT12收益。

r4/sample-audit.json给出变化月度事件、逐股动作覆盖、订单状态和90条资本账户路径审计。完整逐样本/决策/订单/财富路径在samples.json.gz。

`technical_execution` 是入场事件与其退出后现金；已占用槽标为 occupied_slot_see_capital_table，不能当未入场现金。`technical_capital` 是每股每臂相同初始10000资金的逐日账本滚动窗口，其起点是下一收盘已有账户净值，故不同于事件新建仓价格。二者均不年化，不是用户实际组合。

## 测试与版本

```bash
/home/emox/work/homespace/.venv/bin/python -m pytest tests/test_iteration_history_core.py -q
/home/emox/work/homespace/.venv/bin/python -m pytest -q
```

24项新增合成测试不作为真实收益证据。首轮系统python缺pytest、基线代码边写边测导致两项指纹失败、r3主动停止测试的旧记录全部保留。

原dd8a5f8在隔离源码环境验证旧R2 real-release成功；当前新增iteration_*.py使同一验证报engine_or_contract_version_changed，见两个old-engine-*-verify.json。禁止重写旧manifest或放松旧code_hashes解决。
