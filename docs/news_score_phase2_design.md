# news_score Phase 2 设计

**日期**: 2026-05-25
**前置**: Phase 1 (z_repur) 已上线，IC_20d=+0.334，增量贡献 +24.6%。

## 目标

在 Phase 1 单信号 (回购) 基础上，加 1-2 个**互补信号**进一步提升 news_score 的 fwd_20d IC。
满分 15 分不变，新信号纳入 base+swing 框架，单信号 swing ≤ ±2，避免单源主导。

## 候选维度三选一/二

| 维度 | 数据源 | 假设 | 实现成本 | 期望 IC | 优先级 |
|---|---|---|---|---|---|
| **A. 解禁压力** | tushare `share_float` | 30D 内解禁占比高 → 抛压 → fwd_20d 跌 | 低 (1d) | +0.10~+0.20 (经典反指) | ⭐⭐⭐ |
| **B. 大宗交易折溢价** | tushare `block_trade` | 折价大宗 → 机构清仓; 溢价 → 产业资本接盘 | 中 (2d) | +0.05~+0.15 | ⭐⭐ |
| **C. 公告 NLP** | tushare `anns_d` 标题 | 增持/业绩预增 → 利好; 减持/诉讼 → 利空 | 高 (4-5d) | +0.05 (标题级噪声大) | ⭐ |

**推荐**：先实现 A，回测验证 IC 后决定 B 是否上。C 不推（成本高且公告标题级 NLP 噪声大）。

---

## 候选 A: 解禁压力 (Unlock Pressure)

### 信号定义

```
unlock_30d_ratio(concept, t) =
    Σ stock_i in concept_i_constituents:
        Σ row in share_float(stock_i):
            row.float_ratio  if t ≤ row.float_date ≤ t+30
        weighted by stock market cap (or equal-weighted v0)
```

`float_ratio` 是该笔解禁占总流通股本的百分比；按 concept 内股票求和（同股多笔分批解禁求和）。

### 标准化 + 打分

```
z_unlock = (unlock_30d_ratio - mean_60d) / std_60d  # 60d rolling z
```

历史标准化：每个 concept 自己的 60d rolling baseline（不是 cross-section z），因为解禁是个股事件，跨板块差异大。

```python
news_pts_unlock = clip(-z_unlock, -2, +2) * 1.0  # 反指 → 取负, swing ±1.0
```

负号：解禁压力越大 → 信号越凶 → 罚分。
swing ±1.0 (满分 15 中占 ~13%)，配合 z_repur 的 ±1.5 → 两者总 swing ±2.5，留出空间给 B。

### 实现步骤

1. 新建 `news_score_phase2_unlock.py`：
   - `fetch_share_float(stock_list, start_date, end_date)`：调 tushare，cache 到 `.cache/news_share_float.jsonl`
   - `compute_concept_unlock(concept, t)`：取 concept 内 stocks → 加总未来 30d float_ratio
   - `z_unlock_60d(concept, t)`：rolling z-score
2. 在 `news_score_phase1.py` (重命名为 `news_score.py`) 里 wire 进来。
3. 回测：`backtest_news_phase1.py` 改造支持 phase2，跑 6 个月 panel。
4. 上线门槛同 Phase 1：单变量 IC_20d > 0.10，增量 IC > +5%。

### 风险点

- **样本稀疏**：单股每月最多 1-2 次解禁，concept 维度可能多月 0 信号
- **解决**：用 unlock_60d_pct (60d 累计) 平滑，z 用 250d baseline；或承认稀疏，只在有信号月加权
- **大解禁集中失真**：例如 2026 年 4 月寒武纪 1 笔解禁 0.24% 占了全 concept 大头，单股事件可能放大噪声
- **解决**：cap 单股贡献上限 (如 50%)

---

## 候选 B: 大宗交易折溢价 (Block Trade Premium)

### 信号定义

```
block_premium_5d(concept, t) =
    Σ block_trade in last 5 days for stocks in concept:
        (trade_price - close_price_on_trade_date) / close_price * 100
        weighted by trade_amount
```

> 0：溢价（产业资本接盘看好）；< 0：折价（机构出货）

历史经验：折价 -3% 以上是清晰的抛压信号，溢价 +3% 以上是积极信号。

### 实现步骤

1. `fetch_block_trade(stock_list, start, end)`
2. join close price 算折溢价
3. concept 维度 amount 加权
4. 60d z-score → swing ±1.0

### 风险点

- **数据稀疏**：日成交大宗少，5d 窗口可能 0 笔
- **解决**：扩到 10d/20d，或承认稀疏

---

## 候选 C: 公告 NLP — 暂缓

`anns_d` API 只有 ann_date / title / url，全文需要爬 cninfo。标题级 NLP 噪声极大（同一公司可能日发 10 条公告，多数是合规性质），且基础 NLP（关键词、向量）的 ROI 远不如直接用 share_float / block_trade 这种结构化数据。

**何时再考虑**：Phase 2 A+B 上线后，看 fwd_20d IC 是否还有 +0.05 以上提升空间，再投入 NLP 工程。

---

## Phase 2 上线后的 news_score 公式 (假设 A+B 都通过)

```
news_score (15 满分) =
    base 7.5
    + z_repur_clip2 * 1.5      # Phase 1, swing ±3
    + (-z_unlock_clip2) * 1.0  # Phase 2 A, swing ±2
    + z_block_clip2 * 1.0      # Phase 2 B, swing ±2
```

总 swing 范围 [-7.0, +7.0]，叠加 base 后 [0.5, 14.5]，几乎用满 15 分区间，且单信号最大不超过 ±3，避免单点失效拖整体。

---

## 验收标准

| 指标 | Phase 1 baseline | Phase 2 目标 |
|---|---|---|
| 单变量 IC_20d (z_unlock) | — | > +0.10 |
| 单变量 IC_20d (z_block) | — | > +0.05 |
| news_score 整体 IC_20d | +0.334 | > +0.40 |
| 增量 IC vs fund_score 单 | +24.6% | > +30% |
| 月度 IC 反指月份占比 | 1/4 | ≤ 1/4 |

任一不达标，对应信号回滚。

---

## 实施记录

### 2026-05-25 — Phase 2 A (z_unlock) 验证: ❌ REJECT

实施: `news_score_phase2_unlock.py` + `backtest_news_phase2_unlock.py`

回测设置:
- 11 BK-mapped concepts × eval window 20251123 ~ 20260502
- panel n=1118 (fwd_5d), n=1041 (fwd_20d)
- z_unlock 计算: future-30d 累计 float_ratio per stock (cap 50%) → concept 加总 → 60d rolling z

**结果**:

| 子集 | n | fwd_5d IC | fwd_20d IC |
|---|---|---|---|
| 全 panel | 1118/1041 | -0.073 | **+0.174** ⚠️ |
| `|z|>0.5` active | 317/291 | -0.250 | +0.208 |
| `z>1.0` high-press | 144/144 | -0.339 | -0.494 |

**分组均值（fwd_20d）**: high-press +14.45% vs others +6.29% → **gap +8.16pp UPSIDE**（与"解禁压力 → 抛压"假设相反）

月度稳定性 (fwd_20d):
- 6 个月 IC: 202511 +0.04, 202512 +0.02, 202601 -0.10, 202602 -0.11, 202603 +0.22, 202604 -0.17
- 反指失效月（IC > 0）= **3/6 (50%)**，远超 ≤25% 门槛

增量 IC vs fund_score (各 threshold gate, -2 pts 罚分):
- fwd_5d: Δ ∈ [+1.0%, +1.6%] (低于 +5% 门槛)
- fwd_20d: Δ ∈ [-3.2%, **-5.5%**] (NEGATIVE — 信号反而损害组合)

**根因诊断**:
1. 全 panel sign flipped (+0.174) — 11 BK-mapped concept 集中于 AI/算力, 高解禁压力 = 高 IPO 池 = 2025-Q4 bull run beneficiary, sector beta 完全淹没解禁信号
2. high-press subset 内 IC=-0.494 是真信号，但样本只有 144 obs，且需要 within-group rank 才显化
3. 我们 41 concept 池系统性偏 large-cap leader，跨板块 cross-section 极稀疏（多数 concept 月度 0 解禁事件）

**结论**: 设计假设 `pts = clip(-z, -2, +2) * 1.0` 与 BK-mapped panel 上的 6mo 数据**符号相反**。即使做 threshold gate 也无法挽救增量 IC。

**下一步**: Pivot to **Phase 2 B (大宗交易折溢价)**。z_unlock 模块（`news_score_phase2_unlock.py` + 215k 行 cache）保留备查，但不接入 news_score 主线。

⚠️ 反向假设留待后查: "高解禁前 pump（市值管理 / 业绩催化）" 是否一个独立的 long 信号？— 需要 cross-section 更广的 universe 才能证伪。

---

### 2026-05-25 — Phase 2 B (z_block) 验证: ❌ REJECT (for news_score) / ⚠️ Maybe-Useful (短线)

实施: `news_score_phase2_block.py` + `backtest_news_phase2_block.py`

数据特性 (探查 20 stocks → 219 全量):
- 总 block_trade rows: 44,268 (215 stocks with data, 4 HK 无)
- close-price 覆盖: 24% (daily.parquet 历史不全, 但绝对量 20,387 trades 仍够测)
- premium 分布 (非零 sample n=588):
  - median **-7.77%** (深折价为常态)
  - 折价 (>1%) : 溢价 (>1%) = **541 : 16** = 97% : 3%
  - p10 = -17.27%, p90 = -1.11%

→ A股大宗交易 baseline 是 5-10% 折价 ("流动性税"). Signal = "比 baseline 更折/更轻".

回测设置:
- 11 BK-mapped concepts × eval window 20251123 ~ 20260502
- panel n=1118 (with z_block: 681 — 60% 覆盖, sparsity 来自 60d hist 不足)
- window_days = 20 (扩 from 5 due to sparsity)

**结果**:

| 子集 | n | fwd_5d IC | fwd_20d IC |
|---|---|---|---|
| 全 panel (with z) | 681/639 | **+0.123** ✅ | +0.027 ❌ |
| `|z|>0.5` active | 491/458 | +0.169 | +0.068 |
| `z > +1.0` (溢价群) | 149/127 | mean +2.94% (gap +0.76pp) | mean +9.20% (gap +0.35pp) |
| `z < -1.0` (折价群) | 144/144 | mean +0.99% (gap -1.19pp) | mean +7.50% (gap -1.36pp) |

月度稳定性 (fwd_20d):
- 6 个月 IC: 202511 -0.74, 202512 -0.24, 202601 -0.11, 202602 +0.32, 202603 +0.11, 202604 -0.21
- mean -0.147, std 0.362
- 反指失效月（IC < 0）= **4/6 (67%)** ❌ (远超 ≤25% 门槛)

增量 IC vs fund:
- fwd_5d: +7.3% ✅ (但 fwd_5d 不是 news_score 主目标 horizon)
- fwd_20d: +3.1% ❌ (低于 +5%)

**根因诊断**:
1. block_trade 信号偏短期 — 折价交易那天的卖压在 5d 内体现, 20d 内被新流入抹平
2. 月度方向极不稳定 (4/6 反向), std=0.36 — 信号无 persistence
3. 概念-aggregation 损失了股票级别的事件信号方向

**决定**:
- ❌ **不接入 news_score** (fwd_20d 不达标 + 月度不稳定)
- ⚠️ **保留模块, 留待 sector_picks/signal_collector 短线使用** — fwd_5d IC=+0.123 + 增量 +7.3% 在短线层有潜在价值, 但需要单独的 stock-level 验证

---

## Phase 2 总结 (2026-05-25)

**两个候选维度均 fail news_score 的 fwd_20d 上线门槛**:
- A (z_unlock): 全 panel sign flipped, 增量 IC -5%, 月度反指 3/6
- B (z_block):  fwd_20d IC +0.027, 月度反指 4/6

**根因**: 我们 11 BK-mapped concepts 集中于 AI/算力, 2025-Q4~2026-Q2 是 sector-wide bull, **板块 beta 完全淹没了 stock-level 的事件信号**. Phase 1 z_repur 之所以 work 是因为**回购公告本身就有 cross-stock cross-board 的 cross-sectional 差异**, 而 unlock/block 在 AI 主题这 11 个 concept 内同质化太高.

**下一步选项 (待用户决定)**:
1. **Phase 2 暂停接入 news_score**, 保留 phase1_v1 (单 z_repur) 现状, news_score IC_20d 维持 +0.334
2. **回到 sector_score 主线**, 推进 dup_group 排序 / z_main 末期重构 / Tier 0 优化
3. **扩大 mapped concepts universe** (不止 11 个 BK), 让 Phase 2 信号有 cross-section 变异空间 — 但这是大工程
4. **将 z_block 转向 sector_picks 短线层** (fwd_5d 显著)

模块保留:
- `news_score_phase2_unlock.py` (working, but disabled)
- `news_score_phase2_block.py` (working, but disabled for news_score; 可移植)
- `backtest_news_phase2_unlock.py` / `backtest_news_phase2_block.py` (回测脚本可复用)

---

## 2026-05-25 — 用户决策: 走 1 + 4 路径 ✅

**确认终态**:
- ✅ **选项 1 (锁 phase1_v1)**: news_score 终态 = phase1_v1 单一 z_repur 信号
  - IC_20d=+0.334, 增量贡献 +24.6% (vs fund_score 单)
  - swing ±1.5, 公式 `7.5 + clip(z_repur, -2, +2) * 1.5` 不再调整
  - phase2_unlock / phase2_block 模块保留备查, 永不接入 sector_score.news_score
- ✅ **选项 4 (z_block → sector_picks 短线层)**: T_window=20d, horizon=5d
  - 已迁移到 `sector_picks_block_trade.py` (P0-2 任务)
  - 与 news_score 完全解耦, 不影响 fund/news/tech 综合分

**已拒绝**:
- ❌ 选项 3 (扩 BK universe): 高风险大工程, 不投入
- ❌ Phase 2 第 3 个候选 (公告 NLP): ROI 不足

**后续计划**:
- P1: dup_group 排序接入 sector_picks (3h)
- P2: cron 输出整体审计 (检查 6 个 cron 是否还在用 v2 ETF flow_5d_cny 残值)
