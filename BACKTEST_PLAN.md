# Path B — 历史 narrative events 回溯计划

> **目的**: 在 W22 (2026-05-23~30) 现有 37 events 之外, 手工构造 30-50 个历史 events, 用来验证 v4 模型 (8 维 narrative + 1 维 ticker_layer) 是否在不同时段仍然单调有效.
>
> **不做**: 把 W22 之后的现实 events 等齐 — 那个等就行了.
> **做**: 主动回溯过去 1-2 年市场上明显是 "narrative 级" 的事件, 反推涨幅, 打标 9 维特征, 跑 cross-tab 看 hypothesis 是否仍成立.
>
> Last updated: 2026-05-31

## 一、立项决策点 (需要 emox 确认)

### Q1. 选什么时段?

候选 (按推荐度排序):

| 时段 | 离 W22 | 标志性 narrative | 建议 events 数 | 备注 |
|---|---|---|---|---|
| **2025-Q3 (7-9月)** | ~9 个月 | DeepSeek R1 + 国产算力第二波 / Nvidia Blackwell 量产 / 苹果 AI 端侧 | 15-20 | **首选** — 主题密集, 涵盖 AI 全链, 涨幅已尘埃落定 |
| **2025-Q1 (1-3月)** | ~14 个月 | DeepSeek V3 破圈 / 国产替代第一波 / Sora 2.0 概念 | 10-15 | 次选 — 时间稍远但叙事清晰 |
| **2024-H2 (7-12月)** | ~17-22 个月 | Blackwell 发布预热 / 光模块 800G→1.6T 切换 / 苹果 M4 端侧 AI | 10-15 | 备选 — 风险: 当时 ticker 业务边界跟现在不同 |

**推荐**: 先做 **2025-Q3 + 2025-Q1** = 25-35 events, 跨度 6 个月, 覆盖 2 个明显的 narrative 周期.

### Q2. events 候选清单怎么来?

**方法 A — emox 凭印象列**: 你列 10-20 个 "我记得当时这条新闻引发了一波 narrative 涨幅" 的关键节点, agent 去验证日期/标题/涨幅. **优点**: 利用 emox 的实战经验, 选出真正的 narrative 级事件. **缺点**: selection bias (只选事后赢的), 模型会被 inflate.

**方法 B — agent 系统性扒**: agent 用 search_baidu 按 subdomain 关键词 + 时间窗口扒 (e.g. "国产芯片 2025年8月"), LLM 二筛选 score≥2. **优点**: 无 selection bias. **缺点**: 工作量大, 容易漏关键节点.

**方法 C (推荐) — 混合**: 你列 3-5 个 "黄金 narrative 节点" (如 DeepSeek V3 / R1, Blackwell 发布) 作为锚, agent 在每个锚前后 2-4 周扒补充事件 (含输者), 强制每个时段都有 score=3 / score=2 / score=0 的样本各 1/3. 这样既利用了你的经验又控制 selection bias.

### Q3. 涨幅反推数据源?

A 股日线数据, Linux 可跑的免费库:
- `akshare` — 国内最常用, 覆盖 A 股 + 申万行业指数 + 大盘. 推荐.
- `efinance` — 类似 akshare, 数据源是东方财富.
- 备选: `tushare` (需 token, 有限额) / `baostock` (老牌但维护慢).

**输入需要**: T+0 trade_date, ticker_code, hold days (14/28).
**输出需要**: T+0 close, T+14 close, T+28 close, 同期申万 L1 指数涨跌, 同期上证综指涨跌.

**需要确认**: emox 你环境里有没有装过 akshare? 没有的话 `pip install akshare` 走起 (但要 ~50MB 依赖, 别介意).

### Q4. publish_date 怎么定?

历史 narrative 的 "T+0" 是 narrative 第一次明显发酵的交易日. 怎么算:

- **方法 1 — 看新闻 publish_date**: 财联社/新浪 push 的时间, 但有时新闻是周末发, T+0 取下个交易日.
- **方法 2 — 看异动**: 当天 ticker 池里至少 2 个核心 ticker 有明显放量 (>过去 20 日均量 1.5 倍) 且涨幅 >2%, 取这个交易日作 T+0.
- **混合**: publish_date 优先, 如果发布日不是交易日就顺延; 如果发布日 ticker 没异动 (说明市场不知道), 再顺延 1-2 日.

历史复盘相对宽松, 因为是回测不是预测, 取**新闻公开日 + 1 个交易日**作为 T+0 是 OK 的 (这模拟 "看到新闻第二天买入").

### Q5. 涨幅 hit_strict 阈值跟 W22 一致吗?

W22 现在的 hit / hit_strict 阈值 (在 narrative_track.py 里):
- `hit` = T+14 ticker 涨幅 vs 大盘 excess > 阈值 (具体阈值待查)
- `hit_strict` = `hit` AND ticker vs 板块 excess > 阈值

历史回测应当用**完全一致**的阈值, 否则不能跨时段比. 后面执行时确认下 narrative_track.py 里的阈值定义.

## 二、SOP — 单条 event 构造步骤

每个 event ~30-45 分钟, 30 events 总计 15-22 小时 (分批做, 一批 5-10 条).

```
1. 选定 narrative 主题 + 时间点
   └─ 输入: anchor 节点 (e.g. "2025-08-15 DeepSeek R1 发布")
   └─ 决定: subdomain (复用现有 28 个) + track

2. 验证 publish_date
   └─ search_baidu "DeepSeek R1 发布 时间" → 找原始公告/官方 PR
   └─ fetch_url 拿到 publish_datetime
   └─ 如果在非交易日 → trade_date = 下一个交易日

3. 写 event 元数据
   └─ title: 原新闻标题
   └─ score: 0-3 (按 W22 标准: 0 噪音 / 1 常规 / 2 叙事级 / 3 罕见重磅)
   └─ rationale: 1 行打分理由
   └─ thesis_seed: 受益方草图 (单位经济变化)

4. 选 tickers
   └─ 从 universe.yaml 该 subdomain 拉 5-8 个核心 ticker
   └─ 每个 ticker 标 side (+/-) + 在 ticker_layer.yaml 里查 layer
   └─ 如有 layer 缺失, 此时补上

5. 拉行情数据 (akshare)
   └─ T+0, T+14, T+28 ticker 收盘价
   └─ 同期 申万 L1 指数 + 上证综指 涨跌幅
   └─ 计算 abs_pct / excess_vs_market / excess_vs_sector / hit / hit_strict

6. append 到 narrative_events.jsonl + narrative_perf.jsonl
   └─ 用单独的 backfill 脚本, 不走 cmd_add (避免 trade_date 检查冲突)

7. 人工打 8 维 narrative 特征
   └─ 用 narrative_label_event.py
   └─ ⚠️ 反思: 是否被事后涨幅影响判断? 假装当时不知道结果

8. 最后批量跑 narrative_radar_review.py 验证 v4 hypothesis
```

## 三、产出物清单

执行完后预期产出:
- `historical_events_anchors.md` — 选中的 5-10 个 anchor narrative 节点 + 决策依据
- `historical_events_*.jsonl` — 历史 events (单独文件, 不污染 narrative_events.jsonl)
- `historical_perf_*.jsonl` — 对应 perf 数据 (同上)
- 一个**合并视图** — 让 review 脚本能同时读 W22 + 历史, 跑 cross-period cross-tab
- `narrative_radar_attribution_2026W22_v5.md` — 含历史回测结论的 v5 报告

## 四、执行节奏建议

第 1 批 (验证可行性): **5 events 试跑**, 跨 1-2 个 anchor 节点. 跑完后决定是否扩规模.
第 2 批: 10 events.
第 3 批: 10 events.
第 4 批 (可选): 5-15 events 补长尾.

每批之间 emox 审一次中间结果, 看 v4 模型在历史样本上的单调性是否仍成立, 决定下一步.

## 五、立即下一步 (待 emox 确认 Q1-Q5)

1. emox 回答 Q1-Q5 (重点: 时段选择 + 候选清单方法 + akshare 是否可用)
2. agent 写 anchor 节点候选清单 (mix 方法, 含 emox 提供的 + agent 补充)
3. emox 审 anchor → 选定 5 个起步
4. agent 试跑 5 events 走完 SOP
5. emox 审第一批结果, 决定下一步
