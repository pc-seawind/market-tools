#!/usr/bin/env bash
# screen.sh — 条件筛股 (Stage 1: 估值 + 市值 + 流动性 + 动能).
#
# market-tools 此前全部工具都是 "ticker → 分析" 反向流程。screen 填
# 补"条件 → 候选"的 discovery gap: 输入一组 filter, 输出 ranked
# 候选清单。再用 diligence.sh 对 top N 做深度 diligence 形成闭环:
#
#     条件 ─→  screen.sh  ─→  top N 候选  ─→  diligence.sh  ─→  决策
#
# Stage 1 (当前实现, ~5 API calls, <15s):
#   基于 daily_basic + daily 全市场批量数据做 估值/市值/流动性/动能
#   的 filter。这四个维度覆盖 ~80% 的 screening use case。
#
# Stage 2 (未实现, TODO): 用 diligence.sh 或单独的 fina_indicator
#   调用对 Stage 1 候选补 ROE/YoY/北向持股 的精调 filter。
#
# Usage:
#   screen.sh [filters...] [--top=N] [--sort=KEY]
#
# Filters (all optional, AND-combined):
#   --pe-max=N        PE_TTM 上限 (e.g. 30)
#   --pe-min=N        PE_TTM 下限 (默认 0, 自动剔除负 PE/亏损)
#   --pb-max=N        PB 上限
#   --ps-max=N        PS_TTM 上限
#   --dv-min=N        TTM 股息率下限 % (e.g. 3 表示 >=3%)
#   --mv-min=N        总市值下限 (亿, e.g. 100)
#   --mv-max=N        总市值上限 (亿)
#   --amt-min=N       日成交额下限 (亿, 过滤低流动性)
#   --tor-min=N       日换手率下限 %
#   --r1w-min=N       1 周涨跌幅下限 %
#   --r1m-min=N       1 月涨跌幅下限 %
#   --r1m-max=N       1 月涨跌幅上限 % (避免追涨)
#   --r3m-min=N       3 月涨跌幅下限 %
#   --exclude-st      剔除 ST/退市股
#   --market=MKT      sh|sz|bj|all (默认 all; main/gem/star 可后续加)
#
# Output:
#   --top=N           返回前 N 只 (默认 20)
#   --sort=KEY        排序字段: r1w|r1m|r3m|pe|pb|ps|dv|mv|amt|tor
#                     (默认 r1m desc). 前缀 - 表示 desc (如 -pe=升序)
#
# Examples:
#   # 低估红利: PE<15, 股息>3%, 市值>200 亿
#   screen.sh --pe-max=15 --dv-min=3 --mv-min=200 --sort=dv
#
#   # 成长爆发: 1M 涨>+20%, 市值>500 亿, 日成交>10 亿
#   screen.sh --r1m-min=20 --mv-min=500 --amt-min=10 --top=30
#
#   # 超跌反弹候选: 3M 跌>20%, 1W 涨>+5%, 市值>100 亿
#   screen.sh --r3m-min=-100 --r1m-max=0 --r1w-min=5 --mv-min=100 --top=15
#
# Env: TUSHARE_TOKEN required.

set -uo pipefail

here="$(dirname "$(readlink -f "$0")")"

# Parse all args to Python — easier than bash case blocks
exec python3 - "$here" "$@" <<'PY'
import csv, datetime, os, subprocess, sys
from collections import OrderedDict

here = sys.argv[1]
sys.path.insert(0, here)   # 让 sector_health / grading / signals 能被 import
raw_args = sys.argv[2:]

if any(a in ("-h", "--help") for a in raw_args):
    # Reprint the SKILL header
    script_path = f"{here}/screen.sh"
    with open(script_path) as f:
        lines = f.readlines()
    print("".join(l[2:] if l.startswith("# ") else l[1:] if l.startswith("#") else ""
                  for l in lines[1:60]))
    sys.exit(0)

# --- parse filters ---
filters = {}
top = 20
sort_key = "r1m"
sort_asc = False
exclude_st = False
market = "all"

for arg in raw_args:
    if not arg.startswith("--"):
        sys.stderr.write(f"bad arg: {arg!r}\n"); sys.exit(2)
    key_val = arg[2:]
    if "=" in key_val:
        k, v = key_val.split("=", 1)
    else:
        k, v = key_val, None
    k = k.strip()
    if k == "top":
        top = int(v)
    elif k == "sort":
        if v.startswith("-"):
            sort_asc = True; sort_key = v[1:]
        elif v.startswith("+"):
            sort_asc = False; sort_key = v[1:]
        else:
            sort_key = v
    elif k == "exclude-st":
        exclude_st = True
    elif k == "market":
        market = v
    else:
        try:
            filters[k] = float(v)
        except (TypeError, ValueError):
            sys.stderr.write(f"bad filter value: {arg!r}\n"); sys.exit(2)

def tushare(api, **params):
    args = ["python3", f"{here}/tushare.py", api]
    for k, v in params.items():
        if k == "fields": args.append(f"--fields={v}")
        else:             args.append(f"{k}={v}")
    args.append("--csv")
    out = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        sys.stderr.write(f"[WARN] tushare {api} failed: {out.stderr.strip()[:200]}\n")
        return []
    return list(csv.DictReader(out.stdout.splitlines()))

# --- find latest trade_date + N-trading-days-ago dates ---
today = datetime.date.today().strftime("%Y%m%d")
past  = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y%m%d")
cal = tushare("trade_cal", exchange="SSE", start_date=past, end_date=today,
              fields="cal_date,is_open")
# cal is typically newest-first; filter to open days
open_days = [r["cal_date"] for r in cal if r.get("is_open") == "1"]
open_days.sort(reverse=True)  # newest first
if not open_days:
    sys.stderr.write("ERROR: trade_cal 无数据\n"); sys.exit(4)

# Find indices for 1W / 1M / 3M lookbacks (trading days)
latest   = open_days[0]
d_1w_ago = open_days[5]  if len(open_days) > 5  else open_days[-1]
d_1m_ago = open_days[20] if len(open_days) > 20 else open_days[-1]
d_3m_ago = open_days[60] if len(open_days) > 60 else open_days[-1]

print(f"  screening on trade_date={latest}  "
      f"(1W→{d_1w_ago}, 1M→{d_1m_ago}, 3M→{d_3m_ago})", file=sys.stderr)

# --- pull all-market daily_basic (valuation + mv) at latest ---
# daily_basic 字段不含 amount, 需从 daily 补齐
print(f"  pulling daily_basic ({latest})...", file=sys.stderr)
db = tushare("daily_basic", trade_date=latest,
             fields="ts_code,close,turnover_rate,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv")
if not db:
    sys.stderr.write("ERROR: daily_basic 无数据 (trade_date 可能不是交易日)\n"); sys.exit(4)
db_by_code = {r["ts_code"]: r for r in db}
print(f"    got {len(db)} stocks (daily_basic)", file=sys.stderr)

# --- pull daily for latest (for amount) + lookback dates (for returns) ---
print(f"  pulling daily ({latest}) for amount...", file=sys.stderr)
daily_latest = {r["ts_code"]: r for r in
                tushare("daily", trade_date=latest, fields="ts_code,close,amount")}

lookback = {}  # {tag: {ts_code: close}}
for tag, d in [("1w", d_1w_ago), ("1m", d_1m_ago), ("3m", d_3m_ago)]:
    print(f"  pulling daily ({tag}→{d})...", file=sys.stderr)
    rows = tushare("daily", trade_date=d, fields="ts_code,close")
    lookback[tag] = {r["ts_code"]: r["close"] for r in rows}

# --- need stock name/industry. use stock_basic (5000+ rows, 1 call) ---
print(f"  pulling stock_basic...", file=sys.stderr)
basic = tushare("stock_basic", list_status="L",
                fields="ts_code,name,industry,market")
name_by_code = {r["ts_code"]: (r.get("name","?"), r.get("industry","?"), r.get("market","?"))
                for r in basic}

# --- build combined records ---
def to_float(x, default=None):
    try: return float(x) if x not in (None, "", "None") else default
    except ValueError: return default

def ret(cur_close, old_close):
    c1 = to_float(cur_close); c0 = to_float(old_close)
    if c0 is None or c1 is None or c0 == 0: return None
    return (c1 - c0) / c0 * 100

records = []
for ts_code, d in db_by_code.items():
    name_info = name_by_code.get(ts_code, (ts_code, "?", "?"))
    name, industry, mkt = name_info
    cur = to_float(d.get("close"))
    # Skip junk
    if cur is None: continue
    rec = {
        "ts_code": ts_code,
        "name": name,
        "industry": industry,
        "market": mkt,
        "close": cur,
        "pe":  to_float(d.get("pe_ttm")),
        "pb":  to_float(d.get("pb")),
        "ps":  to_float(d.get("ps_ttm")),
        "dv":  to_float(d.get("dv_ttm"), 0.0),
        "mv":  (to_float(d.get("total_mv")) or 0) / 1e4,   # 万元 → 亿元
        "amt": (to_float(daily_latest.get(ts_code, {}).get("amount")) or 0) / 1e5,  # 千元 → 亿元
        "tor": to_float(d.get("turnover_rate")),
        "r1w": ret(cur, lookback["1w"].get(ts_code)),
        "r1m": ret(cur, lookback["1m"].get(ts_code)),
        "r3m": ret(cur, lookback["3m"].get(ts_code)),
    }
    records.append(rec)

print(f"    joined {len(records)} records with basic info", file=sys.stderr)

# --- apply filters ---
def passes(r):
    if exclude_st and ("ST" in (r["name"] or "") or "退" in (r["name"] or "")):
        return False
    if market != "all":
        if not r["ts_code"].lower().startswith(market.lower()):
            return False
    # filters dict uses keys like "pe-max"
    for fk, fv in filters.items():
        if fk == "pe-max":    v = r["pe"];  cond = (v is None or v > fv)
        elif fk == "pe-min":  v = r["pe"];  cond = (v is None or v < fv)
        elif fk == "pb-max":  v = r["pb"];  cond = (v is None or v > fv)
        elif fk == "ps-max":  v = r["ps"];  cond = (v is None or v > fv)
        elif fk == "dv-min":  v = r["dv"];  cond = (v is None or v < fv)
        elif fk == "mv-min":  v = r["mv"];  cond = (v < fv)
        elif fk == "mv-max":  v = r["mv"];  cond = (v > fv)
        elif fk == "amt-min": v = r["amt"]; cond = (v < fv)
        elif fk == "tor-min": v = r["tor"]; cond = (v is None or v < fv)
        elif fk == "r1w-min": v = r["r1w"]; cond = (v is None or v < fv)
        elif fk == "r1w-max": v = r["r1w"]; cond = (v is None or v > fv)
        elif fk == "r1m-min": v = r["r1m"]; cond = (v is None or v < fv)
        elif fk == "r1m-max": v = r["r1m"]; cond = (v is None or v > fv)
        elif fk == "r3m-min": v = r["r3m"]; cond = (v is None or v < fv)
        elif fk == "r3m-max": v = r["r3m"]; cond = (v is None or v > fv)
        else:
            sys.stderr.write(f"unknown filter: --{fk}\n"); sys.exit(2)
        if cond: return False
    # Default filter: strip rows with all-None returns (停牌/新上市)
    if r["r1m"] is None and r["r3m"] is None: return False
    # If user sets --pe-max but didn't set --pe-min, auto-exclude negative PE
    if "pe-max" in filters and "pe-min" not in filters:
        if r["pe"] is None or r["pe"] <= 0: return False
    return True

filtered = [r for r in records if passes(r)]
print(f"  after filters: {len(filtered)} matches", file=sys.stderr)

# --- sort ---
def sort_value(r, k):
    v = r.get(k)
    if v is None: return float("-inf") if not sort_asc else float("inf")
    return v

filtered.sort(key=lambda r: sort_value(r, sort_key), reverse=not sort_asc)
top_rows = filtered[:top]

# ═══ 综合推荐度分级 (使用 grading.py, 与 funnel/momentum 一致) ═══
try:
    import sector_health as _sh_mod
    import grading as _grading
    try:
        from concepts_data import CONCEPTS
    except ImportError:
        CONCEPTS = {}

    # 从已有 records 反推 concept ranking
    code_to_r1m = {r["ts_code"]: r["r1m"] for r in records if r.get("r1m") is not None}
    _concept_ranking = []
    for _cn, _stks in CONCEPTS.items():
        _rs = [code_to_r1m[c] for c, _ in _stks if c in code_to_r1m]
        _avg = sum(_rs)/len(_rs) if _rs else None
        _concept_ranking.append((_cn, _avg, _stks))
    _concept_ranking.sort(key=lambda x: -(x[1] if x[1] is not None else -999))

    # Rebuild maps for sector_health.build_index
    _dl_map = {r["ts_code"]: {"close": r["close"]} for r in records}
    _d20_map = {}
    for r in records:
        if r.get("r1m") is not None and r["close"]:
            c20 = r["close"] / (1 + r["r1m"]/100)
            _d20_map[r["ts_code"]] = {"close": c20}
    _basic_map = {r["ts_code"]: {"name": r["name"], "industry": r.get("industry", "?")}
                  for r in records}

    _sh_index = _sh_mod.build_index(
        daily_latest_map=_dl_map, daily_20d_map=_d20_map,
        stock_basic_map=_basic_map, concept_ranking=_concept_ranking,
    )
    # screen.sh 的 records 用的字段名不同: r1m vs r1m, 但 compute_grade 要 amt_cur/amt_20d
    # 在 screen.sh 里量比用 amt 字段需转换. 直接建 vol_ratio 字段吧
    _fund_map = {}
    try:
        _fund_map = _grading.load_fundamentals_map([r["ts_code"] for r in top_rows])
    except Exception:
        pass

    for _r in top_rows:
        sh_info = _sh_index.get(_r["ts_code"], {})
        # 字段映射: screen 用 pe/mv (不是 pe_ttm/mv_yi)
        _r["pe_ttm"] = _r.get("pe")
        _r["mv_yi"] = _r.get("mv")
        if _r.get("tor") is not None: _r["vol_ratio"] = _r["tor"]
        fund_info = _fund_map.get(_r["ts_code"])
        _grading.compute_grade(_r, sh_info, style="balanced", fund_info=fund_info)
    _grading_ok = True
except Exception as _e:
    sys.stderr.write(f"  [WARN] screen grading 失败: {_e}\n")
    import traceback; traceback.print_exc(file=sys.stderr)
    _grading_ok = False

# --- render ---
def fmt_ret(v):
    if v is None: return "   n/a "
    return f"{v:+6.1f}%"

def fmt_num(v, w=7, p=2):
    if v is None: return " " * w
    return f"{v:{w}.{p}f}"

print()
print("=" * 120)
print(f" 📊 股票筛选结果  trade_date={latest}  "
      f"条件: {', '.join(f'--{k}={v}' for k,v in filters.items()) or '无'}  "
      f"排序={sort_key}{'↑' if sort_asc else '↓'}")
print(f" 匹配: {len(filtered)} 只  |  显示 top {len(top_rows)}")
print("=" * 120)

if _grading_ok:
    # 按 grade 分组展示
    from collections import defaultdict as _dd
    by_grade = _dd(list)
    for r in top_rows: by_grade[r.get("_grade", "C")].append(r)
    GRADE_DESC = {
        "A": "🌟 A 级 · 板块热 + 跑赢 + (可选) 买信号",
        "B": "✅ B 级 · 板块温和或弱信号",
        "C": "👀 C 级 · 信号矛盾或跑输板块",
        "D": "⚠️ D 级 · 板块衰退或严重卖信号",
    }
    for grade in ["A", "B", "C", "D"]:
        if grade not in by_grade: continue
        rs = by_grade[grade]
        print(f"\n━━━ {GRADE_DESC[grade]}  ({len(rs)} 只) ━━━")
        for r in rs:
            name = (r["name"] or "?")[:8]
            ind  = (r["industry"] or "?")[:6]
            heat = r.get("_heat_label", "  ")
            extras = []
            if r.get("_sell_label"): extras.append(r["_sell_label"])
            if r.get("_buy_label"): extras.append(r["_buy_label"])
            extra_str = ("  " + " ".join(extras)) if extras else ""
            print(f"  {heat} {r['ts_code']:<10} {name:<8}  "
                  f"PE={fmt_num(r['pe']):>7} PB={fmt_num(r['pb'],w=5):>5} "
                  f"股息{fmt_num(r['dv']):>6}  市值{fmt_num(r['mv'], w=6, p=0):>6}亿  "
                  f"1M={fmt_ret(r['r1m']):>7}  {ind}{extra_str}")
            if r.get("_fund_label"):
                print(f"         └ 基本面: {r['_fund_label']}")
else:
    # fallback 旧版
    hdr = (f"  {'排名':<3}{'代码':<11}{'名称':<10}{'行业':<8}"
           f"{'收盘':>7}{'PE_TTM':>8}{'PB':>6}{'股息%':>7}"
           f"{'市值(亿)':>10}{'日成交':>10}{'换手%':>7}"
           f"{'1W':>8}{'1M':>8}{'3M':>8}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for i, r in enumerate(top_rows, 1):
        name = (r["name"] or "?")[:8]
        ind  = (r["industry"] or "?")[:6]
        print(f"  {i:>2}. {r['ts_code']:<10} {name:<8}  {ind:<6}  "
              f"{fmt_num(r['close']):>7} {fmt_num(r['pe']):>7} "
              f"{fmt_num(r['pb'],w=5):>5} {fmt_num(r['dv']):>6} "
              f"{fmt_num(r['mv'], w=9, p=0):>9} "
              f"{fmt_num(r['amt'], w=8, p=1):>8}亿 "
              f"{fmt_num(r['tor']):>6} "
              f"{fmt_ret(r['r1w']):>7} {fmt_ret(r['r1m']):>7} {fmt_ret(r['r3m']):>7}")
print()
if top_rows:
    # ts_code 格式: "600519.SH" → diligence.sh 接受 "sh600519"
    tc = top_rows[0]["ts_code"]
    num, mkt = tc.split(".")
    dili_ticker = f"{mkt.lower()}{num}"
    print(f"  下一步: 对 top 候选跑 diligence.sh 做深度分析")
    print(f"  示例:   bash {here}/diligence.sh {dili_ticker}")
print()
PY
