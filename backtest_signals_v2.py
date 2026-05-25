#!/usr/bin/env python3
"""backtest_signals_v2.py — 用 signal_collector v2 (含 macro guard + breakout)
跑 backtest_dataset.jsonl (n=21).

输出:
  * 每条样本的 7 维打分 + verdict
  * 按 action 类型 (BUY/WATCH/HOLD/EXIT/TREND_BUY) 分组的命中率
  * macro_guard 触发统计
  * breakout 维度命中率
  * v1 vs v2 对比 (12 条原 BUY 子集)

判分规则:
  GO + 真胜 → TP (✅)
  GO + 真负 → FP (❌, 买错)
  NEUTRAL/NO_GO/NEUTRAL_BY_MACRO_GUARD + 真负 → TN (✅, 避坑)
  NEUTRAL/NO_GO/NEUTRAL_BY_MACRO_GUARD + 真胜 → FN (❌, 错过)

WATCH 样本不参与命中率, 只看 verdict 是否符合"观察"语义.
"""

import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import asdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from signal_collector import collect, _print_report  # noqa


DATASET = os.path.join(HERE, "backtest_dataset.jsonl")
RESULT_OUT = os.path.join(HERE, "backtest_signals_v2_results.json")


def main():
    samples = [json.loads(l) for l in open(DATASET)]
    print("=" * 84)
    print(f"Signal Collector v2 — {len(samples)} 历史样本盲测 (含 macro guard + 突破结构)")
    print("=" * 84)

    results = []
    for s in samples:
        try:
            r = collect(s["ts_code"], s["decision_date"])
        except Exception as e:
            print(f"\n!! {s['ts_code']} @ {s['decision_date']} 失败: {e}")
            continue
        results.append({**s, "report": r})

        win_str = "✅ 胜" if s["win"] else "❌ 负"
        print(f"\n┌─ {s['name']:14s}  {s['action']:9s} entry={s['entry_price']:>8.2f}  "
              f"exit={s['exit_price']:>8.2f}  T+{s['horizon_days']}d  P&L={s['pnl_pct']:+6.1f}%  {win_str}")
        _print_report(r, indent="│ ")

    # ============================================================================
    # 汇总分析
    # ============================================================================
    print("\n" + "=" * 84)
    print("汇总: 决策类型 × verdict 分布")
    print("=" * 84)

    by_act_verdict = defaultdict(lambda: defaultdict(int))
    for x in results:
        by_act_verdict[x["action"]][x["report"].verdict] += 1
    print(f"{'Action':<11s} | {'GO':>3s} {'NEU':>4s} {'NO_GO':>5s} {'GUARD':>5s} | total")
    print("-" * 50)
    for act in sorted(by_act_verdict.keys()):
        d = by_act_verdict[act]
        go = d.get("GO", 0); ne = d.get("NEUTRAL", 0); ng = d.get("NO_GO", 0); gd = d.get("NEUTRAL_BY_MACRO_GUARD", 0)
        total = go+ne+ng+gd
        print(f"{act:<11s} | {go:>3d} {ne:>4d} {ng:>5d} {gd:>5d} | {total}")
    print()

    # BUY/TREND_BUY 命中率分析
    print("=" * 84)
    print("BUY+TREND_BUY 命中率 (新框架对真实 P&L 的判断)")
    print("=" * 84)
    print(f"{'name':<14s} {'date':<10s} {'P&L':>8s}  {'horizon':>7s}  {'分':>3s}  {'verdict':<26s} {'判定':<8s}")
    print("-" * 84)

    buy_results = [x for x in results if x["action"] in ("BUY", "TREND_BUY")]
    correct = wrong = 0
    blocked = 0
    saved_pnl = 0.0
    missed_pnl = 0.0
    saved_by_guard = 0
    for x in buy_results:
        r: any = x["report"]
        is_go_like = r.verdict == "GO"
        verdict_marker = ""
        if is_go_like and x["win"]:
            verdict_marker = "✅ TP"
            correct += 1
        elif (not is_go_like) and (not x["win"]):
            verdict_marker = "✅ TN"
            correct += 1
            blocked += 1
            saved_pnl += abs(x["pnl_pct"])
            if r.verdict == "NEUTRAL_BY_MACRO_GUARD":
                saved_by_guard += 1
        elif is_go_like and (not x["win"]):
            verdict_marker = "❌ FP"
            wrong += 1
        else:
            verdict_marker = "❌ FN"
            wrong += 1
            missed_pnl += x["pnl_pct"]

        print(f"{x['name']:<14s} {x['decision_date']:<10s} {x['pnl_pct']:>+7.1f}%  "
              f"T+{x['horizon_days']:<2d}     {r.total_score:>+3d}  {r.verdict:<26s} {verdict_marker}")

    n_buy = len(buy_results)
    print("-" * 84)
    print(f"BUY/TREND_BUY 样本: {n_buy} 条 / 命中 {correct}/{n_buy} = {correct/max(1,n_buy)*100:.0f}%")
    print(f"  TP (买对): {correct - blocked}")
    print(f"  TN (避坑): {blocked} 条 (含 macro_guard 拦截 {saved_by_guard} 条) / 累计避免 -{saved_pnl:.1f}% 浮亏")
    print(f"  FN (错过): {wrong - sum(1 for x in buy_results if x['report'].verdict == 'GO' and not x['win'])} / 累计错过 +{missed_pnl:.1f}% 涨幅")
    print()

    # WATCH 样本: 看 verdict 与实际表现的符号
    print("=" * 84)
    print("WATCH 样本: verdict 与真实 P&L 关系 (WATCH 应该上涨的票, 新框架是否给 GO?)")
    print("=" * 84)
    watch_results = [x for x in results if x["action"] == "WATCH"]
    for x in watch_results:
        r = x["report"]
        win_str = "✅ 胜" if x["win"] else "❌ 负"
        # WATCH 实际上涨 → 新框架理想 = GO; 实际下跌 → 理想 = NEUTRAL/NO_GO
        ideal = "GO" if x["win"] else "NEU/NO_GO"
        match = "✅" if (x["win"] and r.verdict == "GO") or (not x["win"] and r.verdict != "GO") else "❌"
        print(f"  {x['name']:<14s} P&L={x['pnl_pct']:+6.1f}% {win_str}  → verdict={r.verdict:<26s} (ideal={ideal}) {match}")
    print()

    # macro_guard 触发统计
    guard_active = sum(1 for x in results if x["report"].macro_guard_active)
    print(f"macro_guard 触发: {guard_active}/{len(results)} 条样本 (= 决策日大盘 < MA60)")
    print()

    # breakout 维度命中
    bo_pos = sum(1 for x in results if x["report"].breakout[0] > 0)
    bo_neg = sum(1 for x in results if x["report"].breakout[0] < 0)
    bo_zero = sum(1 for x in results if x["report"].breakout[0] == 0)
    print(f"突破结构 维度: +1 共 {bo_pos} / 0 共 {bo_zero} / -1 共 {bo_neg}")
    print()

    # 输出 JSON
    with open(RESULT_OUT, "w") as f:
        serial = []
        for x in results:
            d = {k: v for k, v in x.items() if k != "report"}
            d["report"] = asdict(x["report"])
            serial.append(d)
        json.dump(serial, f, ensure_ascii=False, indent=2, default=str)
    print(f"详细 JSON → {RESULT_OUT}")


if __name__ == "__main__":
    main()
