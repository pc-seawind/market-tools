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
| `policy.sh` | bash + python3 stdlib + coreutils `date` + `TUSHARE_TOKEN` |
| `tushare.py` | python3 stdlib + `TUSHARE_TOKEN` |

Nothing to `pip install`. Register at <https://tushare.pro> for a free token.
APIs confirmed working on the default free+积分 tier:
`daily`, `stock_basic`, `daily_basic`, `hk_daily`, `hk_basic`, `index_daily`,
`index_classify`, `index_member`, `sw_daily`, `cctv_news`, `fina_indicator`,
`forecast`.

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
