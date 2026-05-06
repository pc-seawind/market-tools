#!/usr/bin/env bash
# flows.sh — 机构资金动作分析(A 股)。
#
# 五维度 diligence 之外的"第六维":smart money 在干什么。对判断
# "抱团是否瓦解/机构是在抛还是在买"至关重要 —— 这类事情在消息面出来前
# 往往先体现在持股变化里(尤其是北向 + 公募重仓股的季度持股比例)。
#
# 本脚本组合两个 tushare 免费 API:
#   1. top10_floatholders —— 前十大流通股东,按季度归档,能做 QoQ 对比
#   2. moneyflow_hsgt     —— 北向/南向资金总览,作为大盘资金面背景
#
# Usage:
#   flows.sh <ticker> [quarters=4]
#
# 输出三段:
#   A. 近 N 期前十大流通股东对比(显示每期 top10 + 变化)
#   B. 机构类别聚合 —— 按"北向 / 公募 / 险资 / 私募 / 其他机构 / 自然人"归类,
#      计算每季度占流通股比例,看谁在加仓谁在减仓
#   C. 最近 20 个交易日的北向资金净流入趋势(市场环境)
#
# A 股 sh/sz/bj, 港股/美股跳过(top10_floatholders A 股专用)。
#
# Example:
#   flows.sh sz300308        # 中际旭创 —— 看公募抱团是否松动
#   flows.sh sh600519 quarters=6

set -euo pipefail

ticker="${1:-}"
quarters="${2:-4}"
quarters="${quarters#quarters=}"

if [[ -z "$ticker" || "$ticker" == "-h" || "$ticker" == "--help" ]]; then
    cat >&2 <<'EOF'
usage: flows.sh <ticker> [quarters=4]

ticker shapes:
  sh600519 / sz000001 / bj831168  →  A-share only (HK/US 无对应免费 API)

output:
  A. 前十大流通股东季度对比 (QoQ)
  B. 机构类别聚合变化 (北向/公募/险资/私募/自然人)
  C. 最近 20 日北向资金总览 (市场环境)

env: TUSHARE_TOKEN required.
EOF
    exit 2
fi

if ! [[ "$quarters" =~ ^[0-9]+$ ]] || (( quarters < 2 || quarters > 12 )); then
    echo "bad quarters=$quarters (expected 2..12)" >&2; exit 2
fi

# Route ticker → ts_code
ticker_lower="${ticker,,}"
case "$ticker_lower" in
    sh[0-9]*) ts_code="${ticker_lower#sh}.SH" ;;
    sz[0-9]*) ts_code="${ticker_lower#sz}.SZ" ;;
    bj[0-9]*) ts_code="${ticker_lower#bj}.BJ" ;;
    hk[0-9]*)
        echo "ERROR: HK 无免费的 top10_floatholders API. 跳过。" >&2; exit 2 ;;
    *)
        echo "unrecognized ticker shape: $ticker (expect sh/sz/bj)" >&2; exit 2 ;;
esac

here="$(dirname "$(readlink -f "$0")")"

exec python3 - "$ts_code" "$quarters" "$here" <<'PY'
import csv, subprocess, sys, datetime, re
from collections import defaultdict, OrderedDict

ts_code  = sys.argv[1]
quarters = int(sys.argv[2])
here     = sys.argv[3]

def tushare(api, **params):
    args = ["python3", f"{here}/tushare.py", api]
    for k, v in params.items():
        if k == "fields": args.append(f"--fields={v}")
        else:             args.append(f"{k}={v}")
    args.append("--csv")
    out = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        print(f"[WARN] tushare {api} failed: {out.stderr.strip()[-200:]}", file=sys.stderr)
        return []
    return list(csv.DictReader(out.stdout.splitlines()))

# ------------------------------------------------------------------
# A. 前十大流通股东多季度对比
# ------------------------------------------------------------------
# tushare 默认返回历史全量,我们取最近 N 期。
holders = tushare("top10_floatholders", ts_code=ts_code,
                  fields="ts_code,end_date,holder_name,hold_amount,hold_ratio")

# 按 end_date 分组
by_quarter = OrderedDict()
for h in holders:
    q = h.get("end_date", "")
    if q: by_quarter.setdefault(q, []).append(h)

# 按日期 desc 排序,取最近 N 期
recent_qs = sorted(by_quarter.keys(), reverse=True)[:quarters]

print()
print("=" * 72)
print(f" {ts_code}  机构资金动作分析 (最近 {len(recent_qs)} 期)")
print("=" * 72)

def classify_holder(name):
    """粗分类: 北向/公募/险资/外资/私募/产业资本/自然人/其他"""
    name = name.replace(" ", "")
    if "香港中央结算" in name: return "北向"
    if any(k in name for k in ["开放式指数证券投资基金","ETF","证券投资基金","混合型","灵活配置","QDII"]):
        return "公募"
    if "保险" in name: return "险资"
    if any(k in name for k in ["MORGANSTANLEY","GOLDMANSACHS","UBS","JPMORGAN","BARCLAYS","CREDITSUISSE","CITIGROUP","DEUTSCHEBANK","Lumiza"]):
        return "外资"
    if "社保" in name or "养老" in name: return "社保"
    if "年金" in name: return "年金"
    if any(k in name for k in ["私募","投资合伙","合伙企业"]):
        return "私募/合伙"
    if any(k in name for k in ["投资控股","集团","控股","有限公司"]):
        return "产业资本"
    if re.match(r"^[一-龥]{2,4}$", name):  # 中文 2-4 字 = 自然人
        return "自然人"
    return "其他"

# 逐季度打印 top10
print("\n━━ A. 前十大流通股东  (hold_ratio = 占流通股%)━━")
for q in recent_qs:
    rows = sorted(by_quarter[q], key=lambda r: -float(r.get("hold_ratio") or 0))
    total = sum(float(r.get("hold_ratio") or 0) for r in rows)
    print(f"\n[{q}]  Top10 合计持股: {total:.2f}%")
    for r in rows[:10]:
        name = (r.get("holder_name") or "")[:36]
        pad  = " " * max(0, 36 - len(name))
        cat  = classify_holder(r.get("holder_name") or "")
        ratio = float(r.get("hold_ratio") or 0)
        print(f"  {name}{pad}  [{cat:<8}]  {ratio:>6.3f}%")

# ------------------------------------------------------------------
# B. 机构类别聚合 — 看谁在加仓减仓
# ------------------------------------------------------------------
print("\n━━ B. 按机构类别聚合的持股比例变化 (QoQ)━━")
cats_by_q = OrderedDict()
for q in reversed(recent_qs):  # chronological for display
    total_by_cat = defaultdict(float)
    for r in by_quarter[q]:
        cat = classify_holder(r.get("holder_name") or "")
        total_by_cat[cat] += float(r.get("hold_ratio") or 0)
    cats_by_q[q] = total_by_cat

all_cats = set()
for d in cats_by_q.values(): all_cats.update(d.keys())

# 固定顺序(重点机构在前)
ordered_cats = ["北向","公募","外资","险资","社保","年金","私募/合伙","产业资本","自然人","其他"]
ordered_cats = [c for c in ordered_cats if c in all_cats]

# 表头
qs_list = list(cats_by_q.keys())
header = "  类别".ljust(14) + "".join(f"{q:>10}" for q in qs_list) + "     QoQ变化"
print()
print(header)
print("  " + "-" * (len(header) - 2))
for cat in ordered_cats:
    row = f"  {cat:<10}"
    vals = [cats_by_q[q].get(cat, 0.0) for q in qs_list]
    for v in vals:
        row += f"{v:>9.2f}%"
    # QoQ: 最后一期减最新期(即 latest vs 3 quarters ago)
    if len(vals) >= 2:
        delta = vals[-1] - vals[0]
        arrow = "↑" if delta > 0.05 else ("↓" if delta < -0.05 else "→")
        row += f"     {arrow}{delta:+.2f}pp"
    print(row)

print()
# Insight 文案:
delta_beixiang = cats_by_q[qs_list[-1]].get("北向", 0) - cats_by_q[qs_list[0]].get("北向", 0)
delta_gongmu  = cats_by_q[qs_list[-1]].get("公募", 0) - cats_by_q[qs_list[0]].get("公募", 0)
delta_total   = sum(cats_by_q[qs_list[-1]].values()) - sum(cats_by_q[qs_list[0]].values())

print("  💡 速读信号:")
def verdict(delta, name):
    if abs(delta) < 0.1: return f"{name} 基本持平 ({delta:+.2f}pp)"
    if delta > 0:        return f"{name} **加仓** {delta:+.2f}pp ✅"
    return                      f"{name} **减仓** {delta:+.2f}pp ⚠️"
print(f"     · {verdict(delta_beixiang, '北向')}")
print(f"     · {verdict(delta_gongmu,  '公募')}")
print(f"     · Top10 合计: {verdict(delta_total, '总持股')}")

# ------------------------------------------------------------------
# C. 最近 20 日北向资金大盘
# ------------------------------------------------------------------
print("\n━━ C. 最近 20 交易日北向资金 (大盘环境) ━━")
end_date   = datetime.date.today().strftime("%Y%m%d")
start_date = (datetime.date.today() - datetime.timedelta(days=45)).strftime("%Y%m%d")
hsgt = tushare("moneyflow_hsgt", start_date=start_date, end_date=end_date,
               fields="trade_date,hgt,sgt,north_money,south_money")
if hsgt:
    # tushare moneyflow_hsgt: hgt/sgt/north_money 字段实际单位是 万元 (doc 说百万但数据不符)
    # 除以 1e4 转为亿
    hsgt = hsgt[:20]  # 最近 20 日
    total_north = sum(float(r["north_money"] or 0) for r in hsgt) / 1e4
    avg_north   = total_north / len(hsgt) if hsgt else 0
    pos_days    = sum(1 for r in hsgt if float(r["north_money"] or 0) > 0)
    print(f"\n  {'日期':<10} {'沪股通':>11} {'深股通':>11} {'北向合计':>12}")
    print("  " + "-" * 48)
    for r in hsgt[:10]:  # 只显示最近 10 日,避免刷屏
        hgt_yi  = float(r['hgt'] or 0) / 1e4
        sgt_yi  = float(r['sgt'] or 0) / 1e4
        nm_yi   = float(r['north_money'] or 0) / 1e4
        print(f"  {r['trade_date']:<10} {hgt_yi:>8.1f} 亿 "
              f"{sgt_yi:>8.1f} 亿 {nm_yi:>9.1f} 亿")
    print(f"\n  20 日总览: 累计净流入 {total_north:>6.0f} 亿 | "
          f"日均 {avg_north:>5.1f} 亿 | 净流入天数 {pos_days}/20")
else:
    print("  [无北向资金数据]")
print()
PY
