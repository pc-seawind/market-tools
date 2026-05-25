#!/usr/bin/env python3
"""backtest_signals.py — 用 signal_collector 重跑 12 条历史样本.

目标:
  * 11 条 BUY (2 胜 9 负) 在 entry_date 的新框架打分
  * 1 条 北方华创 5/13 WATCH (实际 +15.1% 涨幅) 也跑一遍
  * 看 GO / NEUTRAL / NO_GO 与真实 P&L 的相关性

Backtest 严格 backward-only — signal_collector 内部所有 lookback 都用
trade_date 之前的数据, 不会偷看未来.
"""

import json
import os
import sys
import subprocess
from dataclasses import asdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from signal_collector import collect, _print_report  # noqa


# 历史样本 — 来自 recommendations.jsonl + 用户确认的 P&L
# (ts_code, date, action, entry, current, pnl_pct)
# current 价取自上次对话拉到的实时报价 ~ 5/22 收盘
SAMPLES = [
    # 5/13 第一批 BUY (5/5 全负)
    ("601688.SH", "20260513", "BUY",  19.48,   18.16,  "华泰证券"),
    ("300502.SZ", "20260513", "BUY",  628.99,  606.77, "新易盛"),
    ("300308.SZ", "20260513", "BUY",  1049.20, 1037.98, "中际旭创"),
    ("603444.SH", "20260513", "BUY",  389.75,  355.10, "吉比特"),
    ("300750.SZ", "20260513", "BUY",  434.05,  411.16, "宁德时代"),
    # 5/15 矿业批 (2/2 全负)
    ("603993.SH", "20260515", "BUY",  18.88,   18.53,  "洛阳钼业"),
    ("601899.SH", "20260515", "BUY",  31.84,   30.96,  "紫金矿业"),
    # 5/20 第二批 BUY (1/4 胜)
    ("002281.SZ", "20260520", "BUY",  230.89,  215.87, "光迅科技"),
    ("300502.SZ", "20260520", "BUY",  572.50,  606.77, "新易盛(2nd)"),
    ("300308.SZ", "20260520", "BUY",  1030.00, 1037.98, "中际旭创(2nd)"),
    ("601688.SH", "20260520", "BUY",  18.70,   18.16,  "华泰证券(2nd)"),
    # 反例: 框架打 WATCH 但实际涨 +15% 的票
    ("002371.SZ", "20260513", "WATCH", 581.00,  669.00, "北方华创"),
]


def main():
    print("=" * 78)
    print("Signal Collector v1 — 12 历史样本盲测")
    print("=" * 78)

    results = []
    for ts_code, date, action, entry, current, name in SAMPLES:
        pnl = (current - entry) / entry * 100
        # 不传 P&L 给 collect, 完全独立打分
        try:
            r = collect(ts_code, date)
        except Exception as e:
            print(f"\n!! {ts_code} @ {date} 打分失败: {e}")
            continue
        results.append({
            "name": name,
            "ts_code": ts_code,
            "date": date,
            "action": action,
            "entry": entry,
            "current": current,
            "pnl_pct": pnl,
            "win": pnl >= 0,
            "report": r,
        })
        print(f"\n┌─ {name:14s}  {action:5s} entry={entry:8.2f}  current={current:8.2f}  P&L={pnl:+.1f}%  {'✅ 胜' if pnl >= 0 else '❌ 负'}")
        _print_report(r, indent="│ ")

    # 汇总
    print("\n" + "=" * 78)
    print("汇总表")
    print("=" * 78)
    print(f"{'name':<14s} {'action':<6s} {'P&L':>8s}  {'实际':>4s}  {'新分':>5s}  {'verdict':>8s}  对错")
    print("-" * 78)

    correct = 0
    total = 0
    blocked = 0
    saved_pnl = 0
    for x in results:
        r: any = x["report"]
        actual_win = "胜" if x["win"] else "负"
        match = ""
        if x["action"] == "BUY":
            total += 1
            # 新框架的"对":
            #   GO + 真胜 → 对  (TP)
            #   GO + 真负 → 错  (FP, 还是会买错)
            #   NEUTRAL/NO_GO + 真负 → 对 (TN, 成功避坑)
            #   NEUTRAL/NO_GO + 真胜 → 错 (FN, 错过机会)
            if r.verdict == "GO" and x["win"]:
                match = "✅ TP"
                correct += 1
            elif r.verdict in ("NEUTRAL", "NO_GO") and not x["win"]:
                match = "✅ TN"
                correct += 1
                blocked += 1
                saved_pnl += abs(x["pnl_pct"])
            elif r.verdict == "GO" and not x["win"]:
                match = "❌ FP"
            else:
                match = "❌ FN"
        else:  # WATCH
            # WATCH 反例: 实际涨了, 看新框架会不会捕获 (verdict=GO)
            if r.verdict == "GO" and x["win"]:
                match = "✅ 捕获"
            else:
                match = "❌ 漏掉"

        print(
            f"{x['name']:<14s} {x['action']:<6s} {x['pnl_pct']:>+7.1f}%  {actual_win:>4s}  "
            f"{r.total_score:>+5d}  {r.verdict:>8s}  {match}"
        )

    print("-" * 78)
    print(f"BUY 样本: {total} 条 / 新框架命中 {correct}/{total} = {correct/total*100:.0f}%")
    print(f"  避坑: {blocked} 条败仗被 NEUTRAL/NO_GO 挡住, 累计避免 -{saved_pnl:.1f}% 浮亏")
    print()

    # 按 verdict 分组看分布
    print("Verdict × 胜负分布:")
    from collections import Counter
    grouped = Counter()
    for x in results:
        if x["action"] == "BUY":
            grouped[(x["report"].verdict, x["win"])] += 1
    for k, v in sorted(grouped.items()):
        v_str = "胜" if k[1] else "负"
        print(f"  {k[0]:>8s} × {v_str}: {v}")

    # 输出 JSON 给后续分析
    out_path = os.path.join(HERE, "backtest_signals_results.json")
    with open(out_path, "w") as f:
        serial = []
        for x in results:
            d = {k: v for k, v in x.items() if k != "report"}
            d["report"] = asdict(x["report"])
            serial.append(d)
        json.dump(serial, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n详细 JSON 写到 {out_path}")


if __name__ == "__main__":
    main()
