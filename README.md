# market-tools

Lean market-data utility scripts the `investment` (and other finance-adjacent)
homespace domains import as artifacts.

Three tiers:

- **Zero-dep tier** (`quote.sh`) — bash + curl only, free public APIs, no auth.
  Safe to import anywhere without setup cost.
- **Data tier** (`tushare.py`, `history.sh`) — historical OHLCV + raw tushare
  Pro API access. Requires `TUSHARE_TOKEN`. python3 stdlib only.
- **Analysis tier** (`fundamentals.sh`, `policy.sh`) — higher-level composites
  that pull multiple endpoints and render a single readable report.

For news / research context that tushare doesn't cover (公告, 研报, 实时新闻),
combine with the homespace domain's `search_baidu` / `search_tavily` /
`fetch_url` tools — that's the full 消息面 pipeline.

## Scripts

### `concepts.sh [--topic=X | --compare-top=N | --list]` — 🧭 主题概念板块热度

**解决的问题**: 申万 L1 (31 行业) 和 L3 (~200 细分) 都是"传统工业分类",
抓不到"存储 / CoWoS / HBM / 算力租赁 / 英伟达链 / 人形机器人"这些真正
的 **市场主题概念** (一个主题可能跨多个行业). tushare 的概念指数 API
(ths_index / dc_index / kpl_concept) 都需付费权限.

**解决方案**: 手工维护概念池 (`concepts_data.py`, 14 个热点主题 × 5-10
只龙头) + 已有免费 `daily trade_date=` 批量 API 做本地聚合. 只需 3 API
calls (全市场 3 个日期), <15s 出热度排名.

三种 mode:

```bash
# 1) 默认: 全部 14 概念热度排名, 按 1M 均涨序
$ ./concepts.sh
   🔥 1. 光通信 (光模块/CPO)    +52.8%   Top1: 盛科通信 (+89.7%)
   🔥 2. AI芯片 (算力核心)      +43.0%   Top1: 寒武纪   (+82.7%)
   🔥 3. 存储芯片 (HBM/DDR)    +40.4%   Top1: 江波龙   (+66.3%)
   ...
   🧊14. 白酒 (消费龙头)        -5.6%   Top1: 酒鬼酒  ( -0.7%)

# 2) 看某概念明细 (支持模糊匹配)
$ ./concepts.sh --topic=存储
  江波龙 +66.3% | 佰维存储 +47.6% | 东芯股份 +45.3% | ...
  1M 均涨 +40.4% | 总成交 630 亿
  💡 自动对比 top 3: bash compare.sh sz301308 sh688525 sh688110

# 3) 自动挑 top N 概念龙头 → compare.sh 横向比较
$ ./concepts.sh --compare-top=4
  已选: 盛科通信 / 寒武纪 / 江波龙 / 生益科技
  🚀 调用 compare.sh ... (输出 4 维度对比表)

# 4) 列出全部可用概念
$ ./concepts.sh --list
```

**维护概念池**: 编辑 `concepts_data.py` 的 CONCEPTS dict, 每 1-3 个月 review
一次. 当市场出现新主题时添加一个 entry (概念名 + 5-8 只龙头股).

---

### `compare.sh <ticker1> <ticker2> [... up to 5]` — ⚖️ 多股横向对比

投研里最常见的动作是 "A 和 B 哪个值得买"。compare 输出 4 维度 side-by-side
对比表, 一眼能看出谁占优:

- **§1 估值对比** — 收盘/PE_TTM/PB/PS/股息率/换手率/总市值
- **§2 业绩对比** — 最新季度 ROE/ROA/营收YoY/净利YoY/毛利净利率/负债率
- **§3 量价对比** — 120 天 [位置/回撤/波动率/1W 1M 3M 收益]
- **§4 机构动作对比** — 北向/公募/外资 QoQ Δpp + 最新持股比例

```bash
$ ./compare.sh sh688256 sh688041 sh688981    # 寒武纪 / 海光 / 中芯
$ ./compare.sh sz300308 sz300502             # 中际旭创 vs 新易盛 (光模块双龙)
$ ./compare.sh sh600519 sz000858 sz000568    # 茅五泸三巨头
```

A 股专用 (sh/sz/bj prefix); 每只股 ~5 API calls, 2 只 ~10s, 5 只 ~30s.

最佳搭档是 `screen.sh` — 先筛出 top N 候选, 再对其中最有价值的 2-3 只
做精确对比:
```bash
# 1. 筛出低估高股息大盘股
$ ./screen.sh --pe-max=15 --dv-min=3 --mv-min=500 --top=20

# 2. 对前 3 名做精确对比
$ ./compare.sh sh601838 sh601665 sz002142   # 3 家银行横向比
```

---

### `screen.sh [filters...] [--top=N] [--sort=KEY]` — 🔍 条件筛股 (discovery)

market-tools 其他工具都是"ticker → 分析"反向流程; `screen` 填补
"条件 → 候选"的 discovery gap。输入 filter 组合, 输出 ranked 候选清单。

**完整工作流** (discovery → deep dive):
```
条件 ──→ screen.sh ──→ top N 候选 ──→ diligence.sh <each> ──→ 决策
```

Stage 1 filters (估值 / 市值 / 流动性 / 动能):
```
--pe-max=N / --pe-min=N    PE_TTM 上下限
--pb-max=N / --ps-max=N    PB, PS_TTM 上限
--dv-min=N                 股息率下限 (%)
--mv-min=N / --mv-max=N    总市值上下限 (亿)
--amt-min=N / --tor-min=N  日成交/换手率下限 (亿, %)
--r1w-min=N / --r1w-max=N  1 周涨幅上下限 (%)
--r1m-min=N / --r1m-max=N  1 月涨幅上下限 (%)
--r3m-min=N / --r3m-max=N  3 月涨幅上下限 (%)
--exclude-st               排除 ST/退市股
--market=sh|sz|bj|all      市场过滤 (默认 all)
--top=N                    返回前 N (默认 20)
--sort=KEY                 r1w|r1m|r3m|pe|pb|dv|mv|amt (默认 r1m desc,
                           前缀 - 表升序)
```

三类经典用法:
```bash
# 低估红利 (防御, 稳健仓): PE<15 + 股息>3% + 市值>200亿
$ ./screen.sh --pe-max=15 --dv-min=3 --mv-min=200 --sort=dv

# 成长爆发 (进攻): 1M涨>+20% + 市值>500亿 + 日成交>10亿
$ ./screen.sh --r1m-min=20 --mv-min=500 --amt-min=10 --top=30

# 超跌反弹 (逆向): 3M跌>15% + 本周涨>3% + 市值>100亿 + 排除ST
$ ./screen.sh --r3m-max=-15 --r1w-min=3 --mv-min=100 --exclude-st
```

Runtime: ~5 API calls, <15s (全市场批量, 不存在 rate-limit 风险)。

Note: Stage 2 (ROE / 营收YoY / 北向持股) 未实现 — 对 top 候选跑
`diligence.sh` 就能补上这些维度。

---

### `diligence.sh <ticker> [quarters=6]` — 🎯 ONE-SHOT 六维度综合报告

**最重要的入口命令。** 运行 `quote` + `history-stats` + `fundamentals` +
`flows` + `policy` 于一次调用,输出统一报告,末尾给出预填充的搜索查询
供 agent 接续执行消息面 / 跨市场维度。

```bash
$ ./diligence.sh sz300308        # 中际旭创 full report (~3-4 min)
$ ./diligence.sh hk00700         # 腾讯 (无基本面/资金, §1+§4+§5)
$ ./diligence.sh NVDA            # NVIDIA (snapshot only, §1+§5)
```

输出结构:
- **§1 量价技术面** — quote + 120 天 [位置/回撤/波动率/1W/1M/3M/6M 收益]
- **§2 基本面** — 估值 + 最近 N 期业绩 + 业绩预告 (A 股)
- **§3 机构资金** — 前十大流通股东 QoQ + 类别聚合 + 北向资金 (A 股)
- **§4 政策面** — CCTV 联播 7 天关键词过滤
- **§5 接续步骤** — 预填 search_baidu / search_tavily / fetch_url 查询

替代手工多次调用。推荐作为"XYZ 股能不能买"类问题的起点。

---

### `quote.sh <ticker>` — snapshot quote

Price, open/high/low, volume, timestamp. **Auto-routes by ticker shape:**

| Shape | Source | Example |
|-------|--------|---------|
| `sh######` / `sz######` | Sina Finance (A-shares) | `sh600519` (贵州茅台) |
| `hk######` | Sina Finance (Hong Kong) | `hk00700` (腾讯) |
| `bj######` | Sina Finance (Beijing STAR) | `bj831168` |
| `AAPL` / `TSLA` | Stooq.com (US) | `AAPL`, `tsla.us` |

Output: one human-readable summary line + one raw upstream line for parsing.

```bash
$ ./quote.sh sh600519
sh600519 (贵州茅台) | price=1384.79 open=1400 prev_close=1401.17 ... ts=2026-04-30 15:00:00
var hq_str_sh600519="贵州茅台,1400.000,1401.170,1384.790,...
```

### `history.sh <ticker> [days=60] [adj=none]` — daily OHLCV history

Tushare-backed. Returns CSV, newest row first.

| Ticker shape | Tushare API | ts_code format |
|--------------|-------------|----------------|
| `sh######` / `sz######` | `daily` | `600519.SH` |
| `hk######` | `hk_daily` | `00700.HK` |
| `bj######` | `daily` | `831168.BJ` (token-tier dependent) |

```bash
$ ./history.sh sh600519 days=10
$ ./history.sh hk00700  days=120     # Tencent, last ~120 trading days
```

### `fundamentals.sh <ticker> [quarters=8]` — A-share 业绩+估值+预告 三合一

One-stop report for A-share fundamental diligence. Pulls three tushare
endpoints and renders them inline:

1. **估值快照** — PE_TTM / PB / PS / 股息率 / 换手率 / 总市值 / 流通市值
2. **最近 N 期财务指标** — ROE / ROA / 营收YoY / 净利YoY / 毛利率 / 净利率 /
   资产负债率 (from `fina_indicator`)
3. **业绩预告** — 最近 5 条 forecast（扭亏/续盈/增减幅 + 摘要）

```bash
$ ./fundamentals.sh sh688256     # 寒武纪完整基本面
$ ./fundamentals.sh sz000858 quarters=12
```

A-shares only (tushare free tier doesn't cover HK/US for `fina_indicator`).
For HK fundamentals, fall back to `tushare.py hk_daily_basic …` manually
or use external sources.

### `flows.sh <ticker> [quarters=4]` — 机构资金动作分析

组合 `top10_floatholders` (前十大流通股东季度归档) + `moneyflow_hsgt`
(北向资金大盘环境) 两个 tushare API,输出机构资金行为的立体画面:

1. **前十大流通股东 QoQ** — 每季度明细 + 持股比例变化
2. **类别聚合变化** — 按 北向 / 公募 / 外资 / 险资 / 社保 / 产业资本 /
   自然人 分组,计算每一类的 QoQ 持股比例变化
3. **最近 20 日北向资金总览** — 市场环境指标

核心价值: 机构季度持股变化**往往先于消息面出现**。当"抱团瓦解"成为
热门话题时,数据上往往已经持续了 1-2 个季度。

```bash
$ ./flows.sh sz300308 quarters=4        # 中际旭创 — 看公募抱团是否松动
$ ./flows.sh sh600519 quarters=6        # 茅台 — 跟踪险资/社保长期持有者
```

Example output (中际旭创,最后一段):
```
💡 速读信号:
   · 北向 **加仓** +3.26pp ✅
   · 公募 **减仓** -2.81pp ⚠️
   · Top10 合计: 总持股 基本持平 (-0.08pp)
```

A 股 sh/sz/bj only (top10_floatholders 是 A 股专属 API)。

### `policy.sh [days=7] [--grep=...] [--all]` — CCTV 新闻联播 政策信号扫描

联播中出现的产业/技术/政策方向几乎 100% 会成为后续资金主线或监管重点，
是成本最低的"官方信号"来源。

```bash
# 过去 7 天，默认过滤器（strip 外交/慰问/天气等噪音）
$ ./policy.sh

# 过去 30 天，只看 AI / 半导体 / 基础研究相关
$ ./policy.sh days=30 --grep='半导体|AI|算力|芯片|基础研究|科技'

# 过去 3 天，全部标题（不过滤）
$ ./policy.sh days=3 --all
```

Example output (本周最关键的结构信号):
```
[2026-04-30]
  · 习近平在加强基础研究座谈会上强调 以更大力度更实举措加强基础研究...
[2026-05-02]
  · 人民日报评论员文章：抓住机遇，以更大力度更实举措加强基础研究...
[2026-05-04]
  · 【大国工匠】梁凌宇：为电网铸造"AI底座"
```

Rate-limited endpoint (cctv_news is a few calls/min on free tier); the
underlying `tushare.py` handles 40203 automatically with ~35s backoff.

### `tushare.py <api_name> [k=v ...] [--fields=...] [--csv]` — generic REST wrapper

Escape hatch: any [tushare Pro API endpoint](https://tushare.pro/document/2)
can be called directly. Stdlib-only, no `pip install tushare` needed.

**Built-in robustness:**
- Bypasses env-level proxy by default (stale SOCKS/HTTP proxies were breaking
  calls). Opt out with `TUSHARE_USE_ENV_PROXY=1`.
- Auto-retries on rate-limit (code 40203) up to 3× with 35/45/55s backoff.
  Opt out with `TUSHARE_NO_RETRY=1`.

```bash
# Index history (CSI 300)
$ ./tushare.py index_daily ts_code=000300.SH start_date=20260101 \
       --fields=trade_date,close,vol --csv

# Industry / sector index daily (申万 L1/L2)
$ ./tushare.py sw_daily ts_code=801080.SI start_date=20260101 \
       --fields=trade_date,close,pct_change --csv

# Industry constituents (申万成分股)
$ ./tushare.py index_member index_code=801081.SI \
       --fields=con_code,con_name,in_date,out_date --csv

# CCTV 新闻联播 (policy signals)
$ ./tushare.py cctv_news date=20260430 --fields=date,title --csv
```

Default output: pretty-printed JSON of the full response. With `--csv`,
just the data rows with a header line.

## Dependencies

| Script | Needs |
|--------|-------|
| `quote.sh` | bash + curl + (optional) iconv |
| `history.sh` | bash + python3 stdlib + coreutils `date` + `TUSHARE_TOKEN` |
| `fundamentals.sh` | bash + python3 stdlib + `TUSHARE_TOKEN` |
| `flows.sh` | bash + python3 stdlib + coreutils `date` + `TUSHARE_TOKEN` |
| `policy.sh` | bash + python3 stdlib + coreutils `date` + `TUSHARE_TOKEN` |
| `screen.sh` | bash + python3 stdlib + `TUSHARE_TOKEN` |
| `compare.sh` | bash + python3 stdlib + `TUSHARE_TOKEN` |
| `concepts.sh` | bash + python3 stdlib + `concepts_data.py` + `TUSHARE_TOKEN` |
| `diligence.sh` | all of the above (pure wrapper, adds no new deps) |
| `tushare.py` | python3 stdlib + `TUSHARE_TOKEN` |

Nothing to `pip install`. Register at <https://tushare.pro> for a free token.
APIs confirmed working on the default free+积分 tier:
`daily`, `stock_basic`, `daily_basic`, `hk_daily`, `hk_basic`, `index_daily`,
`index_classify`, `index_member`, `sw_daily`, `cctv_news`, `fina_indicator`,
`forecast`, `top10_holders`, `top10_floatholders`, `moneyflow_hsgt`,
`top_list`, `trade_cal`.

APIs that require higher tiers (will error with 40203/40202):
`anns_d` (公司公告), `news` (通用新闻), `us_basic` / `us_daily` (美股).
For 公告 & 实时新闻 use `search_baidu` + `fetch_url` from the gateway toolset.

## Future

- `adj=qfq|hfq` in `history.sh` (merge `daily` + `adj_factor`)
- Cross-exchange name resolver ("moutai" / "茅台" → `sh600519`)
- FX rates (USD/CNY, USD/HKD) — add a `fx.sh` sibling script
- `diligence.sh <ticker>` — unified one-shot wrapper that runs
  quote + history + fundamentals + policy + news-search in one go
- A wrapping MCP server for typed I/O (deferred until a consumer needs it)
