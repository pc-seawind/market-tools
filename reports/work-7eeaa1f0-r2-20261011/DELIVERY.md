# work_7eeaa1f0a07c8921da50 · revision 2

## 独立验收退回的五项

|问题|本轮工程修复与证据|
|---|---|
|曾准入→pending 停止监控|按 episode 识别曾准入且未明确退出，pending 仍在 monitor_rows / daily；正式计划 scope 仍 fail-closed；保留原 due|
|推荐/退出绩效缺失，重入覆盖退出|新增只读 research-performance；按原推荐/退出事件固定日期、价格口径、币种、基准和费用。旧退出与新 episode 并存；可算推荐期间/至退出/退出后反事实，不借用 discovery cohort|
|到期复核被 4375 新项压住|独立 research-reviews.jsonl，最多 10 项，与新发现 10 项各有预算；不再受 discovery 通过条件限制；真实尝试记录轮转，排队不顺延 due|
|3 港股只是失败样例|从20261009已归档 daily 原解析行/hash及日历读取，6 CN+3 HK 全部日期匹配、daily 实际消费成功；没有重新抓取|
|近期节点被硬推28天以后|research_horizon_end（初始28—93天）与 milestones（未来任意真实近期节点、不晚于周期终点）分开。复核不默默延长原周期|

## 绩效核验，不是行情列表

- 唯一只读 CLI：`python3 mt1.py research-performance --observations FILE --out NEW_DIR`。
- 股票与基准精确日期未复权收盘价；明示双边费用情景，并给净基准及净超额。
- 来源原 bytes/hash/抓取时间校验；公司行动区间需明确原文核验。缺失、复权口径/币种冲突、
  停牌/退市、公司行动未适配、区间缺口均 blocked，不算收益；缺原始事件基准 unknown。
- 目标尚未到日或当日未到保守17点收盘完成界限 → not_matured、收益 null；不拿近邻日期补值。
- 当前支持 CNY/HKD 未复权价格情景；不宣称含分红总回报、真实执行收益或个人盈亏。
  split/dividend 等没有专门调整适配器时明确 blocked，这是核验边界，不是伪造收益。
- `scope.load` / `research-status` / `daily-track` / 周末代码正文均消费绩效结果；
  固定输入入口为 `<scope-path>.research-performance-input.json`（有原始证据才由 investment 准备）。
- `SYNTHETIC-performance/report-final2.md`：明确标注 TEST/BENCH 合成样例，显示第一 episode
  推荐 10% 毛价格回报、9.78% 净情景，旧退出后反事实与新 episode 未成熟并存。
  样例没有写入任何生产事件，仅验证算式/隔离与中文渲染。
- `production-empty-pool-audit/`：真实生产池无新推荐/退出 episode，核验器正确输出无样本；不造推荐。

## 真实消费与测试

- `actual-replay/report.md` / `receipt.json`：本轮新的唯一目录；输入 manifest、result、scope、
  provider-inputs 全部按原 archive hash 核验。6 CN+3 HK，全部 expected/quote_date=2026-10-09。
  复用已完成 frozen funnel/cohort 得4375 backlog /10新研究/9持仓/0生产复核事件。
  没有全量重采集，也没有重启成功任务。
- 本机最终相关回归：`tests-release.txt`（227 tests +25 subtests，约2秒，90秒硬限）。
- WSL `DESKTOP-OAE94K7`：实际执行**本轮新**绩效测试13项，后续新增收盘边界2项、
  异常材料边界1项；没有重跑revision 1已成功的跨机测试。各日志包含hostname/Python/hash。
  主体算式与截止/异常增量按各自代码hash留证；最终完整回归在home-ubuntu。
- 两机 Python 3.12.3，无 LLM/GPU workload 或模型加载；WSL 负载/空闲内存已核，已知租约目录不存在，
  测试独立目录+flock+60秒界限，增量进程另限256MB，不影响他人任务。
- standalone5090 / Dual5090 主机名仍不可解析，无法核验资源租约/运行时，未猜地址派任务。
  此工作只是毫秒级/秒级 CPU 读取与新测试，不需要额外 GPU 或重算成功数据。

## 不变项与独立验收边界

- scope 原字节 hash 仍应为 `8c4eb6f3f6e59bab7beeee42dbc4e35033bf88cf1a2b32130764c1d92c19a3d4`。
- 未写持仓、实盘、交易计划或外部自选；合成事件只在临时隔离 fixture。
- 四 cron 仅只读复核，hash 与 revision1 回执逐项一致；未重部署、未新增调度、未重启 gateway。
  新代码通过原 CLI 子进程与契约文档生效，不能因此声称自然任务已跑。
- 原 topic 继续负责10只真实公司研究、9持仓复核、正式入池/推荐签审，提供真实绩效基准和原文。
  自然 daily/周末正文及飞书发送/readback 待后续独立验收。
- 工程侧不替代公司判断；本次为修复后的新 revision，不自行宣告验收通过。
