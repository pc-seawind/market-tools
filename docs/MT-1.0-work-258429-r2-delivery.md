# MT-1.0 第二轮交付：全量闭环完成，真实链路分层待验

工单 `work_2584297bb8bd6304e99f`；2026-09-11 北京时间约02:45。
四项原始阻塞已获独立认可，本轮不重做。此交付不自行标整体accepted。

## 本轮真正完成

|退回项|工程与真实证据|
|---|---|
|全市场不再只取两只|目标完成交易日2026-09-10，**5561/5561已尝试、closed=5561、pending=0、complete=true**；3915可用、1646不可用；可用覆盖70.40%，请求覆盖100%|
|有界续批/失败重试|按100只自动续批，先全量首遍再重试；3915股1次、1646股2次，记录7207次尝试。受控中断后从逐股checkpoint恢复，没有重置覆盖；独立unit硬上限2h、生命周期最多3次启动。中断时在途读取可能重取，记录次数不是网关物理请求绝对计数|
|可用/不可用闭环|逐股原始财报/来源时间/hash、history、缺失原因、批次日志已落盘。1645项缺PE、65缺PB、42缺ROE、12缺日行情日期/换手（原因有重叠，不是相加后的股票数）；不推断成公司投资风险结论|
|研究池可消费|全量screen得到49个quality_value_shadow候选；不能成为最终BUY。晚盘后台采集，早盘复用对应expected_date的结果，周末保留原asof读取最新结果；不是只采后无人消费|
|真实留证机制|run进程/worker/HEAD/源码hash/manifest/report、实际bundle及finalize、文档/消息返回与readback逐层关联；调度收集器只读日志，人工run不能冒充自然调度；hash变动/缺文件显式待验|
|真实WATCH链路|生产一致性副本＋真实日历/行情＋首批10项真实待复核计划，run→pending bundle→finalize→飞书创建→读回成功。没有新状态事件，投研覆盖仍0/64，不凑final|
|64项审查交接|逐项中文缺口、原始snapshot/legacy_source、版本和响应模板，10项一批共7批；其中63证券＋1个_bootstrap_state元数据，后者明确标记而不删历史。未来迁移不再新增此类伪证券；已有账本保持原样|
|历史数据实际推进|清点缓存行/字段与通道历史缺列；真实无缓存探测权限；三股×五接口15任务全部取数成功；另保存退市339项、日历731行、停牌13行、基准181行和一份公司官网原PDF。不是空泛列缺口|
|线上必要配置|四既有cron仅更新prompt，备份＋CAS＋回读；保留domain/cwd/delivery/silent/model/调度，不新增cron，不重启gateway|

全量 `summary.json` 的日期是 **2026-09-10**，不是9/11尚未收盘的未来行情。
早盘提交后真实回归也确认：report.asof=9/11、quality_value.asof=9/10、5561全量、49shadow、errors=[]。

## 运行与权限证据

- 代码主提交 `d371002`，后续收紧重启预算、防fixture触发真实采集、早盘/周末复用及回归；
  功能代码最终 `62242d9`。指定venv全套 **109 passed in 0.90s**（既有91项全部保留）。
- 首次真实文档路径在提交前工作树执行，其run记录HEAD仍是7cd14ac，**没有改写为新commit**。
  提交后又在 `62242d9` 做了真实run＋pending finalize回归，SOURCE hash已记录；为避免重复副作用，未再次发布云文档。
- 原生产64项仍版本1、final=0；快照hash与初始交接一致，没有修改公司投资结论/持仓。
- 全量unit `mt1-sweep-2026-09-10-df43771a`；最终生命周期启动预算为StartLimitInterval=infinity、Burst=3，
  避免6小时滚动窗口在长失败后过期造成无穷重启。此配置已现场回读。
- 四cron配置备份：VPS `/home/emox/.homespace/cron/_mt1-engine-backup-20260911-022118966551`。
  当前绑定均home-ubuntu（topics 2647/2661/2701/2716）。gateway对暂离线保留绑定，但陈旧/不兼容绑定
  可重选worker，因此**不宣称永久硬pin**；未擅自部署其他worker，prompt要求非home-ubuntu停止报缺口。

## 用户可见真实产物

[真实待复核计划验链文档](https://tcnv6xag1i9w.feishu.cn/docx/SJUfdUe1noJgtCx1l2jcxReLnhf)

文档已用hs_read_doc读回，包含 `real-watch-review-r2` 和10项真实方向/缺口。
历史EXIT仅原样显示，不是本轮新退出。provider返回及读回原文均归档，不以链接字符串代替回执。

## 可直接复验的入口

根目录 `/home/emox/work/projects/market-tools`：

```bash
/home/emox/work/homespace/.venv/bin/python -m pytest tests -q
python3 mt1.py evidence --kind inventory
python3 mt1.py --state-dir .cron_state/mt1-live-validation evidence --kind inventory
cat .cron_state/mt1/sweeps/2026-09-10/summary.json
cat .cron_state/mt1/r2-full-sweep-completion-proof.json
```

- 全量原始证据：`.cron_state/mt1/sweeps/2026-09-10/`（symbols、financial、batches.jsonl）。
- 冷启动交接：`.cron_state/mt1/coldstart/2026-09-11-4be36dbb59f6d76c/`。
- 手工真链路：`.cron_state/mt1-live-validation/chains/real-watch-review-r2/`；仅缺dispatch，按设计不伪造。
- 提交后真回归：`chains/real-watch-current-head-r2/`，仅本地，不再次发布。
- 运行手册：[MT-1.0-operations.md](MT-1.0-operations.md)。
- 逐项数据工作单：[MT-1.0-data-gap-tasks.md](MT-1.0-data-gap-tasks.md)。
- 机器可读汇总：[MT-1.0-work-258429-r2-evidence.json](MT-1.0-work-258429-r2-evidence.json)。

## 未完成项与下一责任/时点

1. **自然调度四链路待时点**：9/11 07:15、18:30；9/12 07:15；9/13 05:00（北京）。
   每次收集实际dispatch/run/bundle/finalize/最终回执，不能把这次手工真数据链路算成四自然任务。
2. **公司证据审查归investment**：逐批核验原文、原始事实和中期理由；不能核验者继续pending。
   报告链路已能输出真实WATCH，不被0 final卡死，但code没有代替公司研究。
3. **精确三通道20/40/60回测仍incomplete**：fina_indicator_vip和anns_d实测无权限；
   财报缓存27770行中21867缺ann_date，修订版本被主键覆盖；13641历史信号全部缺明确通道、方法版本和可用时间。
   公开PDF路径日期与列表发布日期也不自动构成首发时点证据。D1—D7给出具体任务、参数与已补切片；
   **历史asof通道适配器仍待开发**，不是全部问题都能归为外部权限。无精确收益，所有新因子保持shadow。
4. **其他worker/自选真写入不扩权**：当前绑定在home-ubuntu，但不保证陈旧绑定永不重选；
   无合格final候选，自选保持dry-run，不执行交易，不为了回执制造候选或操作第三方账户。

未push；无关三个untracked文件保留。用户批准范围内仅发布了一份明确标注的真实待复核验链文档，
没有发送合成标的报告，没有新建重复cron，没有覆盖原有未提交文件。
