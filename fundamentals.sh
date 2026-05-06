#!/usr/bin/env bash
# fundamentals.sh — one-stop basic-fundamentals snapshot for an A-share.
#
# Wraps three tushare APIs (daily_basic, fina_indicator, forecast) into a
# single human-readable report so a researcher can read "业绩 + 估值 + 预告"
# in one shot without plumbing CSV together by hand.
#
# Usage:
#   fundamentals.sh <ticker> [quarters=8]
#
# Ticker shapes (A-shares only for now — HK has no `fina_indicator` coverage
# on the free tier, and US is not covered at all):
#   sh600519 → 600519.SH    sz000001 → 000001.SZ    bj831168 → 831168.BJ
#
# Environment: TUSHARE_TOKEN required.
# Dependencies: bash + python3 (stdlib).
#
# Output sections:
#   1. 估值快照 — 最近一个交易日的 PE/PB/股息率/总市值
#   2. 最近 N 期财务 — ROE / 营收增速 / 净利增速 / 毛利率 / 净利率 / 资产负债率
#   3. 业绩预告/快报 — 最近 3 期的 forecast 结果（扭亏/增长/下降的信号）
#
# Example:
#   $ ./fundamentals.sh sh688256     # 寒武纪完整基本面

set -euo pipefail

ticker="${1:-}"
quarters="${2:-8}"
quarters="${quarters#quarters=}"

if [[ -z "$ticker" || "$ticker" == "-h" || "$ticker" == "--help" ]]; then
    cat >&2 <<'EOF'
usage: fundamentals.sh <ticker> [quarters=8]

ticker shapes:
  sh600519 / sz000001 / bj831168  →  A-share (SSE / SZSE / BJSE)

pulls & renders:
  - valuation snapshot (PE_TTM / PB / 股息率 / 市值)
  - last N quarters: ROE / 营收利润增速 / 毛利净利率 / 资产负债率
  - recent forecasts (业绩预告: 扭亏/续盈/增减幅)

env: TUSHARE_TOKEN required.

examples:
  fundamentals.sh sh688256
  fundamentals.sh sz000858 quarters=12
EOF
    exit 2
fi

if ! [[ "$quarters" =~ ^[0-9]+$ ]] || (( quarters < 1 || quarters > 40 )); then
    echo "bad quarters=$quarters (expected int 1..40)" >&2
    exit 2
fi

# Route ticker → ts_code
ticker_lower="${ticker,,}"
case "$ticker_lower" in
    sh[0-9]*) ts_code="${ticker_lower#sh}.SH" ;;
    sz[0-9]*) ts_code="${ticker_lower#sz}.SZ" ;;
    bj[0-9]*) ts_code="${ticker_lower#bj}.BJ" ;;
    hk[0-9]*)
        echo "ERROR: HK stocks not supported — fina_indicator is A-share only." >&2
        echo "For HK, try: history.sh $ticker (price only) + search_tavily (news)." >&2
        exit 2
        ;;
    *)
        echo "unrecognized ticker shape: $ticker (expect sh/sz/bj prefix)" >&2
        exit 2
        ;;
esac

here="$(dirname "$(readlink -f "$0")")"

# Use python for the heavy lifting — needs multiple tushare calls + formatting.
# Pass ts_code + quarters via argv; the python block only lives in this script.
exec python3 - "$ts_code" "$quarters" "$here" <<'PY'
import csv, subprocess, sys

ts_code  = sys.argv[1]
quarters = int(sys.argv[2])
here     = sys.argv[3]

def tushare(api, **params):
    """Call tushare.py as subprocess, return list of dicts (--csv mode)."""
    args = ["python3", f"{here}/tushare.py", api]
    for k, v in params.items():
        if k == "fields":
            args.append(f"--fields={v}")
        else:
            args.append(f"{k}={v}")
    args.append("--csv")
    out = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        print(f"[WARN] tushare {api} failed: {out.stderr.strip()[:200]}", file=sys.stderr)
        return []
    return list(csv.DictReader(out.stdout.splitlines()))

def fmt_pct(v, width=7):
    if v is None or v == "": return " " * width
    try: return f"{float(v):+{width-1}.2f}%"
    except ValueError: return v.rjust(width)

def fmt_num(v, width=8):
    if v is None or v == "": return " " * width
    try: return f"{float(v):{width}.2f}"
    except ValueError: return v.rjust(width)

# --- 1. VALUATION SNAPSHOT ---
# daily_basic: latest trading day. Don't hard-code a date; tushare returns
# the most recent available when trade_date is omitted, but requires
# ts_code + trade_date or ts_code + date range. We pull last 5 days and
# take the newest.
import datetime
today = datetime.date.today().strftime("%Y%m%d")
ago5  = (datetime.date.today() - datetime.timedelta(days=7)).strftime("%Y%m%d")

db = tushare("daily_basic", ts_code=ts_code, start_date=ago5, end_date=today,
             fields="ts_code,trade_date,close,turnover_rate,pe,pe_ttm,pb,ps_ttm,dv_ratio,dv_ttm,total_mv,circ_mv")

print()
print("=" * 72)
print(f" {ts_code}  综合基本面快照")
print("=" * 72)

if not db:
    print("  [估值数据拉取失败或无数据]")
else:
    r = db[0]  # newest
    total_mv_yi = float(r['total_mv']) / 1e4 if r.get('total_mv') else 0
    circ_mv_yi  = float(r['circ_mv'])  / 1e4 if r.get('circ_mv')  else 0
    print(f"\n📊 估值快照 (交易日 {r['trade_date']})")
    print(f"   收盘价        : {fmt_num(r.get('close'))}")
    print(f"   PE (TTM)      : {fmt_num(r.get('pe_ttm'))}")
    print(f"   PB            : {fmt_num(r.get('pb'))}")
    print(f"   PS (TTM)      : {fmt_num(r.get('ps_ttm'))}")
    print(f"   股息率 (TTM)  : {fmt_pct(r.get('dv_ttm'))}")
    print(f"   换手率        : {fmt_pct(r.get('turnover_rate'))}")
    print(f"   总市值        : {total_mv_yi:>8.2f} 亿")
    print(f"   流通市值      : {circ_mv_yi:>8.2f} 亿")

# --- 2. FINANCIAL INDICATORS (last N quarters) ---
fi = tushare("fina_indicator", ts_code=ts_code,
             fields="ts_code,end_date,roe,roa,netprofit_yoy,or_yoy,grossprofit_margin,netprofit_margin,debt_to_assets")

print(f"\n📈 最近 {min(quarters, len(fi))} 期财务指标")
if not fi:
    print("   [财务数据为空]")
else:
    print(f"   {'报告期':<10} {'ROE':>8} {'ROA':>8} {'营收YoY':>10} {'净利YoY':>10} "
          f"{'毛利率':>8} {'净利率':>8} {'资产负债':>10}")
    print("   " + "-" * 74)
    for r in fi[:quarters]:
        print(f"   {r.get('end_date','-'):<10} "
              f"{fmt_pct(r.get('roe')):>8} "
              f"{fmt_pct(r.get('roa')):>8} "
              f"{fmt_pct(r.get('or_yoy')):>10} "
              f"{fmt_pct(r.get('netprofit_yoy')):>10} "
              f"{fmt_pct(r.get('grossprofit_margin')):>8} "
              f"{fmt_pct(r.get('netprofit_margin')):>8} "
              f"{fmt_pct(r.get('debt_to_assets')):>10}")

# --- 3. FORECASTS (业绩预告) ---
fc = tushare("forecast", ts_code=ts_code,
             fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,summary")

print(f"\n🔮 业绩预告 (最近 5 条)")
if not fc:
    print("   [无预告数据]")
else:
    print(f"   {'公告日':<10} {'报告期':<10} {'类型':<8} {'增幅下限':>10} {'增幅上限':>10}   预告摘要")
    print("   " + "-" * 80)
    seen = set()  # dedupe by (ann_date, end_date, type)
    count = 0
    for r in fc:
        key = (r.get('ann_date'), r.get('end_date'), r.get('type'))
        if key in seen: continue
        seen.add(key)
        summary = (r.get('summary') or '')[:40]
        print(f"   {r.get('ann_date','-'):<10} "
              f"{r.get('end_date','-'):<10} "
              f"{(r.get('type') or '-'):<8} "
              f"{fmt_pct(r.get('p_change_min')):>10} "
              f"{fmt_pct(r.get('p_change_max')):>10}   "
              f"{summary}")
        count += 1
        if count >= 5: break

print()
PY
