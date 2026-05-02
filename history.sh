#!/usr/bin/env bash
# history.sh — daily OHLCV history for a ticker, via tushare Pro.
#
# Usage:
#   history.sh <ticker> [days=60] [adj=none|qfq|hfq]
#
# Ticker routing (auto by shape, same prefixes as quote.sh):
#   sh600519 / sz000001  →  tushare `daily`    → ts_code=600519.SH
#   hk00700              →  tushare `hk_daily` → ts_code=00700.HK
#   bj831168             →  tushare `daily`    → ts_code=831168.BJ
#                          (coverage depends on token tier)
#
# Defaults:
#   days=60  — approximately the last 60 trading days (newest row first)
#   adj=none — unadjusted (qfq/hfq not yet wired; see TODO below)
#
# Output: CSV header + rows, newest first.
#   trade_date,open,high,low,close,vol,amount
#
# Environment: TUSHARE_TOKEN must be set.
# Dependencies: bash, python3 (stdlib), coreutils `date`.
#
# TODO: adj=qfq/hfq would need the `pro_bar` API (SDK-only helper) or
# manual merge of `daily` + `adj_factor`. Skipped in v1.

set -euo pipefail

ticker="${1:-}"
if [[ -z "$ticker" || "$ticker" == "-h" || "$ticker" == "--help" ]]; then
    cat >&2 <<'EOF'
usage: history.sh <ticker> [days=60] [adj=none|qfq|hfq]

ticker shapes (prefix-based, matching quote.sh):
  sh600519 / sz000001  →  A-share daily via tushare `daily`
  hk00700              →  HK daily via tushare `hk_daily`
  bj831168             →  北交所 via tushare `daily` (limited coverage)

output: CSV, newest row first. default 60 trading days.
env:    TUSHARE_TOKEN required.

examples:
  history.sh sh600519
  history.sh hk00700 days=120
EOF
    exit 2
fi

days=60
adj="none"
for arg in "${@:2}"; do
    case "$arg" in
        days=*) days="${arg#days=}" ;;
        adj=*)  adj="${arg#adj=}"   ;;
        *)      echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if ! [[ "$days" =~ ^[0-9]+$ ]] || (( days < 1 || days > 5000 )); then
    echo "bad days=$days (expected integer 1..5000)" >&2
    exit 2
fi

if [[ "$adj" != "none" ]]; then
    echo "warning: adj=${adj} not yet implemented; returning unadjusted prices" >&2
fi

ticker_lower="${ticker,,}"

case "$ticker_lower" in
    sh[0-9]*) ts_code="${ticker_lower#sh}.SH"; api=daily    ;;
    sz[0-9]*) ts_code="${ticker_lower#sz}.SZ"; api=daily    ;;
    bj[0-9]*) ts_code="${ticker_lower#bj}.BJ"; api=daily    ;;
    hk[0-9]*) ts_code="${ticker_lower#hk}.HK"; api=hk_daily ;;
    *)
        echo "unrecognized ticker shape: $ticker (expect sh/sz/hk/bj prefix)" >&2
        exit 2
        ;;
esac

# Trading-day / calendar-day slack: 5 trading days ≈ 7 calendar days,
# plus 15 extra to absorb long holidays (CN Spring Festival, HK public).
span=$(( days * 7 / 5 + 15 ))
start_date=$(date -d "${span} days ago" +%Y%m%d)
end_date=$(date +%Y%m%d)

here="$(dirname "$(readlink -f "$0")")"

# fields common to `daily` and `hk_daily`
fields="trade_date,open,high,low,close,vol,amount"

# tushare returns newest-first; head caps to header + `days` rows.
"$here/tushare.py" "$api" \
    ts_code="$ts_code" \
    start_date="$start_date" \
    end_date="$end_date" \
    --fields="$fields" --csv | head -n $((days + 1))
