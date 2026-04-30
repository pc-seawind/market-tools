#!/usr/bin/env bash
# quote.sh — snapshot quote for a ticker.
#
# Usage:
#   ./quote.sh <ticker>
#
# Routing (auto by ticker shape):
#   sh600519 / sz000001  →  Sina Finance (A-shares, GBK → UTF-8 converted)
#   hk00700 / hk03690    →  Sina Finance (HK, same endpoint)
#   AAPL / TSLA          →  Stooq (US, CSV format)
#   *                    →  try Stooq as fallback with .us suffix
#
# Output format (one-line human-readable summary, plus a second line
# echoing the raw API response so the agent can parse for more fields):
#
#   AAPL | price=271.35 open=270.42 high=276.00 low=268.14 vol=52,976,488 ts=2026-04-30 22:00:20
#   <raw csv/varline>
#
# Dependencies: bash, curl. NO Python, NO API key. Rate-limited by the
# upstream services — don't spam in a loop.
#
# Limitations:
# - Snapshot only (no historical). For history use the Yahoo-format APIs
#   through a paid proxy, or add tushare/akshare later.
# - Delayed ~15 min during market hours (Sina + Stooq are free-tier).

set -euo pipefail

ticker="${1:-}"
if [[ -z "$ticker" ]]; then
    echo "usage: quote.sh <ticker>" >&2
    echo "  examples: quote.sh AAPL    # US stock via Stooq" >&2
    echo "            quote.sh sh600519  # A-share via Sina" >&2
    exit 2
fi

# Normalize
ticker_lower="${ticker,,}"

# ------ CN A-shares / HK via Sina ------
# Patterns: sh600519, sz000001, hk00700, bj831168 (Beijing STAR)
if [[ "$ticker_lower" =~ ^(sh|sz|hk|bj)[0-9]{5,6}$ ]]; then
    raw=$(curl -sS --max-time 8 \
        -H 'Referer: https://finance.sina.com.cn/' \
        "https://hq.sinajs.cn/list=${ticker_lower}")
    # GBK → UTF-8 so the company name in var_str renders correctly
    if command -v iconv >/dev/null 2>&1; then
        raw=$(printf '%s' "$raw" | iconv -f GBK -t UTF-8//IGNORE 2>/dev/null || printf '%s' "$raw")
    fi
    # var hq_str_sh600519="name,open,prev_close,now,high,low,...,date,time,..."
    body="${raw#*\"}"
    body="${body%\"*}"
    IFS=',' read -ra f <<< "$body"
    name="${f[0]:-}"
    open="${f[1]:-?}"
    prev_close="${f[2]:-?}"
    now_price="${f[3]:-?}"
    high="${f[4]:-?}"
    low="${f[5]:-?}"
    volume="${f[8]:-?}"
    date_str="${f[30]:-?}"
    time_str="${f[31]:-?}"
    echo "${ticker} (${name}) | price=${now_price} open=${open} prev_close=${prev_close} high=${high} low=${low} vol=${volume} ts=${date_str} ${time_str}"
    echo "$raw"
    exit 0
fi

# ------ US (default) via Stooq ------
# Strip any existing .us / .US suffix so a bare `AAPL` or `aapl.us` both work.
stem="${ticker_lower%.us}"
csv=$(curl -sS --max-time 8 "https://stooq.com/q/l/?s=${stem}.us&f=sd2t2ohlcv&e=csv")
# Stooq returns a single line without a header, comma-separated.
# Header-bearing responses also exist but our &f=... pins the column set.
# If the ticker is missing, Stooq writes "<sym>.US,N/D,N/D,...".
data=$(printf '%s\n' "$csv" | grep -v '^Symbol,' | head -1)
if [[ -z "$data" || "$data" == *"N/D,"* ]]; then
    echo "${ticker}: no data from Stooq (delisted / bad symbol?)" >&2
    echo "$csv" >&2
    exit 1
fi
IFS=',' read -ra f <<< "$data"
sym="${f[0]:-?}"
date_str="${f[1]:-?}"
time_str="${f[2]:-?}"
open="${f[3]:-?}"
high="${f[4]:-?}"
low="${f[5]:-?}"
close="${f[6]:-?}"
volume="${f[7]:-?}"
echo "${sym} | price=${close} open=${open} high=${high} low=${low} vol=${volume} ts=${date_str} ${time_str}"
echo "$csv"
