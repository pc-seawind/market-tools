# MT-1.1 Revision 3：向前观察自动收割（2026-09-11）

## 新增功能，而非重复pending回执
本轮仅推进自动前瞻观察，不重做已认可的P1/P2，不新建投研任务，不编造公司pass或真实持仓。
注册、冻结起点/方法、交易日成熟排程、自动收割、成本/基准/现金口径、幂等归档、缺口阻断已实现；**当前真实窗口未成熟**，与功能未实现是两回事。

## 当前真实cohort
cohort `2f11df88edd385a13f9db972`；1个稳定cohort、4714唯一股票、20/40/60合计14142条；起点2026-09-11开盘。
本cohort为宽泛诊断发现池（包含风险否决），不是推荐名单或实际持仓组合。
|窗口|当前日历预计成熟日|状态|收益|
|---|---|---|---|
|20交易日|20261019|4714 not_matured|null|
|40交易日|20261116|4714 not_matured|null|
|60交易日|20261214|4714 not_matured|null|

成熟期=entry交易日索引+horizon。未来交易所日历若修订，blocked并要求复核，不能静默改冻结日期。
首次cohort注册发生在起点开盘前；晚注册不能冒充真正向前。重复周期仍引用原decision/成员/方法/起点，不重置。

## 如何收割（不是实盘）
- 成熟前不算收益，不把不存在的未来价格置零。
- 成熟后必须有完整日线/复权/CSI300基准，以及逐公司覆盖窗口且有发表时间/hash的上市、停牌、公司行动clearance。缺任何一项blocked。
- 公司行动/复权变化未实现现金红利/送股/税处理，因此明确阻断；退市/缺行情也不从分母删掉。
- 价格路径：下一开盘→成熟日收盘；费用情景每边10bps，净收益=exit_close×0.999/(entry_open×1.001)-1；不是实际税费校准或可成交证明。
- 基准：CSI300同窗口价格指数（非含息）；每股独立1单位，退出后无息现金、不再投资、不调仓。只有冻结全体齐全才给等权诊断均值。
- 真实issuer-clearances尚未齐，未来成熟时可能自动得到blocked而不是收益；自动收割功能并不意味着全部成熟数据源已备齐。

## 本轮验证
282 tests passed，保留原195及前版262；成熟窗口、费用/基准、现金口径仅用隔离合成样本验证，没有混入生产。
独立forward审计：1cohort/4714成员/14142记录、起点与20/40/60交易日、起点归档hash、未成熟收益null均通过。
独立漏斗回归：5561/16683/4714/4冻结hash/63绑定字段通过；0 final保持，未改公司研究结论。
真实幂等重跑验证：同cohort、同artifact、全部已有文件hash和mtime未变，无新增cohort/harvest。开发期不同代码hash的历史产物仍保留，不覆盖。
顺手修复同日早晚盘benchmark end_date缓存：按交易日分别归档，晚盘不再读到早盘的基准快照；隔离测试已覆盖。

## 生产接入
parallel-cycle已自动调用forward.run并输出forward-harvest.json；四条原cron仅更新已有标记块中的收割说明，没有新增cron或更改调度/domain/cwd/delivery。
备份/CAS/独立二次readback完成，备份位于VPS `/home/emox/.homespace/cron/_mt11-sidecar-backup-20260911-054832848958`。
本轮最新真实CLI目录：`/home/emox/work/projects/market-tools/.cron_state/mt1/parallel-runs/20260910T215149232799Z-morning`；代码58af9f9；独立cgroup `mt11-r3-latest-cycle-20260911`（3900秒硬限）。
**尚未发生下一次自然cron与最终投递回执核验**；不把手动CLI代替自然链。后续待真实发生再收集run→binding→report→消息回执/readback，不以空pending回执循环触发验收。

## 产物
- `/home/emox/work/projects/market-tools/.cron_state/mt1/parallel-runs/20260910T215149232799Z-morning/forward-harvest.json`：最新周期入口回执
- `/home/emox/work/projects/market-tools/.cron_state/mt1/forward/harvests/2f11df88edd385a13f9db972/7f8923f62afc4ddddad010ffc21c213f.json`：不可变自动收割结果
- `.cron_state/mt1/forward/cohorts/2f11df88edd385a13f9db972.json`及`-start.json.gz`：稳定cohort与起点原始输入
- `.cron_state/mt1/forward/calendars/`：真实交易日历归档
- `.cron_state/mt1/mt11-r3-forward-audit-latest.json`、`mt11-r3-funnel-audit-latest.json`、`mt11-r3-idempotency-proof.json`
- `.cron_state/mt1/mt11-r3-cron-deployment.json`、`mt11-r3-cron-readback.json`
- 成熟时自动写forward/inputs和evidence-blobs；当前未成熟不伪造这些价格/公司行动证据。

## 剩余依赖（不以等待充作新工程增量）
1. 自然调度/最终投递链真实回执待发生。
2. 当前窗口未成熟，没有真实收益或有效性结论；不是等待60天才实现功能。
3. 成熟时所需逐公司行动/停牌/上市状态clearance仍需数据准备；未知保持blocked，未实现自动证明“无公司行动”。
4. 公司完整风险/证据/估值签审由原投资域接手；真实持仓/原期限未知继续保留，安全观察不强写权威计划。
5. 真正回调历史结构、完整行业模型仍shadow研究缺口；旧2024缺档独立incomplete，不重新取数。
本轮未交易、未写自选、未创建cron、未部署其他worker、未push。
