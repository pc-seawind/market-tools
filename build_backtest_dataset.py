#!/usr/bin/env python3
"""build_backtest_dataset.py — 把 recommendations.jsonl 的 21 条决策
归一化成 backtest_dataset.jsonl, 每条带 T+max 实际收益.

输入:
  recommendations.jsonl              (21 条 raw decisions, 两套 schema)
  recommendations_performance.jsonl  (149 条 T+N 跟踪)

输出:
  backtest_dataset.jsonl  -- 每行:
    {
      "id":           "rec_20260513_0001" 或 syn-<idx>,
      "ts_code":      "601688.SH",
      "name":         "华泰证券",
      "decision_date":"20260513",
      "action":       "BUY" / "WATCH" / "HOLD" / "EXIT" / "TREND_BUY",
      "entry_price":  19.48,
      "horizon_days": 7,                      # T+实际跟踪了多少天 (max)
      "exit_price":   18.16,
      "pnl_pct":      -6.78,
      "win":          false,                  # pnl_pct >= 0
      "framework_version": "v2.3" / "v2.4" / "v2.5"
    }

注: "win" 的判定阈值 = 0%. 后续 backtest 可以叠加更严格的 +5% / +10% 阈值.
"""

import json
import os
import subprocess
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RECS = os.path.join(HERE, "recommendations.jsonl")
PERF = os.path.join(HERE, "recommendations_performance.jsonl")
OUT  = os.path.join(HERE, "backtest_dataset.jsonl")
TUSHARE_CLI = os.path.join(HERE, "tushare.py")
LATEST_TRADING_DATE = "20260522"  # 最新可观察日 (今天 5/23 周六, 5/22 是最近交易日)


def fetch_close_after(ts_code: str, start_date: str) -> tuple[str, float] | None:
    """从 tushare daily 拉 start_date → LATEST 的最远那天收盘.
    返回 (trade_date, close) 或 None.
    A 股用 daily, HK 用 hk_daily.
    """
    api = "hk_daily" if ts_code.endswith(".HK") else "daily"
    cmd = ["python3", TUSHARE_CLI, api,
           f"ts_code={ts_code}",
           f"start_date={start_date}",
           f"end_date={LATEST_TRADING_DATE}"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    try:
        body = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    data = body.get("data") or {}
    fields = data.get("fields") or []
    items = data.get("items") or []
    if not items or "trade_date" not in fields or "close" not in fields:
        return None
    idx_d = fields.index("trade_date")
    idx_c = fields.index("close")
    # tushare 返回降序, 取第一行 (即 LATEST)
    items.sort(key=lambda r: r[idx_d], reverse=True)
    return items[0][idx_d], items[0][idx_c]


def days_between(yyyymmdd_a: str, yyyymmdd_b: str) -> int:
    """近似交易日 = max(0, 自然日 / 1.4) — 5 个自然日 ≈ 3-4 个交易日."""
    import datetime as dt
    a = dt.datetime.strptime(yyyymmdd_a, "%Y%m%d")
    b = dt.datetime.strptime(yyyymmdd_b, "%Y%m%d")
    natural = (b - a).days
    return max(0, int(natural / 1.4))


def load_recs():
    recs = []
    for i, line in enumerate(open(RECS)):
        r = json.loads(line)
        # 归一化两套 schema
        ts_code = r.get("ts_code") or r.get("code")
        action  = r.get("action")  or r.get("type")
        entry   = r.get("price_at_rec") or r.get("entry_price")
        date    = (r.get("ts") or r.get("date") or "")[:10].replace("-", "")
        rec_id  = r.get("id") or f"syn-{i:03d}"
        fv      = r.get("framework_version") or r.get("framework") or "?"
        recs.append({
            "id":           rec_id,
            "ts_code":      ts_code,
            "name":         r.get("name", ""),
            "decision_date":date,
            "action":       action,
            "entry_price":  entry,
            "framework_version": fv,
            "raw":          r,
        })
    return recs


def load_perf_by_rec():
    """把 performance.jsonl 按 rec_id 聚合, 找出每个 rec 跟踪到的最远天数 + 那天的价."""
    by_rec = defaultdict(list)
    for line in open(PERF):
        p = json.loads(line)
        by_rec[p["rec_id"]].append(p)
    summary = {}
    for rid, rows in by_rec.items():
        rows.sort(key=lambda x: x["days_since_rec"])
        max_row = rows[-1]
        # 取 T+5 (优先) 或 T+max
        t5_row = next((r for r in rows if r["days_since_rec"] == 5), None)
        summary[rid] = {
            "horizon_days": max_row["days_since_rec"],
            "exit_price":   max_row["current_price"],
            "pnl_pct_max":  max_row["pct_change"],
            "t5_price":     t5_row["current_price"] if t5_row else None,
            "t5_pnl":       t5_row["pct_change"] if t5_row else None,
        }
    return summary


def main():
    recs = load_recs()
    perf = load_perf_by_rec()

    out_lines = []
    skipped   = []
    fallback_used = 0
    for r in recs:
        rid = r["id"]
        entry = (r["raw"].get("price_at_rec") or r["raw"].get("entry_price"))
        if rid in perf:
            p = perf[rid]
            sample = {
                "id":            rid,
                "ts_code":       r["ts_code"],
                "name":          r["name"],
                "decision_date": r["decision_date"],
                "action":        r["action"],
                "entry_price":   entry,
                "horizon_days":  p["horizon_days"],
                "exit_price":    p["exit_price"],
                "pnl_pct":       round(p["pnl_pct_max"], 2),
                "win":           p["pnl_pct_max"] >= 0,
                "t5_pnl":        round(p["t5_pnl"], 2) if p["t5_pnl"] is not None else None,
                "framework_version": r["framework_version"],
                "source":        "perf_jsonl",
            }
            out_lines.append(sample)
        else:
            # Fallback: 从 tushare 拉 decision_date → 最新的收盘
            res = fetch_close_after(r["ts_code"], r["decision_date"])
            if res is None or entry is None:
                skipped.append(r)
                continue
            exit_date, exit_close = res
            pnl_pct = (exit_close - entry) / entry * 100
            horizon = days_between(r["decision_date"], exit_date)
            sample = {
                "id":            rid,
                "ts_code":       r["ts_code"],
                "name":          r["name"],
                "decision_date": r["decision_date"],
                "action":        r["action"],
                "entry_price":   entry,
                "horizon_days":  horizon,
                "exit_price":    exit_close,
                "pnl_pct":       round(pnl_pct, 2),
                "win":           pnl_pct >= 0,
                "t5_pnl":        None,
                "framework_version": r["framework_version"],
                "source":        f"tushare_fallback@{exit_date}",
            }
            out_lines.append(sample)
            fallback_used += 1

    with open(OUT, "w") as f:
        for s in out_lines:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # 统计
    print(f"=== Backtest Dataset Built ===")
    print(f"输入 recommendations.jsonl: {len(recs)} 条")
    print(f"匹配到 perf 跟踪:            {len(out_lines)-fallback_used} 条")
    print(f"用 tushare fallback 补:       {fallback_used} 条")
    print(f"无法构造样本 (跳过):         {len(skipped)} 条")
    if skipped:
        print(f"  跳过样本:")
        for r in skipped:
            print(f"    {r['decision_date']} {r['action']:10s} {r['ts_code']:12s} {r['name']} (id={r['id']})")
    print()
    print(f"按 action 分布:")
    by_act = defaultdict(int)
    for s in out_lines:
        by_act[s["action"]] += 1
    for a, c in sorted(by_act.items()):
        wins = sum(1 for s in out_lines if s["action"]==a and s["win"])
        print(f"  {a:10s}: {c} 条 (胜 {wins}, 负 {c-wins})")
    print()
    print(f"horizon_days 分布:")
    by_h = defaultdict(int)
    for s in out_lines:
        by_h[s["horizon_days"]] += 1
    for h, c in sorted(by_h.items()):
        print(f"  T+{h:2d}: {c} 条")
    print()
    print(f"输出 → {OUT}")


if __name__ == "__main__":
    main()
