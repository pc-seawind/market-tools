# MT-1.2 技术择时升级：shadow 开发交付

授权 work_e36a7c662cffcc43a1d7；投资 topic #3055 独立验收。基线
4b68ef88318f8c7d14b77766c57e0960dbf699e4。没有改选股门槛/排序、候选池、用户
scope、watchlist、thesis、历史计划事件、原 cohort、VPS 或 cron。没有 BUY/SELL
执行接口，也没有 active 晋级入口。未使用或提交原有未跟踪 backtest_exit_rules.py。

## 实现及冻结范围

- `mt1/timing.py`：纯函数状态机；重用 `parallel.timing` 作旧诊断对照，而非改旧发现架构。
- `mt1/timing_experiment.py`：归因 A/B、执行证据门禁、费用情景、分层/滚动清洗边界。
- `mt1/timing_cli.py`：唯一 CLI；采集/观察/对照/周报索引四个子命令。
- `experiment-contract.json`：trial=1，无参数网格，无拟合；所有数值 exploratory。
- `tests/test_mt12_timing.py`：合成正确性测试，与真实采集和实验分目录标识。

P0 优先实现：初始风险取信号前 20 个已完成交易日的最低价，不用未来 pivot。
先登记突破 episode，再在 10 个观察交易日内累计连续 2 收盘跌回原突破水平；
没有 episode 不能叫失败突破。ATR20 为 TR 简单均值，绝非 Wilder；3 倍只是假设。
首次观测以当时收盘作最高参考，不能借首次观测前的当日最高价回填用户浮盈。
其后收盘复核用监控后最高价减 3×ATR；同 episode 内保护线不下调。
当 ATR 候选为非正数时返回缺口，不输出负止损价。

价格在内部统一为 raw×adj_factor，显示值折回当期 raw 单位；拆股不能直接拿两次
raw 价格做 max。已观测 OHLC/factor 修订、价格基准变更或历史断档阻断续接，保留
旧状态与原风险理由，需人工核对再建新版本，不能悄悄清掉风险记录。港股只有 qfq
K 线而没有可核验的跨期因子基准，本轮风险价格阻断，不假称公司行动已全覆盖。

P1 突破必须先记录 20 日区间宽度≤12% 且近 5 日高低幅均值≤前 15 日的 80%
的 setup。后续收盘突破冻结水平且量比≥1.2 才触发；当日不同时建 setup 和 trigger。
回调沿用近 MA20、低量观察，但只标 setup，下一完成交易日之后收盘回到 MA20
上方、超过前一根最高价并收阳才确认；不是“缩量即承接”。setup 10 个交易日后取消，
不是经过周末就重置。TREND 使用 rising MA60，VALUE/REVERSAL 单独走确认路径；
这不代表后两通道的经营复苏、估值或风险研究通过。现有持仓入场不合格不等于卖出。

P2 宽基/行业 RS 单列（不是 RSI）。CN=000300.SH、HK=HSI、US=SP500；行业须有
所属市场、成员口径和可知时间。缺失为 unknown，不代入 0，不跨市场套基准、不作
新增一票否决。价格相对 MA20 的 ATR 延伸与旧固定 10% 并列；2.5 倍为诊断。
波动分层代码固定为 ATR/close <2%、2%—4%、>4%，仅用于分桶而非入场门槛；
指标窗口和数值的实现版本由每次 experiment 输出 implementation_hashes 冻结。

这些少量数值用于机制验证、不是从 9 只持仓优化出的“最佳参数”。没有收益选择或晋级。

## 唯一运行入口及幂等

环境的 `/usr/bin/python3` 无 pytest；已验证的 Python：
`/home/emox/work/homespace/.venv/bin/python`。以下在 market-tools 项目根执行。
先选新的、不可覆盖的 RUN 路径。后台采集需继承 `TUSHARE_TOKEN`，不能将值打印/写入报告。

```bash
PY=/home/emox/work/homespace/.venv/bin/python
ROOT=/home/emox/work/projects/market-tools
RUN=$ROOT/reports/mt12-new-run
systemd-run --user --unit=mt12-collect-$(date +%s) \
  --setenv=TUSHARE_TOKEN --property=RuntimeMaxSec=1200 \
  --working-directory="$ROOT" \
  "$PY" -m mt1.timing_cli collect --out "$RUN"
# 采集完成后读取 RUN/bundle.json；失败保留原目录，重试用新 RUN。
"$PY" -m mt1.timing_cli observe --bundle "$RUN/bundle.json" \
  --root "$ROOT/.cron_state/mt12-shadow-evidence"
# 首轮之后必须显式带上最近完整快照的 immutable manifest：
"$PY" -m mt1.timing_cli observe --bundle "$RUN/bundle.json" \
  --root "$ROOT/.cron_state/mt12-shadow-evidence" --previous /absolute/prior/manifest.json
"$PY" -m mt1.timing_cli compare --bundle "$RUN/bundle.json" \
  --observation /absolute/current/manifest.json --out "$RUN/ab.json"
"$PY" -m mt1.timing_cli weekly --root "$ROOT/.cron_state/mt12-shadow-evidence" \
  --asof '2026-09-12T13:00:00+08:00' --epoch user-reset-20260911T120928Z \
  --out "$RUN/weekly.json"
```

`--observation` 将已归档前向监控起点作为**假设入场信号**供 A 组试验，不当作真实
推荐或用户建仓。无该参数时只消费输入内显式 entry_episodes，不凭持仓倒造买点。
新的真实准入只从同 epoch scope 的 post-reset research_event_id/admitted_at 读取。
缺 scope/损坏/旧 epoch/缺行/多行/旧池回灌全部 fail closed。

幂等键含输入实际字节、scope hash、previous manifest hash、参数和实现 hash。
相同键返回同一 manifest、不重复建事件；串行锁防并发重跑。新的输入作为新快照，
必须续接 previous，不能重置同一 episode 起点。不同日期须重新核验市场日历；
同日新行业资料可刷新 RS，但不增加观察交易日计数。按 close 信号判断，不能将
错过数日后补采的 known_at 回写成当年的实时监控时间。

## 实验含义与执行保守性

A 固定同一个可审计 entry episode，分别 old_ma60_5 / structure_failure /
structure_failure_atr。同股票 entry 间距≤60 交易日拒绝，避免叠加样本污染。
B 同一冻结范围、同一 structure_failure_atr 退出，比较旧触发/结构突破/回调确认。
使用 rolling 120 训练区（不拟合）、60 purge、60 test 的共同边界；每臂同股 60 日
不重叠，跨 test 末尾未退出不偷读后续数据，保留 not_matured。

不是所有数据都具备历史 PIT。本轮真实只有当前范围、当前供应商 vintage，不含
已退市总体/历史行业成员、逐 session 限价停牌/结算证据，不能称为有效选股回测。
没有这些执行证据绝不伪造成交：signal 收盘后，最早下一有明确可交易/结算标志
的 open；明确不可交易跳过，遇不明 open 则 unknown，不跳到有利价格。
CN 最少持有 1 交易日；HK/US 结算适用性还需明确标志。费用为每侧 CN15/HK25/US10 bps
的 all-in 假设，不是券商实际费率核验。日 K 线高低先后未知只作诊断，不按止损价
成交；跳空采用验证后的下一 open。交易次数/单边换手量、净参考价格收益、收盘序列
最大回撤、最差交易尾部、失败突破、退出后 20 日反弹及 unknown/未成熟均输出。
这不是用户盈亏、总回报或资金组合收益。未取股息现金流，收益口径是因子调整价格。

## 日报、longitudinal 与周报接入（仅交方案，投资 topic 接线）

日报原三段式不变：1 行情播报；2 趋势预测与外部观点；3 荐股/已荐股及持仓跟踪。
本模块报告只作为第三段的技术附表，不替代前两段，也不能阻塞行情发布。
`result.cards` 包含 method、日期/来源、旧入场/退出、新两路径、结构/ATR、触发依据、
未知、next_review；状态中冻结首次观察和历史条件，原风险触发持续保留等待签审。

复用 `mt1.longitudinal.archive`，归档真实输入字节、实际 CSV stdout/HTTP body、
scope/参数/代码和实际 report.md，manifest 包含真实执行开始/结束时间及每文件 hash。
Tushare 保存的是工具实际 CSV 输出（可能来自其缓存），不冒充原始 HTTP 响应。
collection 首次因 systemd 未继承 credential 失败也留档；r2/r3 成功不覆盖失败证据。
本单隔离 root，不写原 longitudinal 或原 cohort。

`weekly_index` 复用稳定 entity_id，跨周 pending、不重置原判断；20/40/60 满足交易日
历史时提供 reference_price_return（首次前向参考，不是用户收益），否则 not_matured /
blocked。投资 topic 将同样的 root 纳入现有 weekly-evidence，并将真实 execution_id
纳入新的 expected 清单版本，不覆盖既有 expected/原计划事件。独立调用 weekly
目前 expected 未提供会诚实标 observed_only/not_verified；不能据此宣称自然 cron
全覆盖。新旧出口并列保留，不改原计划 EXIT 或期限。

回退开关就是**不接入/移除 MT12 附表调用**，继续读取既有 MT11/daily_tracking。
没有配置迁移或生产 monkey patch；停止调用不删除归档。若投资 topic 后续上线，
只能 shadow，validated/active 仍需另行研究签审与成熟证据。

## 本轮交付索引

`reports/timing-upgrade-20260911/DELIVERY.md`、`delivery-index.json` 汇总最终
真实 9 行、A/B 结果、原始 hash、测试及限制。早期开发采集/快照均保留，最终以
`final-receipt.json` 指向的 manifest 为准，不相加成多批持仓或多个 trial。
