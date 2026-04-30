# market-tools

Lean market-data utility scripts the `investment` (and other finance-adjacent)
homespace domains import as artifacts.

## Scripts

### `quote.sh <ticker>`

Snapshot quote — price, open/high/low, volume, timestamp.

**Auto-routes by ticker shape:**

| Shape | Source | Example |
|-------|--------|---------|
| `sh######` / `sz######` | Sina Finance (A-shares) | `sh600519` (贵州茅台) |
| `hk######` | Sina Finance (Hong Kong) | `hk00700` (腾讯) |
| `bj######` | Sina Finance (Beijing STAR) | `bj831168` |
| `AAPL` / `TSLA` | Stooq.com (US) | `AAPL`, `tsla.us` |

**Output** is two lines:
1. Human-readable one-line summary (parseable as `key=value` pairs)
2. The raw upstream response (JSON / CSV / var-string) for further parsing

**Examples:**

```bash
$ ./quote.sh AAPL
AAPL.US | price=271.35 open=270.425 high=276 low=268.14 vol=52976488 ts=2026-04-30 22:00:20
AAPL.US,2026-04-30,22:00:20,270.425,276,268.14,271.35,52976488

$ ./quote.sh sh600519
sh600519 (贵州茅台) | price=1384.79 open=1400 prev_close=1401.17 ... ts=2026-04-30 15:00:00
var hq_str_sh600519="贵州茅台,1400.000,1401.170,1384.790,...
```

**Limitations:**
- Snapshot only, no history. Delay ~15 min during US/CN market hours.
- Both sources are free-tier; don't spam in tight loops.
- No authentication, no API keys. Good for quick research; NOT for trading
  system input.

## Dependencies

Bash + `curl` + (optional) `iconv` for GBK→UTF-8 conversion on A-share
company names. Nothing to `pip install`.

## Future

- Historical OHLCV (likely `akshare` / `tushare` with a token — separate artifact)
- Cross-exchange name resolver ("moutai" → `sh600519`)
- FX rates (USD/CNY, USD/HKD) — add a `fx.sh` sibling script
- A wrapping MCP server for typed I/O (deferred until a consumer needs it)
