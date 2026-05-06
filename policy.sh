#!/usr/bin/env bash
# policy.sh — CCTV 新闻联播 抓取 + 关键词过滤,提取政策风向信号。
#
# 为什么是新闻联播? 作为国家级喉舌节目,出现在联播中的产业/技术/政策
# 方向几乎 100% 会成为后续一段时间的资金主线或监管重点。对投研而言
# 它是成本最低的"官方信号"来源 (tushare cctv_news 接口免费)。
#
# Usage:
#   policy.sh [days=7] [--grep=关键词1|关键词2] [--all]
#
# Defaults:
#   days=7           - 回看过去 7 天
#   (no --grep)      - 只返回每天的完整标题列表(默认只取经济/科技相关)
#   --grep=<regex>   - 过滤标题包含任一关键词 (| 分隔多个)
#   --all            - 不过滤,返回所有标题 (默认会尝试过滤掉外交/慰问/会议等)
#
# Environment: TUSHARE_TOKEN required.
#
# Examples:
#   policy.sh                                    # 7天,默认过滤器
#   policy.sh days=30                            # 30天,默认过滤器
#   policy.sh days=30 --grep='半导体|AI|算力|芯片'  # 30天,只看 AI 相关
#   policy.sh days=3 --all                       # 3天,全部标题

set -euo pipefail

days=7
grep_pat=""
show_all=0

for arg in "$@"; do
    case "$arg" in
        days=*)    days="${arg#days=}" ;;
        --grep=*)  grep_pat="${arg#--grep=}" ;;
        --all)     show_all=1 ;;
        -h|--help)
            sed -n '2,25p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if ! [[ "$days" =~ ^[0-9]+$ ]] || (( days < 1 || days > 90 )); then
    echo "bad days=$days (expected int 1..90)" >&2; exit 2
fi

here="$(dirname "$(readlink -f "$0")")"

# Build date range (last N calendar days; CCTV runs every day).
end_date=$(date +%Y%m%d)
start_date=$(date -d "${days} days ago" +%Y%m%d)

exec python3 - "$here" "$start_date" "$end_date" "$grep_pat" "$show_all" <<'PY'
import csv, re, subprocess, sys, datetime

here, start_date, end_date, grep_pat, show_all = sys.argv[1:6]
show_all = (show_all == "1")

import time as _time

def cctv(date):
    # timeout large enough to accommodate tushare.py's 3-step retry (~135s)
    # plus a safety margin. cctv_news has a tight rate limit.
    try:
        out = subprocess.run(
            ["python3", f"{here}/tushare.py", "cctv_news", f"date={date}",
             "--fields=date,title", "--csv"],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        print(f"[WARN] cctv_news {date} timed out after 180s", file=sys.stderr)
        return []
    if out.returncode != 0:
        print(f"[WARN] cctv_news {date} failed: {out.stderr.strip()[:150]}", file=sys.stderr)
        return []
    return list(csv.DictReader(out.stdout.splitlines()))

# Default filter: strip noise (diplomatic visits, condolences, routine meetings).
# Keep: economy / tech / industry / policy / reform signals.
NOISE_PAT = re.compile(
    r"(会见|会谈|致电|致函|致以.*慰问|贺信|吊唁|国际联播快讯|"
    r"国内联播快讯|气象预警|天气|春晚|春节联欢|军委|签署.*主席令|"
    r"胜利日|阅兵|凯旋|外交部|大使)"
)

# Build iterator over dates
def daterange(start, end):
    d = datetime.datetime.strptime(start, "%Y%m%d").date()
    e = datetime.datetime.strptime(end, "%Y%m%d").date()
    while d <= e:
        yield d.strftime("%Y%m%d")
        d += datetime.timedelta(days=1)

all_rows = []
dates_list = list(daterange(start_date, end_date))
print(f"pulling CCTV news for {len(dates_list)} days...", file=sys.stderr)
for i, d in enumerate(dates_list):
    # 2s gap to stay comfortably under rate limit (assumed 5-10/min)
    if i > 0: _time.sleep(2)
    rows = cctv(d)
    if rows:
        all_rows.extend(rows)
        print(f"  [{i+1:>2}/{len(dates_list)}] {d}: {len(rows)} titles", file=sys.stderr)
    else:
        print(f"  [{i+1:>2}/{len(dates_list)}] {d}: (empty/failed)", file=sys.stderr)

# Sort newest-first
all_rows.sort(key=lambda r: r.get("date", ""), reverse=True)

# Apply filters
if grep_pat:
    pat = re.compile(grep_pat, re.IGNORECASE)
    filtered = [r for r in all_rows if pat.search(r.get("title") or "")]
elif not show_all:
    filtered = [r for r in all_rows if not NOISE_PAT.search(r.get("title") or "")]
else:
    filtered = all_rows

print()
print("=" * 72)
print(f" CCTV 新闻联播 | {start_date} — {end_date} ({len(all_rows)} 总标题 → {len(filtered)} 筛后)")
if grep_pat:
    print(f" 关键词: {grep_pat}")
elif not show_all:
    print(" 已过滤: 外交慰问/常规会议/天气/国际国内快讯 (--all 可看全部)")
print("=" * 72)

if not filtered:
    print("\n  无匹配条目. 若关键词太窄, 试试 --all 或放宽 --grep.")
    sys.exit(0)

# Group by date, print.
from collections import OrderedDict
by_date = OrderedDict()
for r in filtered:
    by_date.setdefault(r["date"], []).append(r["title"])

for d, titles in by_date.items():
    d_fmt = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    print(f"\n[{d_fmt}]")
    for t in titles:
        print(f"  · {t}")

print()
PY
