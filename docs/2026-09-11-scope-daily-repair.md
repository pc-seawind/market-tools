# 用户清池后的 scope 与 daily 修复交付

工作项：work_849ab6618c2d18055563；原投资 topic #3055 独立验收。本记录不是验收通过或自然投递回执。

## 实现

- `mt1/scope.py` 统一只读行政范围；CLI 默认显式使用投资域 tracking-scope，缺失/损坏即 fail-closed。库函数无环境变量时保留历史/测试兼容，生产调用须设置 `--scope` 或 `MT1_TRACKING_SCOPE`。
- Store 活跃计划查询、计划写入门禁、pipeline、parallel bridge、coldstart、finalize pending 与 watchlist dry-run 经过 scope。重置后迁移仅保存 legacy 原文，不从旧推荐/thesis创建活跃计划。parallel 原始扫描/cohort保留，research queue/batch才做范围过滤。
- 持仓确认独立 overlay，actual_cost=null，不修改原状态/版本/基准/期限。非持仓已准入候选为 not_held。候选不能仅靠今天重跑旧计划复活：scope 中必须有重置之后的 admitted_at、research_event_id；计划还必须绑定同一 scope_epoch、research_event_id。新准入数据由投资域管理，本交付不写它。
- 新 `daily-track` 不依赖计划存在或 final BUY，覆盖 confirmed_holdings + active_recommendations。9只持仓逐只行情、行情日期、日涨跌、独立市场门禁；没有成本/推荐基准则个人盈亏和推荐收益为 null。候选、历史收益和全市场扫描不混入此表。
- enrich 不需要 evening JSON；逐只检查市场日期/日线、缺失thesis与失败进manifest；任意1—4只失败均partial、exit75、非silent。无pillar证据改PENDING；无基准不触发默认价格止损。支持 `--out`、`--dry-run`，manifest明确written=0。
- narrative 默认当日精确取价；精确日期缺失不取前一交易日。指定历史日期仍可独立补采，历史样本不删。
- 两个模板生成器保留daily-report-policy、tracking-scope及日报定位；主生成器对早晚报仅替换自己的标记区/追加标记区，保留现有其他cron正文。sidecar不再要求研究批次占日报第一屏。没有执行任何部署器 `--apply`。

## 真实只读验证

`reports/scope-repair-20260911/`：

- `real-quotes-final.json`：9持仓、0候选、0活跃推荐，9行报价成功。CN/HK行情日期2026-09-11；US独立日历expected_date=2026-09-10；没有以CN门禁代替US。没有US持仓，因此没有US个股报价实测。
- `real-enrich-final.json`：没有传evening JSON，9只生成预览，failed=0、dry_run=true、written=0。
- `readonly-reprobe-receipt.json`：两次真实命令exit0；watchlist、scope、plans.db和44份thesis前后SHA256完全一致。
- `real-scope-audit.json`：历史账本64计划，范围内8计划。第9只德福科技没有计划，但行情仍有行；宁德时代原EXIT保留。账本前后hash一致。实际读取的最近原始funnel为5561行（不是把用户提到的4714诊断直接当总池数量）；活跃候选0。
- `real-bridge/bound-review.json`：只读绑定8历史计划，另列 holdings_without_plan=[301511.SZ]，范围统计9/0。
- `real-coldstart/`：实际只读导出8份范围内计划，无旧64全量回灌。
- `tests.txt`：302项测试通过。包含9/0、64/4714隔离、缺失/损坏scope、旧准入事件拒绝、无BUY成本报价、独立市场失败、1—4只enrich失败、无晚报依赖、日期回退拒绝、无默认止损、默认PENDING、模板规则回归。
- `SHA256SUMS`：以上产物及实际输入副本hash。真实产物与测试fixture严格分离；大型真实bridge输入副本保留本机，不必重复提交原始扫描大文件。

## 原投资 topic 接线命令（本轮未修改cron/用户数据）

持仓及本轮推荐行情：

```bash
python3 /home/emox/work/projects/market-tools/mt1_job.py \
  --scope /home/emox/work/investment/reference/tracking-scope.json \
  daily-track --out /tmp/daily-tracking-<本轮唯一ID>.json
```

thesis富集验证（保留 `--dry-run` 不写用户数据；投资域决定实际落盘时自行去掉）：

```bash
python3 /home/emox/work/projects/market-tools/mt1_job.py thesis-enrich \
  --scope /home/emox/work/investment/reference/tracking-scope.json \
  --thesis-dir /home/emox/work/investment/thesis --date YYYY-MM-DD \
  --dry-run --out /tmp/thesis-enrich-<本轮唯一ID>.json
```

耗时任务用独立systemd-run/RunDetached；`mt1_job.py`继承已有worker行情凭证，不输出凭证。必须检查manifest及exit75，不能把少数失败静默吞掉。旧 `mt1.py verify` 仍是独立历史episode价格表现验证，不是新的活跃日报入口。

## 尚未完成 / 验收边界

1. 新行情表有真实价格跟踪，但没有自动解释自由文本入场/失效条件；condition_status明确not_verified。原推荐基准逐事件重建、复权/费用总收益没有补造，推荐收益当前保守为null。需投资域结合正式研究记录核验条件；本代码不是完整日报生成器。
2. 没有执行VPS cron修改、正式thesis写入或自然飞书投递；这些归原topic接线及后续自然任务验收。未自行声称七条全部验收通过。
3. 未重跑全市场实时parallel-cycle；验证了实际旧账本、历史funnel及只读bridge/coldstart，范围过滤有回归测试。US仅验证真实独立日历，无US持仓行情实测。
4. 原始news research/trade pool与历史cohort保留，未将其当用户候选；没有为清池删除历史观察数据。

仅提交本工作代码、测试、生成器与交付产物；并发投资域watchlist变更、原有JSONL变化及其他未跟踪脚本不纳入提交。没有push（未收到此次push指令）。
