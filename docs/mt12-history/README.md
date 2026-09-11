# MT1.2 五年真实历史研究入口

工单 `work_9d513102eda442f41a04`；基线 `f2512e9`。这是新增研究，不是旧工单复交。

**交付结论见 `reports/mt12-history-20260912/DELIVERY.md`。** 自动逐项数据表在
`full-report.md`、`full-summary.csv`，完整交易/分层/折在 `full-results.json`。

## 完整复跑（不需要联网、token 或付费）

```bash
cd /home/emox/work/projects/market-tools
PY=/home/emox/work/homespace/.venv/bin/python
RUN=reports/mt12-history-20260912
systemd-run --user --unit=mt12-recheck-$(date +%s) \
  --property=RuntimeMaxSec=1800 --working-directory="$PWD" \
  "$PY" -m mt1.history_research --out "$RUN" --offline
# 日志：journalctl --user -u <上述 unit>；阶段：RUN/stages.jsonl
# 完成后：
"$PY" -m mt1.history_research --out "$RUN" --verify
"$PY" -m pytest tests -q
```

同一 run 不要并发执行。入口收集器校验每份 raw checkpoint 原字节 hash；按年份切片，
每次远端请求30秒、至多3次，权限错误立即停止该接口，失败原文保留。中断后同命令
重跑，成功 raw 不重抓；任一股票采集失败会完整记入 collection_failures 且进程 exit2。
会重建派生 panel/collection/results/report，不覆盖原始 raw、冻结合同或旧MT12交付。
阶段日志追加，最长单股计算约十余秒。无900秒无事件失联。

首次执行去掉 `--offline` 并用 `--setenv=TUSHARE_TOKEN` 继承现有凭证。不能打印
凭证或采购新权限。参数、样本与开始结束日期来自 run 内预先冻结合同，试验开始后
改动会被首条 start 日志的 hash 检查拦住。若另建研究，先创建新隔离 reports 目录
和新 frozen-contract，再运行，不改真实候选scope/持仓。试验参数仍必须等于trial1。

`--pilot` 只跑冻结名单前3只；本次先完成pilot的非空闭合，才扩大到12只。不是挑赢家。
旧报告无改动，原始152证据hash由 `--verify` 再验。

## 执行与信号的边界

- `history_research.py` 是独立版本化历史成交模型，重用原 `timing.step` 和旧
  `parallel.timing` 信号。`timing.py` / `timing_experiment.py` 没有修改。
- 信号先按真实日历逐收盘生成；两成交情景消费相同信号，事后价格/停牌/限价不反馈
  到信号。任何 missing bar 重启暖机；不删除日历缺口再把相邻两根当相邻交易日。
- open_at 是开盘session标签（09:30），不是历史委托成交时间戳。日线开盘值并不能
  验证集合竞价成交数量。日线供应商历史证据只用于离线成交情景，绝不伪写 known_at。
- `open_price_limit_base_v1`：买涨停开盘/卖跌停开盘不可成交，已知停牌保守跳过。
  `any_limit_touch_conservative_v1`：买入日high触涨停或卖出日low触跌停，也整日跳过。
  后者是压力情景（可能恰好改善收益），不是开盘前可知策略；日内停牌保守整日跳过。
- 缺原价/因子/限价/行情，立即unknown，不寻找后面有利价格。限价用原始价格比较，
  跨期收益/指标用raw×factor；不能拿调整价与原始涨跌停价直接比较。
- T+1按共同交易所日历序号；风控收盘触发，严格下一可成交open，不按止损线假成交。
- A固定每fold首个old_trigger episode（跨fold至少61个交易日）；三个退出完全同入场。
  B同scope、同fold、同structure_failure_atr退出，分别采用old/breakout/pullback。
- train120/purge60/test60，stride120、不拟合。状态可使用此前已完成数据，不能使用
  后续价格。test末未闭合保留not_matured/pending，不偷用下一purge或test。
- 单边15/30/50bps是模型费用敏感性，不是实际券商费用；成本不改变信号或成交日期。

## 研究限制和真正未完成项

诚实研究限制：当前vintage、目的性大盘流动性样本、幸存者/选择偏差、非历史PIT
选股；无历史行业/退市总体；日K无开盘队列和容量；删失、少簇、共同市场相关；
调整价格收益非现金流总回报，事件收益非资金组合，未模拟资金/持仓组合约束。

后续数据/实现未完成：完整港股A/B。`hk_adjfactor` 和 `hk_daily_adj` 均真实40203
无权限；`hk_daily`与日历可取。腾讯替代请求虽返回247根day，但没有可独立核验的
factor anchor，仍缺HSI兼容基准和历史执行证据。不能将价格可得冒充完整港股完成。
详见 hk-feasibility*.json、hk-alternative.json、hk-tencent-alternative.json 及 raw
中的两份failure响应。US不在本单样本。

## 归档说明

- raw/*.json = 实际HTTP响应字节；raw/*.meta.json = 请求（不含token）、来源、采集时间、hash。
- panel-*.json、*-collection.json 是同入口可重建的大型派生中间文件，git仅保存其gzip
  归档，复跑会生成JSON。gzip解压后的SHA256见 derived-archives.json。
- first-pass/*.gz 保存先试跑/第一次扩样结果，不能叠加进最终试验样本数。
- full-results/paired/summary/coverage/report = 最终研究结果；reproducibility.json =
  两次离线重放内容一致性；verification.json = 开发侧逐交易独立算术/边界审计。
- acceptance-checklist.md = handoff12项映射；投资topic自行复验，开发侧不标验收通过。
