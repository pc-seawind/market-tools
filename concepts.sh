#!/usr/bin/env bash
# concepts.sh — 主题概念板块热度排名 + 概念详情 + 龙头对比联动.
#
# 为什么需要这个工具:
#   申万一级 31 个行业是"传统工业分类", 抓不到"存储/CoWoS/HBM/算力
#   租赁/英伟达链/人形机器人"这些真正的 **市场主题概念** (一个主题
#   可能跨多个行业). tushare 的概念指数 API (ths_index/dc_index/
#   kpl_concept) 都需付费权限, 我们 token 没有.
#
#   所以本工具采用 "手工维护概念池 + 已有免费 daily API 做本地聚合"
#   的 hybrid 方案:
#     - 概念成分股定义在 concepts_data.py (可独立维护, 不改代码)
#     - 用 daily trade_date= 批量 API (3 次调用返回全市场 3 个日期)
#       做本地聚合, 算出每个概念的 1W/1M 涨幅 + 总成交 + top 股票
#
# Usage:
#   concepts.sh                           # 默认: 全部概念热度排名
#   concepts.sh --top=5                   # 只看 top 5 概念
#   concepts.sh --topic=AI芯片            # 看某个概念内成分股明细
#   concepts.sh --topic=ai                # 支持模糊匹配 (子串)
#   concepts.sh --compare-top=4           # 自动对 top 4 概念的龙头做 compare.sh
#   concepts.sh --list                    # 列出可用概念名
#
# Env: TUSHARE_TOKEN required.
# Deps: bash + python3 (stdlib) + market-tools/concepts_data.py

set -uo pipefail

here="$(dirname "$(readlink -f "$0")")"

# 直接 exec python 做主逻辑 (argparse + tushare 调用 + 聚合渲染)
exec python3 - "$here" "$@" <<'PY'
import csv, datetime, subprocess, sys
from collections import defaultdict

here = sys.argv[1]
raw_args = sys.argv[2:]

# Import concepts_data as a module (relative to `here`)
sys.path.insert(0, here)
try:
    from concepts_data import CONCEPTS, all_concepts, stocks_of, find_concept
except ImportError as e:
    sys.stderr.write(f"ERROR: concepts_data.py 加载失败: {e}\n"); sys.exit(3)

# ---- argparse (manual, simple) ----
mode = "rank"   # rank / topic / compare-top / list
top = None
topic_q = None
compare_top = None

for arg in raw_args:
    if arg in ("-h", "--help"):
        print(__doc__ if "__doc__" in dir() else "see script header for usage", file=sys.stderr)
        # Re-print SKILL header
        with open(f"{here}/concepts.sh") as f:
            lines = f.readlines()
        sys.stderr.write("".join(l[2:] if l.startswith("# ") else l[1:] if l.startswith("#") else ""
                                 for l in lines[1:40]))
        sys.exit(0)
    elif arg == "--list":
        mode = "list"
    elif arg.startswith("--top="):
        top = int(arg[6:])
    elif arg.startswith("--topic="):
        mode = "topic"
        topic_q = arg[8:]
    elif arg.startswith("--compare-top="):
        mode = "compare-top"
        compare_top = int(arg[14:])
    else:
        sys.stderr.write(f"unknown arg: {arg}\n"); sys.exit(2)

# ========= Mode: --list =========
if mode == "list":
    print(f"共 {len(CONCEPTS)} 个概念:\n")
    for c in all_concepts():
        stocks = stocks_of(c)
        print(f"  {c:<30} ({len(stocks)} 股)")
    sys.exit(0)

# ========= 共用: tushare call wrapper =========
def tushare(api, timeout=60, **params):
    args = ["python3", f"{here}/tushare.py", api]
    for k, v in params.items():
        if k == "fields": args.append(f"--fields={v}")
        else:             args.append(f"{k}={v}")
    args.append("--csv")
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"[WARN] tushare {api} timeout\n"); return []
    if out.returncode != 0:
        sys.stderr.write(f"[WARN] tushare {api} failed: {out.stderr.strip()[-200:]}\n"); return []
    return list(csv.DictReader(out.stdout.splitlines()))

def to_float(x, d=None):
    try: return float(x) if x not in (None, "", "None") else d
    except ValueError: return d

def ret_pct(cur, old):
    c1 = to_float(cur); c0 = to_float(old)
    if c1 is None or c0 is None or c0 == 0: return None
    return (c1 - c0) / c0 * 100

# ========= 共用: 拉全市场 3 日期 daily 数据 =========
def pull_market_data():
    """拉 3 个日期的全市场 daily, 返回 {ts_code: metrics}."""
    # 先用 trade_cal 算准确交易日
    today = datetime.date.today().strftime("%Y%m%d")
    past  = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y%m%d")
    cal = tushare("trade_cal", exchange="SSE", start_date=past, end_date=today,
                  fields="cal_date,is_open")
    open_days = sorted([r["cal_date"] for r in cal if r.get("is_open") == "1"], reverse=True)
    if not open_days:
        sys.stderr.write("ERROR: trade_cal 无数据\n"); sys.exit(4)

    latest   = open_days[0]
    d_1w_ago = open_days[5]  if len(open_days) > 5  else open_days[-1]
    d_1m_ago = open_days[20] if len(open_days) > 20 else open_days[-1]
    print(f"  数据日期: latest={latest}  1W→{d_1w_ago}  1M→{d_1m_ago}", file=sys.stderr)

    bars = {}
    for tag, d in [("cur", latest), ("1w", d_1w_ago), ("1m", d_1m_ago)]:
        print(f"  pulling daily trade_date={d} ({tag})...", file=sys.stderr)
        rows = tushare("daily", trade_date=d, fields="ts_code,close,amount")
        bars[tag] = {r["ts_code"]: r for r in rows}

    # Per-stock metrics
    stock_m = {}
    for code, cur_row in bars["cur"].items():
        m = {
            "close": to_float(cur_row.get("close")),
            "amt_yi": (to_float(cur_row.get("amount")) or 0) / 1e5,
            "r1w": ret_pct(cur_row.get("close"),
                           (bars["1w"].get(code) or {}).get("close")),
            "r1m": ret_pct(cur_row.get("close"),
                           (bars["1m"].get(code) or {}).get("close")),
        }
        stock_m[code] = m
    return latest, stock_m

# ========= Mode: --topic=X =========
if mode == "topic":
    matches = find_concept(topic_q) if not topic_q in CONCEPTS else [topic_q]
    if not matches:
        sys.stderr.write(f"ERROR: no concept matched '{topic_q}'\n"
                         f"  available: {', '.join(all_concepts())}\n")
        sys.exit(2)
    if len(matches) > 1:
        sys.stderr.write(f"[info] '{topic_q}' 模糊匹配到 {len(matches)} 个概念:\n")
        for m in matches: sys.stderr.write(f"  - {m}\n")
        sys.stderr.write(f"  (显示第一个匹配)\n\n")

    target = matches[0]
    stocks = stocks_of(target)

    latest, stock_m = pull_market_data()

    print()
    print("=" * 92)
    print(f" 📑 概念详情: {target}  ({len(stocks)} 只成分股)")
    print(f"    数据日: {latest}")
    print("=" * 92)
    print(f"  {'代码':<12}{'名称':<10}{'收盘':>9}{'日成交(亿)':>13}{'1W':>9}{'1M':>9}")
    print("  " + "-" * 65)
    def fmt_pct(v, w=8):
        if v is None: return "n/a".rjust(w)
        return f"{v:+{w-1}.1f}%"
    rows_out = []
    for code, name in stocks:
        m = stock_m.get(code, {})
        rows_out.append({
            "code": code, "name": name,
            "close": m.get("close"), "amt": m.get("amt_yi", 0),
            "r1w": m.get("r1w"), "r1m": m.get("r1m"),
        })
    # Sort by r1m desc
    rows_out.sort(key=lambda r: -(r["r1m"] or -1e9))
    for r in rows_out:
        cs = f"{r['close']:.2f}" if r["close"] else "n/a"
        print(f"  {r['code']:<12}{r['name']:<8}  {cs:>9} {r['amt']:>10.1f}  "
              f"{fmt_pct(r['r1w']):>7} {fmt_pct(r['r1m']):>7}")
    # 快速统计
    rs1m = [r["r1m"] for r in rows_out if r["r1m"] is not None]
    total_amt = sum(r["amt"] for r in rows_out)
    if rs1m:
        avg_r1m = sum(rs1m)/len(rs1m)
        med_r1m = sorted(rs1m)[len(rs1m)//2]
        print()
        print(f"  📊 概念统计: 1M 均涨 {avg_r1m:+.1f}% | 中位数 {med_r1m:+.1f}% | "
              f"总成交 {total_amt:.0f} 亿")
        # 自动建议 compare
        top3 = [r for r in rows_out[:3] if r["r1m"] is not None]
        if len(top3) >= 2:
            codes_fmt = " ".join(
                f"{r['code'].split('.')[1].lower()}{r['code'].split('.')[0]}" for r in top3
            )
            print(f"\n  💡 自动对比 top 3: bash {here}/compare.sh {codes_fmt}")
    print()
    sys.exit(0)

# ========= Mode: rank / compare-top (都需要先聚合) =========
latest, stock_m = pull_market_data()

concept_stats = []
for concept, stocks in CONCEPTS.items():
    rs1w, rs1m, amts, details = [], [], [], []
    for code, name in stocks:
        m = stock_m.get(code)
        if not m or m.get("r1m") is None:
            details.append((code, name, None, None, 0))
            continue
        rs1w.append(m["r1w"]) if m["r1w"] is not None else None
        rs1m.append(m["r1m"])
        amts.append(m["amt_yi"])
        details.append((code, name, m["r1w"], m["r1m"], m["amt_yi"]))
    concept_stats.append({
        "name": concept,
        "n_stocks": len([d for d in details if d[3] is not None]),
        "avg_r1w":    sum(rs1w)/len(rs1w)        if rs1w else None,
        "avg_r1m":    sum(rs1m)/len(rs1m)        if rs1m else None,
        "median_r1m": sorted(rs1m)[len(rs1m)//2] if rs1m else None,
        "total_amt_yi": sum(amts),
        "top_stocks":  sorted(details, key=lambda d: -(d[3] or -1e9))[:3],
    })

concept_stats.sort(key=lambda s: -(s["avg_r1m"] or -1e9))

if mode == "compare-top":
    # 挑前 compare_top 个概念, 每个概念取 top 1 股 (去重), 共最多 5 只
    selected_codes = []
    seen = set()
    for cs in concept_stats[:compare_top]:
        for code, name, _, r1m, _ in cs["top_stocks"]:
            if r1m is not None and code not in seen:
                seen.add(code)
                selected_codes.append((code, name, cs["name"]))
                break
        if len(selected_codes) >= 5: break

    print(f"\n  已选 top {len(selected_codes)} 概念龙头:")
    for code, name, concept in selected_codes:
        print(f"    {code}  {name:<10}  ← {concept}")

    compare_args = [
        f"{code.split('.')[1].lower()}{code.split('.')[0]}"
        for code, _, _ in selected_codes
    ]
    print(f"\n  🚀 正在调用 compare.sh {' '.join(compare_args)}...\n")

    # Call compare.sh
    cmd = ["bash", f"{here}/compare.sh"] + compare_args
    subprocess.run(cmd)
    sys.exit(0)

# ========= Default: rank 渲染 =========
display = concept_stats if top is None else concept_stats[:top]

def fmt_pct(v, w=8):
    if v is None: return "n/a".rjust(w)
    return f"{v:+{w-1}.1f}%"

print()
print("=" * 100)
print(f" 📊 概念板块热度排名  (数据日 {latest})")
print("=" * 100)
print(f"  {'排名':<4}{'概念':<26}{'n':>4}{'1W均':>8}{'1M均':>8}{'1M中位':>8}{'日成交(亿)':>12}  Top1")
print("  " + "-" * 90)
for i, cs in enumerate(display, 1):
    tag = "🔥" if i <= 3 else ("  " if i <= 7 else "🧊")
    top1 = cs["top_stocks"][0] if cs["top_stocks"] else None
    top1_str = ""
    if top1 and top1[3] is not None:
        top1_str = f"{top1[1]} ({fmt_pct(top1[3], 6)})"
    print(f"  {tag}{i:>2}. {cs['name']:<24} {cs['n_stocks']:>4} "
          f"{fmt_pct(cs['avg_r1w']):>7} "
          f"{fmt_pct(cs['avg_r1m']):>7} "
          f"{fmt_pct(cs['median_r1m']):>7} "
          f"{cs['total_amt_yi']:>10.0f}    "
          f"{top1_str}")

print()
print(f"  💡 下一步:")
print(f"     看某概念明细:         bash {here}/concepts.sh --topic='<概念名或关键词>'")
print(f"     top 概念龙头对比:     bash {here}/concepts.sh --compare-top=4")
print()
PY
