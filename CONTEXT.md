# market-tools/ Project · CONTEXT

> 读此文件的人：Claude / Codex / 任何在 `~/work/projects/market-tools/` 工作的 AI agent
> 用途：信号系统 + 工具链的共享语言表。和 `~/work/investment/CONTEXT.md` 构成两套姊妹文档（一个管 thesis / 中期逻辑，一个管 signal / 日级技术）。

---

## 是什么

个人 A 股量化信号工具链。**不控盘、不发 P&L 告警**，只给**市场层面的技术信号**（BUY/SELL 判断基于量价 + 位置，和用户成本无关）。所有工具读 Tushare + Parquet 缓存，输出纯文本/markdown 给上游 cron 或 agent 消费。

---

## 核心文件

```
market-tools/
├── CONTEXT.md                ← 本文档
├── watchlist.yaml            ← 关注清单（人工 + cron 提议 + 审批）
├── watchlist_data.py         ← watchlist 读取助手
├── watchlist_ops.py          ← apply_proposal 等修改操作
├── watchlist_changes.jsonl   ← 变更日志
├── watchlist/proposed/       ← cron 生成的待审提议
│
├── daily.sh                  ← 日级简报 (6 段式)，413 行核心
├── concepts.sh               ← 14 主题轮动分析
├── funnel.sh                 ← 候选池筛选 (A/B/C/D 分级)
├── momentum.sh               ← 动量 / 反向 (contrarian) 筛选
├── compare.sh                ← 个股对标分析
├── diligence.sh              ← 深度尽调
├── history.sh                ← 历史行情
├── quote.sh                  ← 报价
├── fundamentals.sh           ← 基本面
├── flows.sh                  ← 资金流
├── policy.sh                 ← 政策扫描
├── screen.sh                 ← 筛选器
├── backtest.sh               ← 信号回测
│
├── grading.py                ← 统一打分引擎 (A/B/C/D)
├── concepts_data.py          ← 主题数据
├── sector_health.py          ← 板块健康度
├── fina_sync.py              ← 财务数据同步
├── cache_parquet.py          ← Parquet 缓存管理
├── akshare_fallback.py       ← akshare 备份抓取
└── homespace.yaml            ← worker 配置（如何被 homespace cron 调起）
```

---

## 🗣️ Jargon 表

### Signal 信号（daily.sh §1-§2 产出）

| 信号 | 触发规则 | 解读 |
|---|---|---|
| **SELL_EXTREME** | 1W > +25% + 位置 > 90 + 量比 > 2x | 极度超买；历史上 +20d 下跌样本 6/6 |
| **SELL_CONFIRMED** | 1W > +15% + 位置 > 85 + 量比 > 1.5x | 强烈卖点；准确率高 |
| **SELL_EXHAUSTION** | 1W > +15% + 位置 > 85 (不含量比) | 末期加速，情绪顶 |
| **SELL_TOP** | 1M > +50 + 1W < 0 | 主升浪末端，动能衰竭 |
| **SELL_BREAKDOWN** | 1W < -10 + 量比 < 0.8 | 持续下跌 + 缩量破位 |
| **BUY_EARLY** | 量比 ≥ 2x + 1W ∈ [-3, +5] | 放量企稳，早期吸筹（样本少，谨慎） |
| **BUY_BREAKOUT** | 位置 ≥ 90 + 量比 ≥ 1.5x + 1W ∈ (0, 10) | 放量突破 |
| **BUY_PULLBACK** | 1M ≥ +20 + 1W ∈ (-10, 0) + 量比 < 1 | 强势股健康回调 |
| **TODAY_SURGE** | 当日 +7% 以上 | 当日异动（见下） |
| **TODAY_DROP** | 当日 -7% 以上 | 当日异动（注意：TODAY_DROP 是反弹预示不是逃命信号） |

**重要原则**：信号规则**无个人 cost**，纯技术 + 量价。用户拿到市场判断后结合自己仓位决定操作。

### Grading 分级（funnel/momentum/screen 产出）

统一用 `grading.py` 给候选股打分：

| 级别 | 条件 |
|---|---|
| 🌟 **A** | 板块热 + 跑赢 + 无 SELL 信号 + (可选) BUY 信号 + 基本面健康 |
| ✅ **B** | 板块温和，或弱卖信号，或基本面中性 |
| 👀 **C** | 信号矛盾（板块热但 SELL_CONFIRMED；或个股跑输板块；或 ROE 负） |
| ⚠️ **D** | 严重卖信号 / 板块衰退 / 严重亏损 |

### Watchlist 维度

| 字段 | 含义 |
|---|---|
| **code** | ts_code 格式：`002475.SZ` (A 股) / `00700.HK` (港股补 0) |
| **tier** | 基础仓 / 博弈仓-追涨 / 博弈仓-左侧 / 观察池 |
| **themes** | 列表（新能源车 / AI 芯片 / 光通信 / ...） |
| **added_at** | YYYY-MM-DD |
| **reason** | 一句话关注原因（thesis_statement 的前身） |

### 位置 (position)

**位置 %** = 股价在最近 120 日内的相对高度百分位 (0-100)。**不是仓位**。
- < 30 = 底部区域
- 30-70 = 中性
- > 85 = 高位（SELL 信号触发的一个必要条件）
- > 95 = 历史新高区域

### 美股锚点 (us_anchors)

`watchlist.yaml` 末尾的 `us_anchors` section 是美股核心股票（NVDA/TSM/MSFT/META/GOOGL/MU），仅用于 daily.sh §4 美股隔夜分析，**不参与 signal / grading**。

---

## 🔄 数据流

```
[Tushare API + akshare backup]
         │
         ▼
[fina_sync.py / cache_parquet.py] ← 日级同步 + Parquet 缓存
         │
         ▼
[daily.sh / concepts.sh / funnel.sh / momentum.sh / ...]
         │
         ▼ (纯文本 / markdown 输出)
         │
[homespace cron: morning-brief / evening-recap / sunday-preview / ...]
         │
         ▼ (调 create_doc MCP 归档)
         │
[飞书 domain/investment/{日报,周报,thesis}/]
```

---

## 🔗 和其他项目的关系

| 关系 | 说明 |
|---|---|
| **`~/work/investment/`** | thesis 存这里，消费 market-tools 的 signal 作为日级数据源。thesis-enrich-daily cron 跑 daily.sh 并把该 ticker 的 signal 写进 thesis `update_log.technical_status` 字段 |
| **homespace cron** | 上游调度。所有 `.sh` 工具被 cron prompt 调用 |
| **飞书文档** | 下游归档。不直接 create_doc，是由 cron agent 拿着 .sh 输出 调 `create_doc` MCP |

---

## ⚠️ 陷阱 / 注意事项

1. **TUSHARE_TOKEN** 必需 —— 环境变量从 `~/.openclaw/.env` 或 shell 导出
2. **Parquet 缓存可能过期** —— `cache_parquet.py status` 查最后同步时间，周末是刷新最佳时机
3. **concepts 14 个主题 是固定的** —— 不要擅自改 `concepts_data.py` 的主题列表
4. **A/B/C/D 分级是纯规则** —— 不使用 LLM 判断，grading.py 是可回放的
5. **backtest 有 rate-limit** —— Tushare 免费版每天 500 次请求上限

---

## ⏰ 典型用法

- **日级早盘前瞻**（cron）：`bash daily.sh`
- **周末候选池刷新**：`bash funnel.sh --final=20 && bash momentum.sh --final=15 && bash momentum.sh --preset=contrarian --final=12`
- **某只股深度调研**：`bash diligence.sh 002475.SZ`
- **主题轮动**：`bash concepts.sh`
- **信号回测**：`bash backtest.sh` (慢，Tushare 请求多)
- **watchlist 数据访问**：`python3 -c 'from watchlist_data import entries, groups; print(entries())'`

---

## 何时更新本文件

- 新增 signal 规则必写
- grading.py 权重调整必写
- 新加 `.sh` 工具必加到"核心文件"
- watchlist tier 枚举变化必改
