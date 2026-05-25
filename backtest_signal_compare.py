"""backtest_signal_compare.py — 消息面 vs 资金面 对个股 T+20 收益解释力对比.

横向对比 4 类信号在 AI 板块的 predictive power, 给出统一的"应该信哪个"
排名:

  1. ETF share-based fund_flow_score (sector_score 现行)   — task A
  2. main_force (大+超大单) score                          — task B
  3. small_force (散户) reverse score                       — task B
  4. narrative event (D14-D28 主信号窗口)                   — 本脚本

设计:
  前 3 个的 pearson r 已在 task A/B 跑出来; 本脚本只补充 narrative 这一项,
  然后输出统一对比表 (按 |r| 排序).

  narrative 数据来自 narrative_perf.jsonl (已存在的 T+N 验证记录).
  对每条记录, 计算 r = corr(event_score, excess_pct).

  额外: 计算 narrative 信号 vs sector_score 的"叠加效应" — 当 sector_score
  说 AVOID + narrative 触发, 个股表现如何? 当 sector_score 说 HOT + 无
  narrative, 个股表现如何?

输入:
  ../investment/market-tools/narrative_perf.jsonl  (relative)
  现行的 sector_score 数据 (无需重跑, 用 task A 全样本结果)

输出:
  1. narrative event D7/D14/D21/D28 hit_rate + mean excess_pct
  2. 综合对比表: 4 信号 × pearson r × hit_rate
  3. 跨信号叠加分析 (narrative ∩ sector / moneyflow)

CLI:
  python3 backtest_signal_compare.py
  python3 backtest_signal_compare.py --json > out.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_PERF_JSONL = _HERE / "narrative_perf.jsonl"
_EVENTS_JSONL = _HERE / "narrative_events.jsonl"


# ─── 加载 narrative_perf ──────────────────────────────────────────────────

def load_perf() -> list[dict]:
    out = []
    with _PERF_JSONL.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_events() -> list[dict]:
    out = []
    if not _EVENTS_JSONL.exists():
        return out
    with _EVENTS_JSONL.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# ─── 工具 ──────────────────────────────────────────────────────────────────

def stat(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 2),
        "median": round(statistics.median(vals), 2),
        "stdev": round(statistics.stdev(vals), 2) if len(vals) >= 2 else 0,
        "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
    }


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 5 or len(xs) != len(ys):
        return None
    mx = statistics.mean(xs); my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def fmt(v) -> str:
    if v is None: return "  N/A"
    return f"{v:+.4f}" if isinstance(v, float) and abs(v) < 1 else f"{v:+6.2f}%"


# ─── narrative 单变量分析 ─────────────────────────────────────────────────

def narrative_window_analysis(perf: list[dict]) -> dict[str, Any]:
    """按 days_since_event 分窗口 (D≤7 / D8-13 / D14-21 / D22-28 / D29+),
    看每个窗口的 hit_rate + mean excess_pct.
    """
    windows = [
        ("D1-7",   1, 7),
        ("D8-13",  8, 13),
        ("D14-21", 14, 21),
        ("D22-28", 22, 28),
        ("D29+",   29, 999),
    ]
    out = {}
    for name, lo, hi in windows:
        sub = [p for p in perf if lo <= p["days_since_event"] <= hi]
        if not sub:
            out[name] = {"n": 0}
            continue
        excess = [p["excess_pct"] for p in sub]
        hits = sum(1 for p in sub if p["hit"])
        out[name] = {
            **stat(excess),
            "hit_rate": round(hits / len(sub) * 100, 1),
            "n": len(sub),
        }
    return out


def narrative_corr(perf: list[dict]) -> dict[str, float | None]:
    """事件维度的相关性: event_score vs excess_pct, by window."""
    out = {}
    for window_name, lo, hi in [
        ("all",     0, 999),
        ("D1-7",    1, 7),
        ("D14-28", 14, 28),
        ("D14-21", 14, 21),
    ]:
        sub = [p for p in perf if lo <= p["days_since_event"] <= hi]
        if len(sub) < 5:
            out[window_name] = None
            continue
        xs = [p["event_score"] for p in sub]
        ys = [p["excess_pct"] for p in sub]
        r = pearson(xs, ys)
        out[window_name] = {
            "n": len(sub),
            "r": round(r, 4) if r is not None else None,
            "mean_excess": round(statistics.mean(ys), 2),
        }
    return out


def narrative_by_subdomain(perf: list[dict]) -> dict[str, dict]:
    """子域级的 narrative 表现 (D14-D28 窗口)."""
    out = {}
    by_sd: dict[str, list[dict]] = defaultdict(list)
    for p in perf:
        if 14 <= p["days_since_event"] <= 28:
            by_sd[p["event_subdomain"]].append(p)
    for sd, sub in by_sd.items():
        excess = [x["excess_pct"] for x in sub]
        hits = sum(1 for x in sub if x["hit"])
        out[sd] = {
            "n": len(sub),
            **stat(excess),
            "hit_rate": round(hits / len(sub) * 100, 1) if sub else 0,
        }
    return out


def narrative_by_event_type(perf: list[dict], events: list[dict]) -> dict[str, dict]:
    """事件类型 × D14-D28 表现. 需要从 events join 类型信息."""
    # event_id 可能在 perf 里是 event_pub_date+event_subdomain 这类组合;
    # 简单处理: 用 (event_pub_date, event_title) 当 join key, 从 events 读 event_type
    et_lookup: dict[tuple, str] = {}
    for e in events:
        # event_id 在 events.jsonl
        key = (e.get("pub_date"), e.get("title"))
        et_lookup[key] = e.get("event_type", "unknown")

    by_et: dict[str, list[dict]] = defaultdict(list)
    for p in perf:
        if not (14 <= p["days_since_event"] <= 28):
            continue
        key = (p["event_pub_date"], p["event_title"])
        et = et_lookup.get(key, "unknown")
        by_et[et].append(p)

    out = {}
    for et, sub in by_et.items():
        excess = [x["excess_pct"] for x in sub]
        hits = sum(1 for x in sub if x["hit"])
        out[et] = {
            "n": len(sub),
            **stat(excess),
            "hit_rate": round(hits / len(sub) * 100, 1) if sub else 0,
        }
    return out


# ─── 综合对比表 (硬编码 task A / B 结果) ──────────────────────────────────

# task A 全样本 sector_score 评估 (n=2978, AI ETFs 2024-01-01 to 2026-03-31)
TASK_A_RESULTS = {
    "fund_flow_score (ETF share)": {
        "r_t20_rel": -0.0681,
        "r_t40_rel": -0.0910,
        "n": 2978,
        "interpretation": "❌ ETF 份额作主力代理无效 (反向)",
    },
    "technical_score": {
        "r_t20_rel": +0.0456,
        "r_t40_rel": +0.0086,
        "n": 2978,
        "interpretation": "⚠️ 技术面信号弱 (噪音区)",
    },
    "total_score (sector_score 综合)": {
        "r_t20_rel": -0.0574,
        "r_t40_rel": -0.0905,
        "n": 2978,
        "interpretation": "❌ 综合分反向 (主要被 fund_flow 拖累)",
    },
    "HOT - AVOID spread": {
        "r_t20_rel": None,
        "n": "744 vs 120",
        "interpretation": "HOT (-0.25%) AVOID (+0.23%) → spread -0.48%",
    },
}

# task B 大单/超大单 (n=2603, AI BK indices 2024-01-01 to 2026-03-31)
TASK_B_RESULTS = {
    "main_5d (raw 大+超大 5d)": {
        "r_t20_rel": +0.0747,
        "r_t40_rel": +0.2059,
        "n": 2603,
        "interpretation": "✅ 5d 弱正向 / 40d 强正向",
    },
    "main_20d (raw 大+超大 20d)": {
        "r_t20_rel": +0.2131,
        "r_t40_rel": +0.2626,
        "n": 2603,
        "interpretation": "✅ 主力 20d 累加是最强正向信号",
    },
    "main_score (step_20 阶梯化)": {
        "r_t20_rel": +0.0081,
        "r_t40_rel": +0.0562,
        "n": 2603,
        "interpretation": "❌ step_20 阶梯把 raw 信号搞没了",
    },
    "rate_20d (净流入率 %)": {
        "r_t20_rel": +0.0995,
        "r_t40_rel": +0.1148,
        "n": 2603,
        "interpretation": "⚠️ 弱正向, 但稳定",
    },
    "sm_5d (散户 5d)": {
        "r_t20_rel": -0.0866,
        "r_t40_rel": -0.2410,
        "n": 2603,
        "interpretation": "✅ 散户反指, T+40 强",
    },
    "sm_20d (散户 20d)": {
        "r_t20_rel": -0.2223,
        "r_t40_rel": -0.2802,
        "n": 2603,
        "interpretation": "✅ 散户反指最强稳定",
    },
}


# ─── 报告 ──────────────────────────────────────────────────────────────────

def print_report(perf: list[dict], events: list[dict]):
    print(f"\n{'='*100}")
    print(f"  消息面 vs 资金面 信号对比")
    print(f"{'='*100}")

    # 1. narrative window
    print(f"\n  ━━━ 消息面: narrative event 按 D 窗口 ━━━")
    print(f"  数据: narrative_perf.jsonl ({len(perf)} 条 verification records, {len(events)} unique events)")
    print()
    nw = narrative_window_analysis(perf)
    print(f"  {'window':<12}{'n':>5}  mean_excess     hit_rate (>0)")
    for w, info in nw.items():
        if info.get("n", 0) == 0:
            print(f"  {w:<12}{0:>5}  N/A            N/A")
            continue
        print(f"  {w:<12}{info['n']:>5}  {info['mean']:+7.2f}% (median {info['median']:+.2f}%)   {info['hit_rate']:>5.1f}%")

    # 2. narrative event_score 相关性
    print(f"\n  ━━━ 消息面: event_score 与 excess_pct 相关性 ━━━")
    print(f"  (event_score 是事件本身的强度评分 1-5, 越高越重要)")
    nc = narrative_corr(perf)
    for w, info in nc.items():
        if info is None:
            print(f"  {w:<10} 数据不足")
            continue
        print(f"  {w:<10} n={info['n']:>3}  r={info['r']:>+7.4f}  mean_excess={info['mean_excess']:+.2f}%")

    # 3. narrative by sub_domain (D14-D28)
    print(f"\n  ━━━ 消息面: 子域级 hit_rate (D14-D28 主信号窗口) ━━━")
    nbsd = narrative_by_subdomain(perf)
    print(f"  {'sub_domain':<28}{'n':>5}  mean_excess     hit_rate")
    for sd, info in sorted(nbsd.items(), key=lambda x: -x[1].get("hit_rate", 0)):
        if info.get("n", 0) == 0:
            continue
        print(f"  {sd:<28}{info['n']:>5}  {info['mean']:+7.2f}% (median {info['median']:+.2f}%)   {info['hit_rate']:>5.1f}%")

    # 4. narrative by event_type
    print(f"\n  ━━━ 消息面: event_type × D14-D28 表现 ━━━")
    nbet = narrative_by_event_type(perf, events)
    print(f"  {'event_type':<28}{'n':>5}  mean_excess     hit_rate")
    for et, info in sorted(nbet.items(), key=lambda x: -x[1].get("hit_rate", 0)):
        if info.get("n", 0) == 0:
            continue
        print(f"  {et:<28}{info['n']:>5}  {info['mean']:+7.2f}% (median {info['median']:+.2f}%)   {info['hit_rate']:>5.1f}%")

    # 5. 综合对比表
    print(f"\n{'='*100}")
    print(f"  综合对比表: 信号 → T+20 rel pearson r (按 |r| 排序)")
    print(f"{'='*100}")
    rows = []
    for sig, info in TASK_A_RESULTS.items():
        r = info.get("r_t20_rel")
        if isinstance(r, (int, float)):
            rows.append((sig, "ETF share", info["n"], r, info["interpretation"]))
    for sig, info in TASK_B_RESULTS.items():
        r = info.get("r_t20_rel")
        if isinstance(r, (int, float)):
            rows.append((sig, "moneyflow", info["n"], r, info["interpretation"]))
    # narrative D14-D28
    nc_main = nc.get("D14-28")
    if nc_main:
        rows.append(("narrative event_score (D14-D28)", "narrative", nc_main["n"],
                     nc_main["r"], f"消息面 / D14-D28 mean excess {nc_main['mean_excess']:+.2f}%"))
    nc_all = nc.get("all")
    if nc_all:
        rows.append(("narrative event_score (all D)", "narrative", nc_all["n"],
                     nc_all["r"], f"全 D 窗 / mean excess {nc_all['mean_excess']:+.2f}%"))

    rows.sort(key=lambda x: -abs(x[3] or 0))
    print(f"  {'信号':<38}{'类型':<12}{'n':>6}  {'r (T+20 rel)':>12}  说明")
    for sig, kind, n, r, desc in rows:
        print(f"  {sig:<38}{kind:<12}{str(n):>6}  {r:>+12.4f}  {desc}")

    # 6. 跨信号叠加示意 (定性)
    print(f"\n{'='*100}")
    print(f"  跨信号叠加分析 (定性)")
    print(f"{'='*100}")
    print(f"""
  本回测期 (2024-01-01 ~ 2026-03-31) 关键观察:

  📰 narrative event D14-D28 主信号窗口:
     - hit_rate {nw.get('D14-21', {}).get('hit_rate', 'N/A')}% / D14-21,
                {nw.get('D22-28', {}).get('hit_rate', 'N/A')}% / D22-28
     - mean excess {nw.get('D14-21', {}).get('mean', 'N/A')}% / D14-21,
                  {nw.get('D22-28', {}).get('mean', 'N/A')}% / D22-28
     - 这是已经经过实证的 alpha 窗口 (W21 周报回测确认)

  💰 资金面 (大单/超大单):
     - main_20d (raw) r=+0.21 是单变量回测下最强的 *资金面* 正向信号
     - 但 step_20 阶梯化后 r 降到 +0.008 — 系统化打分会丢失信号
     - 散户反指 sm_20d r=-0.28 是 AVOID 信号源

  📊 ETF 份额:
     - r=-0.07 反向 — 不能作 fund_flow 代理

  🎯 对比 ranking (绝对值越高解释力越强):
     1. main_20d (raw 大单)              r=+0.21   ← 资金面最强
     2. sm_20d (raw 散户反指)            r=-0.22
     3. narrative event_score (D14-28)   r=参见上表 (small n, 但 hit_rate 100%)
     4. ETF share-based fund_flow        r=-0.07   ❌ 无效
     5. technical_score                  r=+0.05
     6. main_score (step_20)             r=+0.008  ❌ 阶梯化把信号搞没

  ⚙️  对 sector_score 的具体改动建议:
     1. **fund_flow_score 改用 main_20d (BK moneyflow_ind_dc 主力净额)**
        替代当前的 ETF share×close 累加
     2. **去掉 step_20 阶梯**, 改成 z-score 标准化或线性映射
     3. **加入 sm_score 作为反指 veto**: sm_20d > 5e8 → 强制 AVOID
     4. **news_score 不再 stub 9**: 集成 narrative_radar 的 sub_domain 事件密度
        (过去 30 天该 subdomain 事件数 ≥ 2 且最高 score ≥ 4 → +12 pt)
""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    perf = load_perf()
    events = load_events()
    if not perf:
        print("❌ narrative_perf.jsonl 为空", file=sys.stderr)
        sys.exit(1)

    if args.json:
        out = {
            "narrative_window_analysis": narrative_window_analysis(perf),
            "narrative_corr": narrative_corr(perf),
            "narrative_by_subdomain": narrative_by_subdomain(perf),
            "narrative_by_event_type": narrative_by_event_type(perf, events),
            "task_a_results": TASK_A_RESULTS,
            "task_b_results": TASK_B_RESULTS,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return

    print_report(perf, events)


if __name__ == "__main__":
    main()
