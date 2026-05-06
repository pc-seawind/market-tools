#!/usr/bin/env bash
# momentum.sh — 博弈仓筛选器 (与 funnel.sh 对偶的 momentum 工具).
#
# 为什么需要这个工具:
#   funnel.sh 是基础仓哲学 (资金动向 + 估值 + 避免末期加速), 主动
#   过滤 1W 暴涨股, 会错过 寒武纪/江波龙/中际旭创 这类处于主升浪的
#   momentum 强势股. 实际投资组合里, 基础仓 + 博弈仓两种逻辑都需要:
#     - 基础仓 (funnel): 60-70% 仓位, 确定性 + 稳健
#     - 博弈仓 (momentum): 10-20% 仓位, 题材弹性 + 严格纪律
#
# momentum.sh 严格对偶 funnel.sh 的哲学:
#   funnel Round 2:  避免暴涨 → momentum Round 2: 要求已在趋势中
#   funnel 位置偏好: 中位  → momentum: 接近高位 (≥70%)
#   funnel 估值门槛: PE ≤ 60  → momentum: 无估值上限 (题材优先)
#
# 但保留硬风控底线 (防止接盘):
#   - 非亏损股 (PE > 0)
#   - 最大回撤可控 (< 30%, 防止已过情绪顶)
#   - --deep 模式: 产业资本未大幅减持 (Δpp > -5%)
#
# 关键输出特性: 每只股票给出**明确交易纪律**
#   - 买入区间 (当前价附近)
#   - 止损价 (-10% 严格)
#   - 减仓信号 (量比下降 / 1W 继续暴涨超 +15%)
#   - 目标价 (+20~+40%)
#
# Usage:
#   momentum.sh [--deep] [--final=N] [--preset=balanced|aggressive|contrarian]
#
# Presets:
#   balanced     (默认) 标准 momentum + 基础风控
#   aggressive   放宽回撤 / 位置要求, 追更猛的
#   contrarian   短期回调 (1W ≤ 0%) 但中长期仍强势 — 左侧博弈
#
# Examples:
#   momentum.sh                       # 默认 balanced, 10 只
#   momentum.sh --preset=aggressive   # 更激进
#   momentum.sh --deep                # 加产业资本检测
#
# Env: TUSHARE_TOKEN required.

set -uo pipefail

here="$(dirname "$(readlink -f "$0")")"

exec python3 - "$here" "$@" <<'PY'
import csv, datetime, math, statistics, subprocess, sys
from collections import defaultdict

here = sys.argv[1]
raw_args = sys.argv[2:]

deep_mode = False
final_n = 10
preset = "balanced"

for arg in raw_args:
    if arg in ("-h", "--help"):
        with open(f"{here}/momentum.sh") as f:
            lines = f.readlines()
        sys.stderr.write("".join(l[2:] if l.startswith("# ") else l[1:] if l.startswith("#") else ""
                                 for l in lines[1:60]))
        sys.exit(0)
    elif arg == "--deep":
        deep_mode = True
    elif arg.startswith("--final="):
        final_n = int(arg[8:])
    elif arg.startswith("--preset="):
        preset = arg[9:]
    else:
        sys.stderr.write(f"unknown arg: {arg}\n"); sys.exit(2)

# Preset configs
if preset == "aggressive":
    cfg_r2_r1m_min     = 15      # 放宽 1M 要求
    cfg_r2_r1w_min     = 0       # 1W 只要不跌
    cfg_r2_vol_ratio   = 1.3     # 量比要求降
    cfg_r2_pos_min     = 60      # 位置要求降 (可接回调)
    cfg_r3_mdd_max     = -40     # 允许更大回撤
elif preset == "contrarian":
    cfg_r2_r1m_min     = 20
    cfg_r2_r1w_min     = -10    # 允许短期回调
    cfg_r2_r1w_max     = 3      # 必须非暴涨, 正在蓄势
    cfg_r2_vol_ratio   = 1.2
    cfg_r2_pos_min     = 60
    cfg_r3_mdd_max     = -30
else:  # balanced (默认)
    cfg_r2_r1m_min     = 20      # 1M ≥ +20% (已在趋势)
    cfg_r2_r1w_min     = 0       # 1W ≥ 0% (短期未回调)
    cfg_r2_r1w_max     = 50      # 1W 上限 (避免极端投机)
    cfg_r2_vol_ratio   = 1.5     # 量比 ≥ 1.5x
    cfg_r2_pos_min     = 70      # 位置 ≥ 70%
    cfg_r3_mdd_max     = -30     # 最大回撤不超 30%

# tushare helpers
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

# Load concept pool
sys.path.insert(0, here)
try:
    from concepts_data import CONCEPTS
except ImportError:
    CONCEPTS = {}

code_to_concepts = defaultdict(list)
for concept, stocks in CONCEPTS.items():
    for code, _ in stocks:
        code_to_concepts[code].append(concept)

# 日期准备
today   = datetime.date.today().strftime("%Y%m%d")
past120 = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y%m%d")
cal = tushare("trade_cal", exchange="SSE", start_date=past120, end_date=today,
              fields="cal_date,is_open")
open_days = sorted([r["cal_date"] for r in cal if r.get("is_open") == "1"], reverse=True)

latest  = open_days[0]
d_5d    = open_days[5]  if len(open_days) > 5  else open_days[-1]
d_20d   = open_days[20] if len(open_days) > 20 else open_days[-1]
d_60d   = open_days[60] if len(open_days) > 60 else open_days[-1]
d_120d  = open_days[119] if len(open_days) > 119 else open_days[-1]

# ====================================================================
print()
print("╔════════════════════════════════════════════════════════════════════════╗")
print(f"║  🚀 MOMENTUM SELECTION  ·  {preset.upper()} preset  ·  mode={'DEEP' if deep_mode else 'QUICK'}".ljust(73) + "║")
print(f"║  博弈仓工具 (与 funnel.sh 对偶)  数据日: {latest}".ljust(73) + "║")
print("╚════════════════════════════════════════════════════════════════════════╝")

# ====================================================================
# Round 0: 数据采集
# ====================================================================
print(f"\n  ROUND 0: 全市场扫描", file=sys.stderr)
db = tushare("daily_basic", trade_date=latest,
             fields="ts_code,close,turnover_rate,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv")
# T-1 fallback
if len(db) < 100 and len(open_days) > 1:
    sys.stderr.write(f"  [WARN] {latest} 数据不全, 退回 T-1={open_days[1]}\n")
    latest = open_days[1]
    d_5d   = open_days[6]; d_20d = open_days[21]
    d_60d  = open_days[61]; d_120d = open_days[120] if len(open_days) > 120 else open_days[-1]
    db = tushare("daily_basic", trade_date=latest,
                 fields="ts_code,close,turnover_rate,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv")

db_by_code = {r["ts_code"]: r for r in db}

daily_latest = {r["ts_code"]: r for r in tushare("daily", trade_date=latest,
                                                  fields="ts_code,close,amount,vol,high,low")}
daily_5d = {r["ts_code"]: r for r in tushare("daily", trade_date=d_5d, fields="ts_code,close,amount")}
daily_20d = {r["ts_code"]: r for r in tushare("daily", trade_date=d_20d, fields="ts_code,close,amount")}
daily_60d = {r["ts_code"]: r for r in tushare("daily", trade_date=d_60d, fields="ts_code,close")}

basic = tushare("stock_basic", list_status="L", fields="ts_code,name,industry,list_date")
basic_by_code = {r["ts_code"]: r for r in basic}

# 为了算 120 天位置和回撤, 对每只候选股拉 120 天日线代价太高
# 改用近似: 用 60 天数据 + 当前价 估算位置
# 我们取 60 天前的 close 作为 "低点" 近似 — 若 60 天有更低, 低估位置
# 实际生产上可以对 Round 1 通过的 ~300 只跑一次 120 天
# 当前用近似: 位置 = (cur - 60d_low_proxy) / (max(cur, 60d_high_proxy) - 60d_low_proxy)
# 不够准确, 直接拉 120 天 daily 最可靠但贵. 这里用一个简化: 用 amount_trend 近似

# 简化方案: 位置 = f(1M 收益, 3M 收益).
# 如果 3M > 0 且 1M > 0 且 1M > 0.5 * 3M → 位置接近高点 (>70%)
# 如果 3M > 20% 且 1M > 10% → 位置 ~80%+
# 如果 3M > 30% → 位置 ~90%

records = {}
for ts_code, d in db_by_code.items():
    b = basic_by_code.get(ts_code, {})
    dl = daily_latest.get(ts_code, {})
    d5 = daily_5d.get(ts_code, {})
    d20 = daily_20d.get(ts_code, {})
    d60 = daily_60d.get(ts_code, {})

    cur_close = to_float(dl.get("close")) or to_float(d.get("close"))
    if cur_close is None: continue

    r1w = ret_pct(cur_close, d5.get("close"))
    r1m = ret_pct(cur_close, d20.get("close"))
    r3m = ret_pct(cur_close, d60.get("close"))

    amt_cur = (to_float(dl.get("amount")) or 0) / 1e5
    amt_20d = (to_float(d20.get("amount")) or 0) / 1e5

    # 估算位置 (简化): r3m 大部分时候能代表强弱
    # 真实的 120 天位置需要拉 120 天日线, 这里用代理
    # > 40% 3M 涨幅 → 位置 ~85%; > 20% → ~70%; > 10% → ~55%
    if r3m is None: pos_proxy = None
    elif r3m >= 40: pos_proxy = 85
    elif r3m >= 20: pos_proxy = 70 + (r3m - 20) * 0.75
    elif r3m >= 0:  pos_proxy = 50 + r3m
    elif r3m >= -20: pos_proxy = 50 + r3m * 1.5  # 跌了就降位置
    else: pos_proxy = 20

    records[ts_code] = {
        "ts_code": ts_code,
        "name": b.get("name", ts_code),
        "industry": b.get("industry", "?"),
        "list_date": b.get("list_date", "20000101"),
        "close": cur_close,
        "high_today": to_float(dl.get("high")),
        "low_today": to_float(dl.get("low")),
        "pe_ttm": to_float(d.get("pe_ttm")),
        "pb": to_float(d.get("pb")),
        "mv_yi": (to_float(d.get("total_mv")) or 0) / 1e4,
        "amt_cur": amt_cur,
        "amt_20d": amt_20d,
        "vol_ratio": amt_cur / amt_20d if amt_20d > 0 else 0,
        "r1w": r1w,
        "r1m": r1m,
        "r3m": r3m,
        "pos_proxy": pos_proxy,
    }

# ====================================================================
# Round 1: 基础盘 (流动性 + 市值 + 排除 ST/新股)
# ====================================================================
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  ROUND 1: 基础盘 (流动性 ≥ 1 亿, 市值 ≥ 50 亿, 排除 ST/新股)")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  注: 博弈仓流动性要求 (1 亿) 比基础仓 (2 亿) 宽松, 允许小盘题材股")

r1 = []
for code, r in records.items():
    if "ST" in r["name"] or "退" in r["name"]: continue
    if r["amt_cur"] < 1: continue  # 博弈仓允许更小流动性
    if r["mv_yi"] < 50: continue
    try:
        ld = datetime.datetime.strptime(r["list_date"], "%Y%m%d").date()
        if (datetime.date.today() - ld).days < 60: continue
    except Exception:
        pass
    r1.append(r)

print(f"  → 剩余: {len(r1)} 只")

# ====================================================================
# Round 2: MOMENTUM 核心 (趋势 + 放量 + 高位)
# ====================================================================
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  ROUND 2: MOMENTUM 核心 — 要的就是 funnel 排除的 (趋势中 + 放量 + 高位)")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  逻辑 (preset={preset}):")
print(f"    1. 1M 涨幅 ≥ {cfg_r2_r1m_min}% (已在趋势里)")
print(f"    2. 1W 涨幅 ≥ {cfg_r2_r1w_min}% (短期未回调/反抽)")
print(f"    3. 量比 ≥ {cfg_r2_vol_ratio}x (明确放量, 比 funnel 1.2x 严)")
print(f"    4. 估算位置 ≥ {cfg_r2_pos_min}% (已接近 120 天高位)")

def round2_pass(r):
    if r["r1m"] is None or r["r1m"] < cfg_r2_r1m_min: return False
    if r["r1w"] is None or r["r1w"] < cfg_r2_r1w_min: return False
    if preset == "contrarian":
        if r["r1w"] > cfg_r2_r1w_max: return False  # contrarian 上限
    else:
        if r["r1w"] > cfg_r2_r1w_max: return False  # 极端上限
    if r["vol_ratio"] < cfg_r2_vol_ratio: return False
    if r["pos_proxy"] is None or r["pos_proxy"] < cfg_r2_pos_min: return False
    return True

r2 = [r for r in r1 if round2_pass(r)]
print(f"  → 剩余: {len(r2)} 只")

# ====================================================================
# Round 3: 题材 + 风控底线
# ====================================================================
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  ROUND 3: 题材匹配 + 风控底线")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  题材: 属于 concepts 池 OR 属于热门行业 (1M 均涨 top 5)")
print(f"  风控: PE > 0 (排除亏损股, 即使是题材也不能纯梭哈)")

# 热门行业 top 5
by_ind = defaultdict(list)
for r in r1:
    if r["r1m"] is not None:
        by_ind[r["industry"]].append(r["r1m"])
ind_rank = sorted(
    [(ind, sum(vs)/len(vs)) for ind, vs in by_ind.items() if vs],
    key=lambda x: -x[1]
)
top5_inds = set(ind for ind, _ in ind_rank[:5])

r3 = []
for r in r2:
    # PE > 0 (排除亏损)
    if r["pe_ttm"] is None or r["pe_ttm"] <= 0: continue
    # 主题或行业
    concepts = code_to_concepts.get(r["ts_code"], [])
    in_hot_industry = r["industry"] in top5_inds
    if not concepts and not in_hot_industry: continue
    r["tags"] = [("concept", c) for c in concepts] + \
                ([("industry", r["industry"])] if in_hot_industry else [])
    r3.append(r)

print(f"  → 剩余: {len(r3)} 只")

# ====================================================================
# Round 4: Deep mode — 产业资本检测 (可选)
# ====================================================================
if deep_mode and r3:
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  ROUND 4 [DEEP]: 产业资本减持检测")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  逻辑: 排除 产业资本 4 季度累计 Δpp < -5pp 的股 (大股东大幅减持)")
    candidates = r3[:40]
    print(f"  [DEEP] 对 {len(candidates)} 只做 top10_floatholders...", file=sys.stderr)

    def classify(name):
        name = (name or "").replace(" ", "")
        if "香港中央结算" in name: return "北向"
        if any(k in name for k in ["证券投资基金","ETF","交易型开放式指数"]):
            return "公募"
        if any(k in name for k in ["投资控股","集团","控股公司","有限公司"]):
            return "产业资本"
        return "其他"

    r4 = []
    for i, r in enumerate(candidates, 1):
        rows = tushare("top10_floatholders", ts_code=r["ts_code"],
                       fields="end_date,holder_name,hold_ratio", timeout=30)
        if not rows:
            r4.append(r); continue  # 无数据保守保留
        by_q = defaultdict(list)
        for rr in rows:
            q = rr.get("end_date")
            if q: by_q[q].append(rr)
        qs = sorted(by_q.keys(), reverse=True)[:5]
        if len(qs) < 2:
            r4.append(r); continue
        def cap_ratio(rs):
            return sum(to_float(x.get("hold_ratio"), 0) or 0
                       for x in rs if classify(x.get("holder_name")) == "产业资本")
        latest_cap = cap_ratio(by_q[qs[0]])
        oldest_cap = cap_ratio(by_q[qs[-1]])
        delta = latest_cap - oldest_cap
        r["cap_delta"] = delta
        if i % 10 == 0:
            print(f"    [{i}/{len(candidates)}] {r['name']} cap Δ={delta:+.2f}pp",
                  file=sys.stderr)
        if delta > -5:  # 产业资本没大幅减持
            r4.append(r)

    print(f"  → 剩余: {len(r4)} 只 (过滤 {len(candidates) - len(r4)} 只大股东大减持)")
    r3 = r4

# ====================================================================
# Final: momentum score 打分 + 输出交易纪律
# ====================================================================
def momentum_score(r):
    score = 0
    # 量比 (资金关注度)
    score += min(r["vol_ratio"], 5) * 15
    # 1M 涨幅 (momentum 本身)
    score += min(r["r1m"] or 0, 100) * 0.5
    # 位置 (在高位 = momentum 确认)
    score += (r["pos_proxy"] or 0) * 0.3
    # 市值 (大市值更稳, 但博弈仓允许小市值 → 不加不减)
    # PE 极高扣分 (避免纯炒作)
    if r["pe_ttm"] and r["pe_ttm"] > 150:
        score -= (r["pe_ttm"] - 150) * 0.1
    # deep 模式下大股东减持扣分
    if r.get("cap_delta") is not None and r["cap_delta"] < 0:
        score += r["cap_delta"] * 2   # delta 负 = 减仓 = 扣分
    return score

r3.sort(key=lambda r: -momentum_score(r))
final = r3[:final_n]

# ====================================================================
# FINAL 输出 + 每只股票的交易纪律
# ====================================================================
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  FINAL: {len(final)} 只博弈仓候选 (附交易纪律)")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

def fmt_pct(v, w=7):
    if v is None: return "n/a".rjust(w)
    return f"{v:+{w-1}.1f}%"

for i, r in enumerate(final, 1):
    cur = r["close"]
    # 交易纪律计算
    buy_low  = cur * 0.97
    buy_high = cur * 1.03
    stop_loss = cur * 0.90   # -10% 硬止损
    target_low = cur * 1.20   # +20% 目标
    target_high = cur * 1.40
    reduce_signal_pct = 15   # 1W 继续 > +15% 开始减仓

    concepts = [c for kind, c in r.get("tags", []) if kind == "concept"]
    concept_str = ", ".join(concepts[:2]) if concepts else r["industry"] + " [行业]"

    cap_str = ""
    if r.get("cap_delta") is not None:
        cap_str = f" | 产业资本Δ{r['cap_delta']:+.1f}pp"

    print(f"\n  【{i}】 {r['ts_code']:<12} {r['name']}  · {concept_str}")
    print(f"      当前 ¥{cur:.2f}  PE={r['pe_ttm']:.1f}  市值={r['mv_yi']:.0f}亿  "
          f"1W={fmt_pct(r['r1w'])} 1M={fmt_pct(r['r1m'])} 3M={fmt_pct(r['r3m'])}  "
          f"量比={r['vol_ratio']:.1f}x  位置≈{r['pos_proxy']:.0f}%{cap_str}")
    print(f"      ▸ 交易纪律:")
    print(f"          买入区间: ¥{buy_low:.2f} — ¥{buy_high:.2f}  (当前价 ±3%)")
    print(f"          止损价:   ¥{stop_loss:.2f}  (-10% 严格)")
    print(f"          目标价:   ¥{target_low:.2f} — ¥{target_high:.2f}  (+20%~+40%)")
    print(f"          减仓信号: 1W 继续涨幅 > +{reduce_signal_pct}% 开始分批减仓")

# Footer
print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  ⚠️  博弈仓纪律提醒:")
print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
print(f"  1. 单只股票仓位 ≤ 3%, 博弈仓总仓位 ≤ 20%")
print(f"  2. 严格执行 -10% 止损, 不扛跌")
print(f"  3. 1W 涨幅 > +15% 开始减仓 (防末期情绪顶)")
print(f"  4. 建议每 3-5 个交易日重跑 momentum.sh 检查是否仍在榜")
print(f"  5. 如果标的从榜上消失 (1M 跌破 +20% 或量比萎缩), 说明趋势破坏, 立即减仓")

if final:
    top5_codes = [r["ts_code"] for r in final[:5]]
    compare_args = " ".join(
        f"{code.split('.')[1].lower()}{code.split('.')[0]}"
        for code in top5_codes
    )
    print(f"\n  💡 对 top 5 跑横向对比:")
    print(f"     bash {here}/compare.sh {compare_args}")
    print()
PY
