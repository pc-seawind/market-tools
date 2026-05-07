#!/usr/bin/env bash
# backtest.sh — 信号历史回测 (验证 signals.py 的规则有效性).
#
# 用途: 每次修改 signals.py 的信号规则后, 用这个工具在 N 只股的历史数据上
# 重放信号, 观察 forward returns (触发后 +5d/+10d/+20d 收益) 验证:
#   - 信号是否在历史上真实 predict 了后续走势
#   - 还是只是 noise (触发后走势随机)
#
# Usage:
#   backtest.sh <ticker1> [ticker2 ...] [--days=180] [--signal=TYPE]
#
# Args:
#   tickers        A 股格式: sh600519 / sz000001 / bj831168 (支持多只)
#   --days=N       回测天数 (默认 180 calendar, 约 120 交易日)
#   --signal=TYPE  只显示某种信号 (默认显示所有):
#                  SELL_EXHAUSTION / SELL_CONFIRMED / SELL_EXTREME /
#                  SELL_BREAKDOWN / SELL_TOP / BUY_EARLY / BUY_BREAKOUT /
#                  BUY_PULLBACK
#
# Output:
#   对每只股, 每种信号类型:
#     - 所有历史触发点 (日期, 收盘, 关键指标)
#     - 触发后 +5d/+10d/+20d 收益
#     - 统计: 平均收益 + 正收益占比
#
# 判断规则有效性:
#   - 卖点信号: 触发后 avg return 为负 + 正收益占比低 = 有效
#   - 买点信号: 触发后 avg return 为正 + 正收益占比高 = 有效
#   - 如果结果相反或随机 → 信号规则需修正
#
# Example:
#   # 对寒武纪/海光/江波龙过去 180 天回测所有信号
#   backtest.sh sh688256 sh688041 sz301308
#
#   # 只看 SELL_EXHAUSTION 的触发表现
#   backtest.sh sh688256 sh688041 sz301308 --signal=SELL_EXHAUSTION
#
# Env: TUSHARE_TOKEN required.
# Deps: bash + python3 stdlib + signals.py + watchlist_data.py (可选)

set -uo pipefail

here="$(dirname "$(readlink -f "$0")")"

exec python3 - "$here" "$@" <<'PY'
import csv, datetime, subprocess, sys
from collections import defaultdict

here = sys.argv[1]
raw_args = sys.argv[2:]

days_back = 180
only_signal = None
tickers = []

for arg in raw_args:
    if arg in ("-h", "--help"):
        with open(f"{here}/backtest.sh") as f:
            lines = f.readlines()
        sys.stderr.write("".join(l[2:] if l.startswith("# ") else l[1:] if l.startswith("#") else ""
                                 for l in lines[1:40]))
        sys.exit(0)
    elif arg.startswith("--days="):
        days_back = int(arg[7:])
    elif arg.startswith("--signal="):
        only_signal = arg[9:]
    elif arg.startswith("--"):
        sys.stderr.write(f"unknown flag: {arg}\n"); sys.exit(2)
    else:
        tickers.append(arg)

if not tickers:
    sys.stderr.write("usage: backtest.sh <ticker1> [ticker2 ...] [--days=N] [--signal=TYPE]\n")
    sys.exit(2)

sys.path.insert(0, here)
try:
    import signals as sig_mod
except ImportError as e:
    sys.stderr.write(f"ERROR: signals.py 加载失败: {e}\n"); sys.exit(3)

def ticker_to_tscode(t):
    """sh600519 → 600519.SH 等"""
    tl = t.lower()
    if tl.startswith(("sh", "sz", "bj")) and tl[2:].isdigit():
        return f"{tl[2:]}.{tl[:2].upper()}"
    if tl.endswith(".sh") or tl.endswith(".sz") or tl.endswith(".bj"):
        return t.upper()
    raise ValueError(f"bad ticker: {t!r}")

TUSHARE = f"{here}/tushare.py"

def fetch_history(ts_code, days):
    """拉 days calendar days 历史 (A 股 daily)."""
    end = datetime.date.today().strftime("%Y%m%d")
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y%m%d")
    out = subprocess.run(
        ["python3", TUSHARE, "daily", f"ts_code={ts_code}",
         f"start_date={start}", f"end_date={end}",
         "--fields=trade_date,close,amount,pct_chg", "--csv"],
        capture_output=True, text=True, timeout=60,
    )
    if out.returncode != 0:
        sys.stderr.write(f"  {ts_code} fetch failed\n")
        return []
    rows = list(csv.DictReader(out.stdout.splitlines()))
    rows.sort(key=lambda r: r["trade_date"])  # 升序
    return rows

def backtest_one(ts_code, name, rows):
    """对 rows 做 sliding window, 返回 events list."""
    events = []
    # 至少需要 60 天历史才能算 r3m
    for i in range(60, len(rows)):
        try:
            cur = float(rows[i]["close"])
            c5  = float(rows[i-5]["close"])
            c20 = float(rows[i-20]["close"])
            c60 = float(rows[i-60]["close"])
            amt_cur = float(rows[i]["amount"])
        except (ValueError, KeyError):
            continue

        # 20 日 amount 均
        amts = [float(rows[j]["amount"]) for j in range(max(0, i-20), i)
                if rows[j].get("amount")]
        amt_20d_avg = sum(amts) / len(amts) if amts else 0
        vr = amt_cur / amt_20d_avg if amt_20d_avg > 0 else None

        r1w = (cur - c5) / c5 * 100 if c5 else None
        r1m = (cur - c20) / c20 * 100 if c20 else None
        r3m = (cur - c60) / c60 * 100 if c60 else None
        pos = sig_mod.position_proxy(r1m)

        today_chg = None
        try: today_chg = float(rows[i].get("pct_chg") or "")
        except ValueError: pass

        m = {"r1w": r1w, "r1m": r1m, "r3m": r3m,
             "vol_ratio": vr, "pos": pos, "pct_chg_today": today_chg}
        sigs = sig_mod.detect(m)
        if not sigs: continue

        # 计算 forward returns
        fwd = {}
        for n in [5, 10, 20]:
            if i + n < len(rows):
                try:
                    cn = float(rows[i+n]["close"])
                    fwd[n] = (cn - cur) / cur * 100
                except ValueError:
                    fwd[n] = None
            else:
                fwd[n] = None

        events.append({
            "date": rows[i]["trade_date"],
            "close": cur, "r1w": r1w, "r1m": r1m, "r3m": r3m,
            "vr": vr, "pos": pos,
            "signals": [s[1] for s in sigs],
            "fwd": fwd,
        })
    return events

# 主循环
print(f"\n{'═' * 94}")
print(f"  🔬 信号回测  ·  {len(tickers)} 只股 × {days_back} 天历史  ·  signals.py 当前规则")
if only_signal:
    print(f"  过滤信号类型: {only_signal}")
print(f"{'═' * 94}")

# 收集全局统计 (跨所有股跨所有信号)
global_stats = defaultdict(lambda: {"fwd_5": [], "fwd_10": [], "fwd_20": []})

for t in tickers:
    try:
        ts_code = ticker_to_tscode(t)
    except ValueError as e:
        sys.stderr.write(f"  {e}\n"); continue

    print(f"\n  拉 {t} ({ts_code}) {days_back} 天历史 ...", file=sys.stderr)
    rows = fetch_history(ts_code, days=days_back)
    if len(rows) < 60:
        sys.stderr.write(f"    [skip] 数据不足 {len(rows)} 天, 需 >60\n"); continue
    name = ts_code
    events = backtest_one(ts_code, name, rows)

    print(f"\n{'━' * 94}")
    print(f"  {ts_code}  —  回测 {rows[0]['trade_date']} → {rows[-1]['trade_date']} "
          f"({len(rows)} 天, {len(events)} 次信号触发)")
    print(f"{'━' * 94}")

    by_sig = defaultdict(list)
    for ev in events:
        for s in ev["signals"]:
            if only_signal and s != only_signal: continue
            by_sig[s].append(ev)

    if not by_sig:
        if only_signal:
            print(f"  (无 {only_signal} 触发)")
        else:
            print(f"  (无信号触发)")
        continue

    # 展示: 对每种信号 + forward returns
    sig_order = ["BUY_EARLY", "BUY_BREAKOUT", "BUY_PULLBACK",
                 "SELL_EXHAUSTION", "SELL_CONFIRMED", "SELL_EXTREME",
                 "SELL_BREAKDOWN", "SELL_TOP",
                 "TODAY_SURGE", "TODAY_DROP"]
    for sig_type in sig_order:
        if sig_type not in by_sig: continue
        evs = by_sig[sig_type]
        fwd_5  = [e["fwd"][5]  for e in evs if e["fwd"][5]  is not None]
        fwd_10 = [e["fwd"][10] for e in evs if e["fwd"][10] is not None]
        fwd_20 = [e["fwd"][20] for e in evs if e["fwd"][20] is not None]

        # 收集到 global
        for x in fwd_5:  global_stats[sig_type]["fwd_5"].append(x)
        for x in fwd_10: global_stats[sig_type]["fwd_10"].append(x)
        for x in fwd_20: global_stats[sig_type]["fwd_20"].append(x)

        print(f"\n  ▣ {sig_type}  ({len(evs)} 次)")
        print(f"    {'日期':<10}{'收盘':>9}{'1W':>8}{'1M':>8}{'3M':>8}"
              f"{'量比':>7}{'位置':>6}{'+5d':>8}{'+10d':>8}{'+20d':>8}")
        for ev in evs[-8:]:   # 最近 8 次, 避免刷屏
            def fp(v, w=7):
                return f"{v:+{w-1}.1f}%" if v is not None else "n/a".rjust(w)
            vr_str = f"{ev['vr']:.1f}x" if ev['vr'] else "n/a"
            r3m_str = fp(ev['r3m'])
            print(f"    {ev['date']:<10}{ev['close']:>8.2f} "
                  f"{fp(ev['r1w']):>7} {fp(ev['r1m']):>7} "
                  f"{r3m_str:>7} {vr_str:>6} {ev['pos']:>5.0f}% "
                  f"{fp(ev['fwd'][5]):>7} {fp(ev['fwd'][10]):>7} "
                  f"{fp(ev['fwd'][20]):>7}")
        if len(evs) > 8:
            print(f"    ... ({len(evs)-8} 条更早的未显示, 已纳入统计)")

        # 本股统计
        def stats(arr):
            if not arr: return "n/a"
            avg = sum(arr) / len(arr)
            pos_pct = sum(1 for x in arr if x > 0) / len(arr) * 100
            return f"avg {avg:+.1f}%, 正 {pos_pct:.0f}%"
        if fwd_5:
            print(f"    ─ 本股统计: +5d {stats(fwd_5)} | +10d {stats(fwd_10)} | +20d {stats(fwd_20)}")

# 全局统计
print(f"\n{'═' * 94}")
print(f"  📊 全局统计 (跨所有 {len(tickers)} 只股的所有触发)")
print(f"{'═' * 94}")
print(f"  {'信号类型':<20}{'触发次数':>10}{'+5d 平均':>15}{'+5d 正%':>10}"
      f"{'+20d 平均':>15}{'+20d 正%':>10}  判断")
print(f"  {'-' * 94}")

for sig_type in ["BUY_EARLY", "BUY_BREAKOUT", "BUY_PULLBACK",
                 "SELL_EXHAUSTION", "SELL_CONFIRMED", "SELL_EXTREME",
                 "SELL_BREAKDOWN", "SELL_TOP", "TODAY_SURGE", "TODAY_DROP"]:
    if sig_type not in global_stats: continue
    s = global_stats[sig_type]
    n = len(s["fwd_5"])
    if n == 0: continue
    avg_5 = sum(s["fwd_5"]) / n
    avg_20 = sum(s["fwd_20"]) / len(s["fwd_20"]) if s["fwd_20"] else None
    pos_5 = sum(1 for x in s["fwd_5"] if x > 0) / n * 100
    pos_20 = (sum(1 for x in s["fwd_20"] if x > 0) / len(s["fwd_20"]) * 100
              if s["fwd_20"] else None)

    # 判断有效性
    is_sell = sig_type.startswith("SELL_") or sig_type == "TODAY_SURGE"
    is_buy  = sig_type.startswith("BUY_")
    verdict = "?"
    if is_sell and avg_20 is not None:
        if avg_20 < -5 and pos_20 < 30:
            verdict = "✅ 有效 (触发后明显跌)"
        elif avg_20 < 0:
            verdict = "⚪ 中性 (略负)"
        else:
            verdict = "❌ 无效 (仍涨)"
    elif is_buy and avg_20 is not None:
        if avg_20 > 5 and pos_20 > 60:
            verdict = "✅ 有效 (触发后明显涨)"
        elif avg_20 > 0:
            verdict = "⚪ 中性 (略正)"
        else:
            verdict = "❌ 无效 (仍跌)"

    avg_5_str = f"{avg_5:+.1f}%"
    avg_20_str = f"{avg_20:+.1f}%" if avg_20 is not None else "n/a"
    pos_20_str = f"{pos_20:.0f}%" if pos_20 is not None else "n/a"
    print(f"  {sig_type:<20}{n:>10}{avg_5_str:>15}{pos_5:>8.0f}%"
          f"{avg_20_str:>15}{pos_20_str:>9}  {verdict}")
print()
PY
