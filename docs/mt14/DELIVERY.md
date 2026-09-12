# MT-1.4 开发交付（待投资域独立验收）

工单 `work_5d6320876daa43b70a3b`；原基线 `fc0a8a7`；运行代码与合同 hash 见 `reports/mt14-20260913/code-version.json`。不 push、不部署定时器/VPS、不实盘。

## 1. 真正执行的链路

统一 CLI 已贯通只读采集/原材料归档、有据候选、两类原引擎计算、独立前向分账、证据重算、隔离晋级/拒绝/回退、报告与断点恢复。每轮最多两候选；成熟后两类独立生成后继，active 版实际参与下一代对照；旧仓继续按旧版退出，取消不复活。生产 validated 门槛未改。

## 2. 真实首轮

|类别|原版|候选版|范围/结果|决定|
|---|---|---|---|---|
|基本面|ROE≥10，PE≤25，PB≤3|ROE≥12，其余不变|当前股票池5,562；完整财务3,916；筛选50→26|continue_shadow|
|技术|signal-policy-v1，突破量比≥1.2|量比≥1.4，退出/费用不变|原授权9码（A股6、港股3），两版均HOLD|continue_shadow|

股票池快照日期 2026-09-11，实际采集/决策时间保留在材料中；不把当前快照倒填为历史 PIT。额外实际补采 daily、adj_factor，取得5,550个价格行；其余12码未造价格，不把真实持仓9只当全部选股池。

两个候选均无成熟独立事件（0）；20/40/60需继续前向等待。自然行情本轮无BUY/SELL信号、无模拟fill、无真实实验晋级。收益主指标是冻结资金槽/费用的前向信号价格观察，不是个人盈亏或可成交证明。原 observed-quote-v1 实际执行独立留账，需要两次递增报价，不从日K制造fill。

## 3. 合成证明

`synthetic-verified` 标记 SYNTHETIC_ONLY，真实样本计数0。实际20个成熟合成事件触发实验指针切换；拒绝分支、篡改拒绝/自动回退、prepare恢复和重复CLI均执行；原MT13执行引擎完成一次合成买卖往返。两版技术引擎也实际计算并分别注册/留账。独立verify重算原始合成输入和指标，不只核对通过字段。

## 4. 恢复与唯一入口

```bash
cd /home/emox/work/projects/market-tools
python3 -m mt1.iteration_loop run --root reports/mt14-20260913/real-release
python3 -m mt1.iteration_loop status --root reports/mt14-20260913/real-release
python3 -m mt1.iteration_loop verify --root reports/mt14-20260913/real-release
python3 -m mt1.iteration_loop verify --root reports/mt14-20260913/synthetic-verified
```

相同日期/请求键恢复或幂等返回；过期/失败自动重新采集，新attempt链接原回执。首个缺环境凭证的真实失败没有删除，恢复已实跑。最终重复run为idempotent=true，11,338个实验文件hash不变，零新增/变化。next_check_at已记录，scheduled=false，不声称无人值守。

## 5. 保护与测试

原全量574项，最终604 passed（新增30项）。原47,165文件无缺失，唯一获准旧代码改动是candidates.py默认等价参数化；生产策略、真实持仓/scope、自选及本机配置未变。

审计例外如实保留：并发thesis-weekly-bootstrap于05:31:09更新了生产longitudinal/latest.json索引；对应manifest与差异均保留，未覆盖该更新，原历史材料hash未变。VPS两次只读快照43项调度相同；这是后补的测量窗口，不伪称开工远端基线。

## 6. 产物与未完成的真实观察

- `reports/mt14-20260913/final-evidence.tar.gz`：最终真实/合成原材料、账本、manifest、源代码快照、保护证据。
- `real-summary.json`、`repeat-verification.json`、`code-version.json`、`pytest-final.txt`、`tripwires-final.txt`。
- `initial-failure-receipt.json`：首个缺环境凭证失败与真实补采记录。
- 原型real/real-final/real-verified目录未覆盖；它们绑定开发中较早代码，正式核验请使用real-release。

剩余是自然前向成熟样本、真实开盘报价执行及真实实验晋级/回退的观察；这些不由合成测试替代。本轮证明工程机制，不证明提高收益。调度接线按授权留待投资域独立验收后确认。开发交付不等于投资验收通过。
