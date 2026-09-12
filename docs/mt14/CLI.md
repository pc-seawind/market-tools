# MT14 唯一 CLI

在 market-tools 仓库运行，Python stdlib。没有定时器、VPS 部署或生产写入。

```bash
python3 -m mt1.iteration_loop demo --root reports/mt14-20260913/synthetic
python3 -m mt1.iteration_loop run --root reports/mt14-20260913/real
python3 -m mt1.iteration_loop status --root reports/mt14-20260913/real
python3 -m mt1.iteration_loop verify --root reports/mt14-20260913/real
```

run 自动采集技术数据、确定最新完成交易日，复用相同日期 sweep；缺失时在实验目录实际尝试有界补采（100 股一批，其余披露不足）。同时实际补采日价格/复权因子，以当前存续股票池进行前向信号评价，不把现持仓当成全部选股池。原 sweep、scope 均只读。

默认幂等键为 UTC 日期；同一天重复相同命令恢复已有阶段，完成后只做核验不重复执行。次日同一命令创建新观察轮。`--request-id` 用于显式新轮，不能覆盖原轮。若失败或中断超过原 MT13 的 30 分钟输入时限，同一命令自动创建 retry 子轮重新采集，旧材料与失败回执保留并链接后继轮，不绕过原引擎门槛。

`--sweep` 可只读指定源目录；日期与新采技术完成日不一致时实际补采，不改写指定目录。`--scope` 可指定只读 tracking-scope。

每轮：inputs → selection → 两类 engines → frames → evaluation → manifest → 隔离 release。
阶段结果 hash 固化，互斥锁覆盖全部运行；失败写 failure.json，总状态 incomplete，CLI exit 2。归档验证不会信任调用者通过字段或计数。

基本面前向收益、技术前向 BUY 信号及 MT13 报价模拟分别留账。前向主指标为固定资金槽、次个观测交易日收盘起的 60 日净价格收益（成本为合同场景），不是历史成交回测；真实模拟执行仍由原 MT13 observed-quote-v1 独立管理，不用日 K 伪造执行报价。当前 CLI 采集日行情，不替代开盘/实时执行采集器。

候选参数只允许白名单。基线/候选账本不同目录；晋级/回退只改变 releases/<category>/active.json。现有虚拟仓保留原版本退出，人工取消由原 MT13 cancel 保留，不复制真实持仓。原生产 validated/active 不变。

独立核验会读取归档原字节，重算基本面、技术动作、frames 与决策，并核对源 hash 和当前引擎 hash。代码版本改变后旧轮按原 commit checkout 在独立 worktree 中核验；不静默把新引擎当成原版。

尚未部署调度，所有 scheduled=false。next_check_at 仅为下次建议检查，不保证执行。开发测试与合成晋级证明工程分支，不证明投资策略有效。

## 连续迭代与仓位归属

未成熟候选参数和准入股票池固定（新上市股票不偷偷改变既有 cohort）。成熟后两类独立推进：拒绝/实验晋级后，按冻结步长 ROE +1、量比 +0.1 生成后继，白名单耗尽则 keep，不无限搜索。实验 active 版本实际作为下一代对照基线计算；原始生产基线继续输出。旧版有虚拟仓或未终结信号时继续按旧策略观察退出，取消/暂停不会被新代复活。

遇到真实未终结信号，run 对对应隔离账本调用原 run_worker 的30 秒有界 watch 模式（observed-quote-v1 必须取得递增时间/成交量的两次报价，不能用单次 once）采集和消费真实执行证据。非合法窗口、休市或证据不全保留回执，不伪造 fill；没有信号不空跑执行采集。未部署定时器，错过窗口不会补造历史报价。

真实首个完整原型轮位于 real/（代码 7bb7bee）；后续改进线性校验和后继代切换后，最终核验轮另存 real-final/，不改写原型证据。最终验收命令以交付记录中的 real-release 和 synthetic-verified 为准。

## R2 发布事务与只读核验

manifest 只标记归档检查点，不再代表发布已结束。`release-stages/<run>/` 保存每类别绑定 manifest/evidence/evaluation 的完成回执及 complete.json。manifest 后中断或发布失败，原 request-id 同命令恢复原轮，不补采替代已冻结的评估；旧 failure.json 保留。只有归档前失败/过期才另建 retry。终态之前 status/verify 显示 incomplete。

verify 对 root 身份、归档候选类别与白名单、active/intent/committed、证据重算、前序链和发布事件交叉核验；发现异常只读报错，不执行 recover、不改指针。空 root 的 closed_loop_executed=false，不混同已执行闭环。正常 run 仍在互斥锁内执行恢复。

测试使用明确 SYNTHETIC_ONLY 的完整共同 run 状态机积累成熟窗口并注入 prepare/switch 中断；内部 `_kind` 用于此隔离测试，CLI run 固定 REAL_CURRENT，不允许命令行覆盖为合成后冒充真实。合成测试数据不计入真实首轮样本。
