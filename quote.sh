#!/usr/bin/env bash
# quote.sh — snapshot quote for a ticker.
#
# Usage:
#   ./quote.sh <ticker>
#
# Routing (auto by ticker shape):
#   sh600519 / sz000001  →  Sina Finance (A-shares, GBK → UTF-8 converted)
#   hk00700 / hk03690    →  Xueqiu quote API when XUEQIU_COOKIE is present,
#                           otherwise Sina Finance fallback
#   AAPL / TSLA          →  Xueqiu quote API when XUEQIU_COOKIE is present,
#                           otherwise Yahoo Finance fallback, then Stooq
#   *                    →  try Yahoo/Stooq as US fallback
#
# Output format (one-line human-readable summary, plus a second line
# echoing the raw API response so the agent can parse for more fields):
#
#   AAPL | price=271.35 open=270.42 high=276.00 low=268.14 vol=52,976,488 ts=2026-04-30 22:00:20
#   <raw csv/varline>
#
# Dependencies: bash, curl. Optional python3 + XUEQIU_COOKIE enables
# Agent Reach / Xueqiu quote coverage for HK/US tickers. Rate-limited by
# the upstream services — don't spam in a loop.
#
# Limitations:
# - Snapshot only (no historical). For history use the Yahoo-format APIs
#   through a paid proxy, or add tushare/akshare later.
# - Delayed during market hours depending on upstream source
#   (Xueqiu/Sina/Stooq free/community tier).

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

# ------ Optional Agent Reach / Xueqiu quote API for HK + US ------
# Xueqiu supports US symbols like AAPL/TSLA and HK symbols without the `hk`
# prefix (00700, 00300).  It is a better cross-market snapshot source than
# Stooq/Sina when the read-only cookie is available.  Keep it optional so the
# zero-dep public fallback still works in fresh environments.
try_xueqiu_quote() {
    local input="$1"
    local norm="${input,,}"
    local symbol=""

    if [[ -z "${XUEQIU_COOKIE:-}" ]]; then
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        return 1
    fi

    # Normalize to Xueqiu symbol convention.
    if [[ "$norm" =~ ^hk[0-9]{5}$ ]]; then
        symbol="${norm#hk}"
    elif [[ "$norm" =~ ^[0-9]{5}\.hk$ ]]; then
        symbol="${norm%.hk}"
    elif [[ "$norm" =~ ^[0-9]{5}$ ]]; then
        symbol="$norm"
    elif [[ "$norm" =~ ^[a-z]{1,5}(\.us)?$ ]]; then
        symbol="${norm%.us}"
        symbol="${symbol^^}"
    else
        return 1
    fi

    XQ_SYMBOL="$symbol" XQ_INPUT="$input" python3 - <<'PY'
import datetime as _dt
import json
import os
import sys
import urllib.parse
import urllib.request

cookie = os.environ.get("XUEQIU_COOKIE") or ""
symbol = os.environ.get("XQ_SYMBOL") or ""
original = os.environ.get("XQ_INPUT") or symbol
if not cookie or not symbol:
    sys.exit(1)

url = "https://stock.xueqiu.com/v5/stock/batch/quote.json?symbol=" + urllib.parse.quote(symbol)
req = urllib.request.Request(
    url,
    headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"https://xueqiu.com/S/{urllib.parse.quote(symbol)}",
        "Cookie": cookie,
    },
)
try:
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    items = ((data.get("data") or {}).get("items") or [])
    quote = (items[0].get("quote") or {}) if items else {}
except Exception:
    sys.exit(1)

price = quote.get("current")
if price in (None, ""):
    sys.exit(1)

def val(key, default="?"):
    v = quote.get(key)
    return default if v in (None, "") else v

ts = quote.get("timestamp")
if isinstance(ts, (int, float)) and ts > 0:
    ts_text = _dt.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M:%S")
else:
    ts_text = "?"

name = val("name", "")
display = val("symbol", symbol)
summary = (
    f"{original} ({name}) | source=xueqiu symbol={display} "
    f"price={price} chg={val('chg')} pct={val('percent')} "
    f"open={val('open')} prev_close={val('last_close')} "
    f"high={val('high')} low={val('low')} vol={val('volume')} "
    f"amount={val('amount')} currency={val('currency')} ts={ts_text}"
)
print(summary)
print(raw)
PY
}

# ------ CN A-shares / HK via Sina ------
# Patterns: sh600519, sz000001, hk00700, bj831168 (Beijing STAR)
if [[ "$ticker_lower" =~ ^(sh|sz|hk|bj)[0-9]{5,6}$ ]]; then
    # Prefer Xueqiu for HK because it carries better cross-market fields
    # (currency, amount, market cap in raw JSON). Keep Sina as fallback.
    if [[ "$ticker_lower" =~ ^hk[0-9]{5}$ ]]; then
        if try_xueqiu_quote "$ticker"; then
            exit 0
        fi
    fi

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
    if [[ "$ticker_lower" =~ ^hk[0-9]{5}$ ]]; then
        # Sina HK field order differs from A-shares:
        # 0=en_name 1=zh_name 2=open 3=prev_close 4=high 5=low 6=price
        # 7=chg 8=pct 11=amount 12=volume 17=date 18=time
        name="${f[1]:-${f[0]:-}}"
        open="${f[2]:-?}"
        prev_close="${f[3]:-?}"
        now_price="${f[6]:-?}"
        high="${f[4]:-?}"
        low="${f[5]:-?}"
        volume="${f[12]:-?}"
        date_str="${f[17]:-?}"
        time_str="${f[18]:-?}"
    else
        # var hq_str_sh600519="name,open,prev_close,now,high,low,...,date,time,..."
        name="${f[0]:-}"
        open="${f[1]:-?}"
        prev_close="${f[2]:-?}"
        now_price="${f[3]:-?}"
        high="${f[4]:-?}"
        low="${f[5]:-?}"
        volume="${f[8]:-?}"
        date_str="${f[30]:-?}"
        time_str="${f[31]:-?}"
    fi
    echo "${ticker} (${name}) | source=sina price=${now_price} open=${open} prev_close=${prev_close} high=${high} low=${low} vol=${volume} ts=${date_str} ${time_str}"
    echo "$raw"
    exit 0
fi

# ------ US (default) via Stooq ------
# Prefer Xueqiu for US when available; it has a cleaner real-time snapshot
# and also gives market cap/amount in raw JSON. Fallback remains Stooq.
if try_xueqiu_quote "$ticker"; then
    exit 0
fi

try_yahoo_quote() {
    local input="$1"
    local norm="${input,,}"
    local symbol="${norm%.us}"
    if [[ ! "$symbol" =~ ^[a-z]{1,5}$ ]]; then
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        return 1
    fi
    YF_SYMBOL="${symbol^^}" YF_INPUT="$input" python3 - <<'PY'
import datetime as _dt
import json
import os
import sys
import urllib.parse
import urllib.request

symbol = os.environ.get("YF_SYMBOL") or ""
original = os.environ.get("YF_INPUT") or symbol
if not symbol:
    sys.exit(1)
url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=1d&interval=1m"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
try:
    with urllib.request.urlopen(req, timeout=8) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        sys.exit(1)
    node = result[0]
    meta = node.get("meta") or {}
    price = meta.get("regularMarketPrice") or meta.get("previousClose")
    if price in (None, ""):
        sys.exit(1)
except Exception:
    sys.exit(1)

ts = meta.get("regularMarketTime")
if isinstance(ts, (int, float)) and ts > 0:
    ts_text = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
else:
    ts_text = "?"

def val(key, default="?"):
    v = meta.get(key)
    return default if v in (None, "") else v

quote = (((node.get("indicators") or {}).get("quote") or [{}])[0] or {})

def first_non_null(values, default="?"):
    if isinstance(values, list):
        for item in values:
            if item not in (None, ""):
                return item
    return default

open_price = meta.get("regularMarketOpen")
if open_price in (None, ""):
    open_price = first_non_null(quote.get("open"))
volume = meta.get("regularMarketVolume")
if volume in (None, ""):
    volume = first_non_null(quote.get("volume"))

raw_small = {"chart": {"result": [{"meta": meta}], "error": (data.get("chart") or {}).get("error")}}

print(
    f"{original} | source=yahoo symbol={val('symbol', symbol)} "
    f"price={price} open={open_price} "
    f"prev_close={val('previousClose')} high={val('regularMarketDayHigh')} "
    f"low={val('regularMarketDayLow')} vol={volume} currency={val('currency')} ts={ts_text}"
)
print(json.dumps(raw_small, ensure_ascii=False, separators=(",", ":")))
PY
}

if try_yahoo_quote "$ticker"; then
    exit 0
fi

# Strip any existing .us / .US suffix so a bare `AAPL` or `aapl.us` both work.
stem="${ticker_lower%.us}"
csv=$(curl -sS --max-time 8 "https://stooq.com/q/l/?s=${stem}.us&f=sd2t2ohlcv&e=csv")
# Stooq returns a single line without a header, comma-separated.
# Header-bearing responses also exist but our &f=... pins the column set.
# If the ticker is missing, Stooq writes "<sym>.US,N/D,N/D,...".
data=$(printf '%s\n' "$csv" | grep -v '^Symbol,' | head -1)
if [[ -z "$data" || "$data" == *"N/D,"* || "$data" == *"<"* || "$data" != *,* ]]; then
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
