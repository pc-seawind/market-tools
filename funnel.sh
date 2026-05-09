#!/usr/bin/env bash
# funnel.sh — 多轮漏斗式选股流程 (5500 → 300 → 80 → 25 → 8).
#
# 为什么需要这个工具:
#   screen.sh 是"单轮阈值筛选", 因子权重不灵活 (PE/ROE/涨幅一刀切).
#   实际投研 pipeline 应该是多轮渐进收敛, 每轮启用不同因子:
#     Round 1 — 基础盘, 排除不合格 → 流动性 / 市值 / 非 ST
#     Round 2 — **资金动向提前**  → smart money 有迹象 (不看涨幅)
#     Round 3 — 基本面             → ROE / 营收增速 / 估值合理
#     Round 4 — 主题一致性         → 属于 concepts 池 / 行业靠前
#
# 关键设计: Round 2 把 **资金动向** 作为最早期的精细因子,
#   替代传统的"涨幅排序". 避免漏过 "估值低位 + 机构悄悄建仓"
#   的 deep value 标的, 不做击鼓传花式追涨.
#
# Usage:
#   funnel.sh [--deep] [--final=N] [--round1=N] [--preset=NAME] [--group-by=concept|industry]
#
# Options:
#   --deep             启用 Round 2 的 per-stock API (top10_floatholders)
#                      对 Round 1 候选做真实机构资金动向检测. 慢 2-3 min
#                      但能筛出外资/公募实际加仓的股票.
#   --final=N          最终收敛到 N 只 (默认 8-10)
#   --round1=N         Round 1 保留上限 (默认 300)
#   --preset=balanced  balanced (默认) / value / growth
#                        balanced: 均衡 (当前默认)
#                        value:    降低 Round 2 动能要求, 更看估值
#                        growth:   Round 3 放宽估值, 更看增速
#   --group-by=KEY     最终输出按 KEY 分组: concept (概念池) 或 industry (申万 L1)
#                      (默认 concept)
#
# Examples:
#   funnel.sh                              # 默认 quick mode, balanced preset
#   funnel.sh --deep --final=6             # 深度模式, 收敛到 6 只
#   funnel.sh --preset=value               # 价值偏好
#   funnel.sh --round1=500 --final=15      # 更宽的初筛, 更多 final
#
# Env: TUSHARE_TOKEN required.

set -uo pipefail

here="$(dirname "$(readlink -f "$0")")"

exec python3 - "$here" "$@" <<'PY'
import csv, datetime, math, statistics, subprocess, sys, time
from collections import defaultdict, Counter

here = sys.argv[1]
raw_args = sys.argv[2:]

# ---- argparse ----
deep_mode = False
final_n = 8
round1_n = 300
preset = "balanced"
group_by = "concept"

for arg in raw_args:
    if arg in ("-h", "--help"):
        with open(f"{here}/funnel.sh") as f:
            lines = f.readlines()
        sys.stderr.write("".join(l[2:] if l.startswith("# ") else l[1:] if l.startswith("#") else ""
                                 for l in lines[1:50]))
        sys.exit(0)
    elif arg == "--deep":
        deep_mode = True
    elif arg.startswith("--final="):
        final_n = int(arg[8:])
    elif arg.startswith("--round1="):
        round1_n = int(arg[9:])
    elif arg.startswith("--preset="):
        preset = arg[9:]
    elif arg.startswith("--group-by="):
        group_by = arg[11:]
    else:
        sys.stderr.write(f"unknown arg: {arg}\n"); sys.exit(2)

# Preset 配置
#
# r1w 上下限刻度基准: 科创板/创业板/北交所单日涨跌停 ±20%, 主板 ±10%.
# 1 个涨停 = +20% (创业板/科创板) 或 +10% (主板), 1W 只要卡在 "1 个板"
# 附近就不该过滤 — 那往往是启动信号. 真正的末期加速靠 grading.py
# 的 SELL_EXHAUSTION (1W>+15 from signals.py) / SELL_CONFIRMED (+25) /
# SELL_EXTREME (+35) 在 grade 层降级, 而不是在 Round 2 硬过滤.
#
# 所以 r1w_max 应该放在"允许 1 个涨停 + 轻微惯性延续"的位置 (~+30%),
# 让 grading 做真正的分级. r1w_min 类似, 允许 1 个创业板跌停 (-20%).
if preset == "value":
    # 价值偏好: Round 2 放宽量价要求, Round 3 严格估值
    cfg_r2_turn_boost = 1.0    # 换手率提升门槛放宽
    cfg_r2_r1w_min   = -22    # deep value 常在跌势中, 允许 1 个创业板跌停
    cfg_r2_r1w_max   = 22     # 允许 1 个创业板涨停启动
    cfg_r3_pe_max    = 25      # PE 严格 (value 偏好便宜)
    cfg_r3_pb_max    = 5
    cfg_r3_dv_min    = 0       # 股息不强求
elif preset == "growth":
    # 成长偏好: Round 3 放宽估值, 看增长率
    cfg_r2_turn_boost = 1.3
    cfg_r2_r1w_min   = -15     # growth 不捡深跌票, 只允许单日创业板跌停以内
    cfg_r2_r1w_max   = 35      # 放到 SELL_EXTREME 阈值, 再往上靠 grading 降级
    cfg_r3_pe_max    = 100     # PE 宽松
    cfg_r3_pb_max    = 20
    cfg_r3_dv_min    = -1      # 股息无要求
else:  # balanced (默认)
    cfg_r2_turn_boost = 1.2
    cfg_r2_r1w_min   = -22     # 允许 1 个创业板/科创板跌停
    cfg_r2_r1w_max   = 30      # 允许 1 个创业板涨停 + 小幅惯性 (>+30 靠 grading 降级)
    cfg_r3_pe_max    = 60
    cfg_r3_pb_max    = 12
    cfg_r3_dv_min    = 0

# ---- tushare helpers ----
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
        sys.stderr.write(f"[WARN] tushare {api}: {out.stderr.strip()[-150:]}\n"); return []
    return list(csv.DictReader(out.stdout.splitlines()))

def to_float(x, d=None):
    try: return float(x) if x not in (None, "", "None") else d
    except ValueError: return d

def ret_pct(cur, old):
    c1 = to_float(cur); c0 = to_float(old)
    if c1 is None or c0 is None or c0 == 0: return None
    return (c1 - c0) / c0 * 100

# ---- Load concept pool ----
sys.path.insert(0, here)
try:
    from concepts_data import CONCEPTS
except ImportError:
    CONCEPTS = {}

code_to_concepts = defaultdict(list)
for concept, stocks in CONCEPTS.items():
    for code, _ in stocks:
        code_to_concepts[code].append(concept)

# ---- 准备日期 ----
today   = datetime.date.today().strftime("%Y%m%d")
past120 = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y%m%d")
cal = tushare("trade_cal", exchange="SSE", start_date=past120, end_date=today,
              fields="cal_date,is_open")
open_days = sorted([r["cal_date"] for r in cal if r.get("is_open") == "1"], reverse=True)
if not open_days:
    sys.stderr.write("ERROR: trade_cal 无数据\n"); sys.exit(4)

latest  = open_days[0]
d_5d    = open_days[5]  if len(open_days) > 5  else open_days[-1]
d_20d   = open_days[20] if len(open_days) > 20 else open_days[-1]
d_60d   = open_days[60] if len(open_days) > 60 else open_days[-1]

# ====================================================================
# Header
# ====================================================================
print()
print("╔════════════════════════════════════════════════════════════════════════╗")
print(f"║  🌊 FUNNEL SELECTION  ·  {preset.upper()} preset  ·  mode={'DEEP' if deep_mode else 'QUICK'}".ljust(73) + "║")
print(f"║  数据日: {latest}  (lookback: 5d→{d_5d}, 20d→{d_20d}, 60d→{d_60d})".ljust(73) + "║")
print("╚════════════════════════════════════════════════════════════════════════╝")

# ====================================================================
# Round 0: 拉全市场基础数据
# ====================================================================
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  ROUND 0: 全市场扫描")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

print("  拉 daily_basic (估值 + 市值 + 换手率)...", file=sys.stderr)
db = tushare("daily_basic", trade_date=latest,
             fields="ts_code,close,turnover_rate,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv")
# Fallback: 当日数据如果还没更新 (e.g. 收盘后 15 min 内), 退回前一个交易日
if len(db) < 100 and len(open_days) > 1:
    sys.stderr.write(f"  [WARN] {latest} 数据不全 ({len(db)} 行), 退回 T-1={open_days[1]}\n")
    latest = open_days[1]
    d_5d   = open_days[6]  if len(open_days) > 6  else open_days[-1]
    d_20d  = open_days[21] if len(open_days) > 21 else open_days[-1]
    d_60d  = open_days[61] if len(open_days) > 61 else open_days[-1]
    db = tushare("daily_basic", trade_date=latest,
                 fields="ts_code,close,turnover_rate,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv")
db_by_code = {r["ts_code"]: r for r in db}

# 拉 daily for latest + 5d + 20d (for returns + amount)
print(f"  拉 daily × 4 个日期...", file=sys.stderr)
daily_latest = {r["ts_code"]: r for r in
                tushare("daily", trade_date=latest, fields="ts_code,close,amount,vol")}
daily_5d = {r["ts_code"]: r for r in
            tushare("daily", trade_date=d_5d, fields="ts_code,close,amount")}
daily_20d = {r["ts_code"]: r for r in
             tushare("daily", trade_date=d_20d, fields="ts_code,close,amount")}
daily_60d = {r["ts_code"]: r for r in
             tushare("daily", trade_date=d_60d, fields="ts_code,close")}

# 拉 stock_basic for name/industry/list_date
print(f"  拉 stock_basic...", file=sys.stderr)
basic = tushare("stock_basic", list_status="L",
                fields="ts_code,name,industry,market,list_date")
basic_by_code = {r["ts_code"]: r for r in basic}

# 合并所有 metrics
records = {}
for ts_code, d in db_by_code.items():
    b = basic_by_code.get(ts_code, {})
    dl = daily_latest.get(ts_code, {})
    d5 = daily_5d.get(ts_code, {})
    d20 = daily_20d.get(ts_code, {})
    d60 = daily_60d.get(ts_code, {})

    cur_close = to_float(dl.get("close")) or to_float(d.get("close"))
    if cur_close is None: continue

    amt_cur = (to_float(dl.get("amount")) or 0) / 1e5  # 千元→亿
    amt_5d  = (to_float(d5.get("amount")) or 0) / 1e5
    amt_20d = (to_float(d20.get("amount")) or 0) / 1e5

    records[ts_code] = {
        "ts_code": ts_code,
        "name": b.get("name", ts_code),
        "industry": b.get("industry", "?"),
        "list_date": b.get("list_date", "20000101"),
        "close": cur_close,
        "pe_ttm": to_float(d.get("pe_ttm")),
        "pb": to_float(d.get("pb")),
        "ps_ttm": to_float(d.get("ps_ttm")),
        "dv_ttm": to_float(d.get("dv_ttm"), 0.0),
        "tor":  to_float(d.get("turnover_rate"), 0.0),
        "mv_yi": (to_float(d.get("total_mv")) or 0) / 1e4,
        "circ_mv_yi": (to_float(d.get("circ_mv")) or 0) / 1e4,
        "amt_cur":  amt_cur,
        "amt_5d":   amt_5d,
        "amt_20d":  amt_20d,
        "r1w": ret_pct(cur_close, d5.get("close")),
        "r1m": ret_pct(cur_close, d20.get("close")),
        "r3m": ret_pct(cur_close, d60.get("close")),
    }

print(f"  → 全市场有效记录: {len(records)} 只")

# ====================================================================
# Round 1: 基础盘 (流动性 + 市值 + 非 ST/新股)
# ====================================================================
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  ROUND 1: 基础盘 (流动性 / 市值 / 排除 ST 新股)")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  逻辑: 日成交 ≥ 2 亿 (机构能进场) + 总市值 ≥ 50 亿 (抗操纵) +")
print(f"        排除 ST/退市 + 排除 60 天内次新 (价格未稳定)")

r1 = {}
for code, r in records.items():
    # ST / 退
    if "ST" in r["name"] or "退" in r["name"]: continue
    # 流动性
    if r["amt_cur"] < 2: continue
    # 市值
    if r["mv_yi"] < 50: continue
    # 次新 (60 天内上市)
    try:
        ld = datetime.datetime.strptime(r["list_date"], "%Y%m%d").date()
        if (datetime.date.today() - ld).days < 60: continue
    except Exception:
        pass
    r1[code] = r

# 按市值取前 round1_n (因为日成交 + 市值 其实过滤出来可能 > 300, 限制到上限)
r1_sorted = sorted(r1.values(), key=lambda r: -r["mv_yi"])[:round1_n]
r1 = {r["ts_code"]: r for r in r1_sorted}
print(f"  → 剩余: {len(r1)} 只  (目标 ≤ {round1_n})")

# ====================================================================
# Round 2: 资金动向 (smart money 信号, 不看涨幅大小)
# ====================================================================
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  ROUND 2: 资金动向 (smart money 信号)  {'[DEEP: + 北向持股变化]' if deep_mode else '[QUICK: 量价代理信号]'}")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  逻辑 (不看涨幅大小, 看资金行为):")
print(f"    1. 关注度上升: 最近成交金额 > 过去 20 日均值 * {cfg_r2_turn_boost}")
print(f"    2. 量价配合: 1W 涨幅 ∈ [{cfg_r2_r1w_min}%, {cfg_r2_r1w_max}%] (排除暴涨暴跌)")
print(f"    3. 不在长期下跌: 3M 涨幅 > -30% (排除崩盘股)")
if deep_mode:
    print(f"    4. [DEEP] 北向持股: 最近 2 季度 Δ ≥ +0.3pp  OR  近 1 季度 > 0")

def round2_quick_pass(r):
    # 关注度: 今日/近 5日均 vs 20日均
    baseline = r["amt_20d"]
    if baseline <= 0: return False
    if r["amt_cur"] / baseline < cfg_r2_turn_boost: return False
    # 量价配合
    if r["r1w"] is None or r["r1w"] < cfg_r2_r1w_min or r["r1w"] > cfg_r2_r1w_max: return False
    # 排除长期崩盘
    if r["r3m"] is not None and r["r3m"] < -30: return False
    return True

r2_quick = [r for r in r1.values() if round2_quick_pass(r)]
print(f"  → Quick mode 剩余: {len(r2_quick)} 只")

if deep_mode and r2_quick:
    # 对每只 Round 2 候选调 top10_floatholders
    # 限制上限 (避免极端 rate limit)
    candidates = r2_quick[:120]  # 最多 120 只做 per-stock 调用
    print(f"  [DEEP] 对 {len(candidates)} 只做 top10_floatholders...", file=sys.stderr)
    def get_beixiang_delta(ts_code):
        rows = tushare("top10_floatholders", ts_code=ts_code,
                       fields="end_date,holder_name,hold_ratio", timeout=30)
        if not rows: return None, None
        # 按 end_date 聚合
        by_q = defaultdict(list)
        for r in rows:
            q = r.get("end_date")
            if q: by_q[q].append(r)
        qs = sorted(by_q.keys(), reverse=True)[:4]
        if len(qs) < 2: return None, None
        def beixiang_ratio(rows_in_q):
            return sum(to_float(r.get("hold_ratio"), 0) or 0
                       for r in rows_in_q
                       if "香港中央结算" in (r.get("holder_name") or ""))
        latest_bx = beixiang_ratio(by_q[qs[0]])
        oldest_bx = beixiang_ratio(by_q[qs[-1]])
        return latest_bx, latest_bx - oldest_bx

    r2_deep = []
    for i, r in enumerate(candidates, 1):
        latest_bx, delta = get_beixiang_delta(r["ts_code"])
        if i % 20 == 0:
            print(f"    [{i}/{len(candidates)}] {r['name']}", file=sys.stderr)
        if delta is None: continue  # 无北向数据, 保守处理: 丢弃
        r["beixiang_latest"] = latest_bx
        r["beixiang_delta"] = delta
        # 条件: Δ ≥ +0.3pp  OR  Δ > 0 AND 最新 > 2%
        if delta >= 0.3 or (delta > 0 and latest_bx > 2):
            r2_deep.append(r)
    print(f"  → Deep mode (北向有加仓) 剩余: {len(r2_deep)} 只")
    r2 = r2_deep if r2_deep else r2_quick[:80]   # fallback
else:
    r2 = r2_quick[:80]   # 上限 80 避免 Round 3 过载

print(f"  → Round 2 最终: {len(r2)} 只")

# ====================================================================
# Round 3: 基本面 + 估值
# ====================================================================
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  ROUND 3: 基本面 + 估值")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  逻辑 (preset={preset}):")
print(f"    PE_TTM > 0 且 ≤ {cfg_r3_pe_max} (非亏损, 估值不极端)")
print(f"    PB ≤ {cfg_r3_pb_max}")
print(f"    股息率 ≥ {cfg_r3_dv_min}%")

def round3_pass(r):
    if r["pe_ttm"] is None or r["pe_ttm"] <= 0 or r["pe_ttm"] > cfg_r3_pe_max: return False
    if r["pb"] is None or r["pb"] > cfg_r3_pb_max: return False
    if r["dv_ttm"] is not None and r["dv_ttm"] < cfg_r3_dv_min: return False
    return True

r3 = [r for r in r2 if round3_pass(r)]
print(f"  → 剩余: {len(r3)} 只")

# ====================================================================
# Round 4: 主题一致性 + 行业分布
# ====================================================================
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  ROUND 4: 主题一致性 + 行业活跃度")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  逻辑: 属于 concepts 池 (主题标签)  OR  行业是活跃行业 (1M 均涨 top 50%)")

# 计算行业活跃度 (根据 R1 全量的 1M 均值)
by_ind = defaultdict(list)
for r in r1.values():
    if r["r1m"] is not None:
        by_ind[r["industry"]].append(r["r1m"])
ind_rank = sorted(
    [(ind, sum(vs)/len(vs)) for ind, vs in by_ind.items() if vs],
    key=lambda x: -x[1]
)
active_inds = set(ind for ind, _ in ind_rank[:len(ind_rank)//2])

def round4_tag(r):
    """返回标签 list. 至少有一个才过关."""
    tags = []
    for c in code_to_concepts.get(r["ts_code"], []):
        tags.append(("concept", c))
    if r["industry"] in active_inds:
        tags.append(("industry", r["industry"]))
    return tags

r4 = []
for r in r3:
    tags = round4_tag(r)
    if tags:
        r["tags"] = tags
        r4.append(r)
print(f"  → 剩余: {len(r4)} 只")

# ====================================================================
# Final: 按 group_by 分组, 收敛到 final_n
# ====================================================================
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  FINAL: 收敛到 {final_n} 只  (按 {group_by} 分组)")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

# 按 group_by 分组, 每组保留 top 股
if group_by == "concept":
    def key_of(r):
        concepts = [c for kind, c in r["tags"] if kind == "concept"]
        return concepts[0] if concepts else (r["industry"] + " [行业]")
elif group_by == "industry":
    def key_of(r):
        return r["industry"]
else:
    def key_of(r):
        return "all"

groups = defaultdict(list)
for r in r4:
    groups[key_of(r)].append(r)

# 每组按 "1M 涨幅 + 换手率提升" 综合打分选 top (排除只看涨幅)
def final_score(r):
    """最终打分: 主要看机构迹象 + 基本面 + 适度动能."""
    score = 0
    # 量价迹象 (不是纯涨幅)
    if r["amt_20d"] > 0:
        boost = r["amt_cur"] / r["amt_20d"]
        score += min(boost, 3) * 10  # cap 3x 防止极端值主导
    # 基本面
    if r["pe_ttm"] and 0 < r["pe_ttm"] < 40:
        score += (40 - r["pe_ttm"]) * 0.5  # PE 越低分越高
    if r["dv_ttm"] and r["dv_ttm"] > 0:
        score += r["dv_ttm"] * 2            # 股息率加分
    # 深度模式额外考虑北向加仓
    if r.get("beixiang_delta"):
        score += r["beixiang_delta"] * 5
    # 避免追高: 过去 3M 如果 > +80% 扣分
    if r["r3m"] and r["r3m"] > 80:
        score -= (r["r3m"] - 80) * 0.2
    return score

# 先按组内 score 排序
for k in groups:
    groups[k].sort(key=lambda r: -final_score(r))

# 每组最多 2 只, 然后按 global score 收敛到 final_n
final_pool = []
for k, rs in sorted(groups.items(), key=lambda kv: -sum(final_score(r) for r in kv[1]) / max(len(kv[1]), 1)):
    for r in rs[:2]:
        final_pool.append(r)

final_pool.sort(key=lambda r: -final_score(r))
final_pool = final_pool[:final_n]

# ═══ 综合推荐度分级 (用 grading.py 统一逻辑) ═══
try:
    import sector_health as _sh_mod
    import grading as _grading
    try:
        from concepts_data import CONCEPTS
    except ImportError:
        CONCEPTS = {}

    # 从 records.r1m 反推 concept ranking (免二次 API)
    code_to_r1m = {code: r["r1m"] for code, r in records.items()
                   if r.get("r1m") is not None}
    _concept_ranking = []
    for _cn, _stks in CONCEPTS.items():
        _rs = [code_to_r1m[c] for c, _ in _stks if c in code_to_r1m]
        _avg = sum(_rs)/len(_rs) if _rs else None
        _concept_ranking.append((_cn, _avg, _stks))
    _concept_ranking.sort(key=lambda x: -(x[1] if x[1] is not None else -999))

    _sh_index = _sh_mod.build_index(
        daily_latest_map=daily_latest,
        daily_20d_map=daily_20d,
        stock_basic_map=basic_by_code,
        concept_ranking=_concept_ranking,
    )

    # 加载基本面数据 (从 Parquet, 无数据的股不在 map 里)
    _fund_map = {}
    try:
        _fund_map = _grading.load_fundamentals_map([r["ts_code"] for r in final_pool])
    except Exception as _fe:
        sys.stderr.write(f"  [WARN] 加载基本面失败, 跳过基本面维度: {_fe}\n")

    for _r in final_pool:
        sh_info = _sh_index.get(_r["ts_code"], {})
        fund_info = _fund_map.get(_r["ts_code"])
        _grading.compute_grade(_r, sh_info, style="balanced", fund_info=fund_info)
    _grading_ok = True
except Exception as _e:
    sys.stderr.write(f"  [WARN] 推荐度评级失败: {_e}\n")
    import traceback; traceback.print_exc(file=sys.stderr)
    _grading_ok = False

# 渲染: 按推荐度分组
def fmt_pct(v, w=7):
    if v is None: return "n/a".rjust(w)
    return f"{v:+{w-1}.1f}%"

if _grading_ok:
    print()
    print(_grading.render_group(final_pool, show_tags=True))
else:
    # fallback: 旧版分组渲染
    final_groups = defaultdict(list)
    for r in final_pool: final_groups[key_of(r)].append(r)
    print()
    for group, stocks in sorted(final_groups.items(), key=lambda kv: -sum(final_score(r) for r in kv[1])):
        print(f"  ▣ {group}  ({len(stocks)} 只)")
        for r in stocks:
            print(f"      {r['ts_code']:<12} {r['name']:<8}  "
                  f"PE={r['pe_ttm'] or 0:>5.1f}  "
                  f"PB={r['pb'] or 0:>5.1f}  "
                  f"市值={r['mv_yi']:>5.0f}亿  "
                  f"1M={fmt_pct(r['r1m']):>7}  "
                  f"量比={r['amt_cur']/max(r['amt_20d'],0.01):.1f}x")
        print()

# ====================================================================
# Footer: 下一步
# ====================================================================
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  💡 下一步 — 把 final 名单送入对比 + 深度 diligence:")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

# 生成 top 5 的 compare.sh 命令
top5_codes = [r["ts_code"] for r in final_pool[:5]]
compare_args = " ".join(
    f"{code.split('.')[1].lower()}{code.split('.')[0]}"
    for code in top5_codes
)
print(f"\n  对 final top 5 做横向对比:")
print(f"    bash {here}/compare.sh {compare_args}")

if final_pool:
    dili_ticker = f"{final_pool[0]['ts_code'].split('.')[1].lower()}{final_pool[0]['ts_code'].split('.')[0]}"
    print(f"\n  对 top 1 做六维深度分析:")
    print(f"    bash {here}/diligence.sh {dili_ticker}")
print()
PY
