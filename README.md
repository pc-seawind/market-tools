# market-tools

Lean market-data utility scripts the `investment` (and other finance-adjacent)
homespace domains import as artifacts.

Two tiers:

- **Zero-dep tier** (`quote.sh`) — bash + curl only, free public APIs, no auth.
  Safe to import anywhere without setup cost.
- **Tushare tier** (`tushare.py`, `history.sh`) — historical / fundamental data
  via tushare Pro. Requires `TUSHARE_TOKEN` env var. python3 stdlib only.

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
trade_date,open,high,low,close,vol,amount
20260430,1400.0,1401.17,1380.0,1384.79,52752.67,7316111.748
20260429,1405.0,1409.75,1400.28,1401.17,34813.13,4881691.348
...

$ ./history.sh hk00700 days=120     # Tencent, last ~120 trading days
```

Output fields: `trade_date,open,high,low,close,vol,amount`. Volume is in units
the upstream API uses: A-share `daily` reports 手 (100 shares), HK `hk_daily`
reports shares; `amount` is CNY-k for A-shares, HKD for HK.

Known limitations:
- `adj=qfq|hfq` not yet supported; all prices are unadjusted. Use `tushare.py
  adj_factor ...` manually if you need backward-adjusted series.
- US equities not covered (tushare's US coverage is sparse + paid); stick with
  `quote.sh` + an external history source for those.

### `tushare.py <api_name> [k=v ...] [--fields=...] [--csv]` — generic REST wrapper

Escape hatch: any [tushare Pro API endpoint](https://tushare.pro/document/2)
can be called directly. Stdlib-only, no `pip install tushare` needed.

```bash
# Index history (CSI 300)
$ ./tushare.py index_daily ts_code=000300.SH start_date=20260101 \
       --fields=trade_date,close,vol --csv

# Fundamentals (financial reports — PE, PB, ROE, etc.)
$ ./tushare.py daily_basic ts_code=600519.SH trade_date=20260430 \
       --fields=ts_code,pe,pe_ttm,pb,dv_ratio --csv

# Stock basics
$ ./tushare.py stock_basic exchange=SSE list_status=L \
       --fields=ts_code,name,industry,list_date --csv
```

Default output: pretty-printed JSON of the full response. With `--csv`,
just the data rows with a header line.

## Dependencies

| Script | Needs |
|--------|-------|
| `quote.sh` | bash + curl + (optional) iconv |
| `history.sh` | bash + python3 stdlib + coreutils `date` + `TUSHARE_TOKEN` |
| `tushare.py` | python3 stdlib + `TUSHARE_TOKEN` |

Nothing to `pip install`. Register at <https://tushare.pro> for a free token
(most basic APIs — `daily`, `stock_basic`, `daily_basic`, `hk_daily`,
`index_daily` — are available on the free tier with 积分 floor).

## Future

- `adj=qfq|hfq` in `history.sh` (merge `daily` + `adj_factor`)
- Cross-exchange name resolver ("moutai" / "茅台" → `sh600519`)
- FX rates (USD/CNY, USD/HKD) — add a `fx.sh` sibling script
- A wrapping MCP server for typed I/O (deferred until a consumer needs it)
