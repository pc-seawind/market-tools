#!/usr/bin/env bash
# diligence.sh — one-shot 六维度 diligence report for a ticker.
#
# Wraps the rest of market-tools into a single command that runs every
# shell-accessible dimension of analysis and prints a unified report:
#
#   §1. 量价技术面       — quote + computed 120-day stats
#   §2. 基本面           — fundamentals.sh (A-share only)
#   §3. 机构资金         — flows.sh (A-share only)
#   §4. 政策面           — policy.sh recent 7 days, tech-oriented grep
#   §5. 消息/跨市场接续步骤 — pre-filled search queries for the agent/user
#                            to run out-of-band (search_baidu / search_tavily /
#                            fetch_url live outside this script).
#
# Market support:
#   sh/sz/bj (A-shares) — full §1-§4 + §5
#   hk       (HK)       — §1 + §4 + §5 only (HK lacks fina_indicator/
#                          top10_floatholders on tushare free tier)
#   US       (AAPL/…)   — §1 snapshot only + §5 (no history API)
#
# Runtime:
#   A-share: ~3-4 min (policy.sh is the slow leg due to cctv_news rate limit)
#   HK:      ~1 min
#   US:      <10 sec
#
# Usage:
#   diligence.sh <ticker> [quarters=6]
#
# Examples:
#   diligence.sh sz300308               # 中际旭创 full report
#   diligence.sh hk00700 quarters=8     # 腾讯 量价+政策
#   diligence.sh NVDA                   # NVIDIA snapshot only

set -uo pipefail   # NOT -e: section failures are isolated, don't kill pipeline

ticker="${1:-}"
quarters="${2:-6}"
quarters="${quarters#quarters=}"

if [[ -z "$ticker" || "$ticker" == "-h" || "$ticker" == "--help" ]]; then
    sed -n '2,35p' "$0" | sed 's/^# \?//'
    exit 2
fi

if ! [[ "$quarters" =~ ^[0-9]+$ ]] || (( quarters < 2 || quarters > 12 )); then
    echo "bad quarters=$quarters (expected 2..12)" >&2
    exit 2
fi

here="$(dirname "$(readlink -f "$0")")"
t_lower="${ticker,,}"

# Market routing
case "$t_lower" in
    sh[0-9]*|sz[0-9]*|bj[0-9]*) market=A  ;;
    hk[0-9]*)                   market=HK ;;
    *)                          market=US ;;
esac

sep() {
    echo
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  $1"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ═══════════════════ HEADER ═══════════════════
echo
echo "════════════════════════════════════════════════════════════════════════"
echo "  📋 DILIGENCE REPORT  ·  $ticker  (market=$market)"
echo "  生成时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════════════════════════"

# ═══════════════════ §1 量价技术面 ═══════════════════
sep "§1. 量价技术面"

# Snapshot quote
"$here/quote.sh" "$ticker" 2>&1 | head -2 || echo "  [quote.sh failed]"
echo

# Computed 120-day stats (skip for US — history.sh doesn't cover)
if [[ "$market" != "US" ]]; then
    hist_file=$(mktemp -t diligence-hist-XXXXXX.csv)
    trap 'rm -f "$hist_file"' EXIT
    if "$here/history.sh" "$ticker" days=120 > "$hist_file" 2>/dev/null && [[ -s "$hist_file" ]]; then
        echo "  ─── 120 天技术指标 ───"
        python3 - "$hist_file" <<'PY'
import csv, math, statistics, sys
try:
    with open(sys.argv[1]) as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("  [history 无数据]"); sys.exit(0)
    closes = [float(r['close']) for r in rows]
    highs  = [float(r['high'])  for r in rows]
    lows   = [float(r['low'])   for r in rows]
    chrono = list(reversed(closes))
    peak = chrono[0]; mdd = 0
    for p in chrono:
        if p > peak: peak = p
        dd = (p - peak) / peak * 100
        if dd < mdd: mdd = dd
    rets = [math.log(chrono[i+1]/chrono[i]) for i in range(len(chrono)-1)]
    ann_vol = statistics.pstdev(rets) * math.sqrt(252) * 100 if rets else 0
    def r_d(b):
        return None if b >= len(closes) else (closes[0] - closes[b]) / closes[b] * 100
    hi, lo, cur = max(highs), min(lows), closes[0]
    pos = (cur - lo) / (hi - lo) * 100 if hi > lo else 50
    print(f"  当前价:      {cur:.2f}")
    print(f"  120 天区间:  [{lo:.2f}, {hi:.2f}]")
    print(f"  120 天位置:  {pos:.1f}%     (0=底部, 100=顶部)")
    print(f"  最大回撤:    {mdd:.1f}%")
    print(f"  年化波动率:  {ann_vol:.1f}%")
    for tag, b in [("1W",5),("1M",20),("3M",60),("6M",120)]:
        v = r_d(b)
        print(f"  {tag} 收益:     {'n/a' if v is None else f'{v:+.1f}%'}")
except Exception as e:
    print(f"  [tech stats failed: {e}]")
PY
    else
        echo "  [history.sh 失败或无数据]"
    fi
fi

# ═══════════════════ §2 基本面 ═══════════════════
if [[ "$market" == "A" ]]; then
    sep "§2. 基本面 (业绩 + 估值 + 预告)"
    "$here/fundamentals.sh" "$ticker" "$quarters" 2>&1 || echo "  [fundamentals.sh failed]"
else
    sep "§2. 基本面 — 跳过 (HK/US 在 tushare 免费层无对应 API)"
    echo "  建议手工:"
    echo "    search_tavily \"$ticker earnings revenue guidance\""
fi

# ═══════════════════ §3 机构资金 ═══════════════════
if [[ "$market" == "A" ]]; then
    sep "§3. 机构资金 (smart money 动作)"
    flow_q=$(( quarters > 6 ? 6 : quarters ))
    "$here/flows.sh" "$ticker" "$flow_q" 2>&1 || echo "  [flows.sh failed]"
else
    sep "§3. 机构资金 — 跳过 (HK/US 在 tushare 免费层无对应 API)"
fi

# ═══════════════════ §4 政策面 ═══════════════════
sep "§4. 政策面 (CCTV 新闻联播, 过去 7 天)"
"$here/policy.sh" days=7 --grep='半导体|芯片|人工智能|AI|算力|基础研究|科技|制造业|新能源|机器人|生物医药|消费|金融|房地产|军工|国防' 2>&1 \
    | tail -n +1 || echo "  [policy.sh failed]"

# ═══════════════════ §5 接续步骤 (消息/跨市场) ═══════════════════
sep "§5. 消息面 / 跨市场 接续步骤 (out-of-band)"

cat <<EOF

  以下内容 shell 脚本无法直接执行,需 agent/user 用 gateway 的搜索
  工具并行拉取 —— pre-filled queries:

  [消息面 · 国内]
    search_baidu "$ticker 最新业绩 2026"        (news resource)
    search_baidu "$ticker 机构评级 研报"         (web resource)
    search_baidu "$ticker 龙虎榜 主力资金"       (news resource)

  [跨市场锚点 · 国际]
    # 基于行业推测可能的美股锚点 (需 agent 判断对应赛道):
    search_tavily "$ticker industry peer 2026 outlook"
    quote AAPL / MSFT / NVDA / TSM / GOOGL / META / TSLA  — 按赛道挑
    # 例: 光模块/算力 → NVDA + TSM; 新能源车 → TSLA; 创新药 → PFE/MRK

  [深入挖掘 (可选)]
    fetch_url <具体研报/公告 URL>                # 读全文
    tushare.py fina_audit ts_code=...            # 审计意见 (若有权限)
    tushare.py top_list trade_date=YYYYMMDD      # 某日龙虎榜明细

EOF

echo
echo "════════════════════════════════════════════════════════════════════════"
echo "  ✅ DILIGENCE 数据采集完成 · $ticker"
echo "════════════════════════════════════════════════════════════════════════"
echo
