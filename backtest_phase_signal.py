"""backtest_phase_signal.py — 验证 sector_score 的反向信号是否真信号.

诊断起源 (2026-05-25 W22 day 1):
  当天 AI芯片 ETF (159995/512480) 5日 nav +8.56%, position_pct=98,
  fund_share 5日累计 -14.9 亿元 → sector_score 给 26.5 (AVOID).
  同期 光通信 ETF 5日 nav -2.83%, share 大额净流入 → 给 77.8 (HOT).
  但实际市场在 5/25 继续追涨芯片 (寒武纪 +7.08%, 5d +13.15%),
  CPO 链跌 (中际旭创 5d -1.05%).

  问题: sector_score 的"价格上 + 份额减 + 高位 = AVOID"是 distribution
  detector (机构出货) 还是错误的反向信号? 需历史数据回答.

测试设计:
  对每个 AI 链 ETF, 走过去 ~250 个交易日, 每天 (as-of) 计算:
    nav_pct_5d, position_pct (60d/120d/250d percentile rank),
    share_chg_5d_cny (份额变动 × 收盘价)
  按以下规则打 phase 标签:
    distribution_phase: pos_60d ≥ 95 AND share_5d ≤ -3亿 AND nav_5d ≥ +5%
    accumulation_phase: pos_60d ≤ 30 AND share_5d ≥ +3亿 AND nav_5d ≤ -3%
    其他               : neutral
  然后看 T+20 / T+40 后:
    abs_return: 该 ETF 的实际 NAV 涨跌
    rel_return: 减去 AI 板块均值 (8 只 AI ETF 当期均值)
  按 phase 聚合, 统计 mean / median / hit_rate.

判定标准:
  反向信号有效 (sector_score 的 -40 资金面权重对):
    distribution_phase  T+20/T+40 mean rel_return ≤ -3% AND hit_rate ≥ 60%
    accumulation_phase  T+20/T+40 mean rel_return ≥ +3% AND hit_rate ≥ 60%
  反向信号在 AI 趋势市失效 (需要重新设计):
    任意一个 phase mean |rel_return| < 2% OR hit_rate ~50%

CLI:
  python3 backtest_phase_signal.py
  python3 backtest_phase_signal.py --start 20250401 --end 20260331
  python3 backtest_phase_signal.py --json > out.json    # JSON 给下游

输出:
  per-ETF 信号触发统计 + 全局 phase 表 + 时序分布 (early/mid/late phase 落地年份)
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"


# AI 链 8 只核心 ETF (从 concept_etf_map.yaml 抽出)
AI_ETFS = [
    {"code": "159995.SZ", "name": "芯片 ETF",          "sub_domain": "ai_chip"},
    {"code": "512480.SH", "name": "半导体 ETF",        "sub_domain": "ai_chip"},
    {"code": "515880.SH", "name": "通信 ETF 国泰",     "sub_domain": "cpo"},
    {"code": "159583.SZ", "name": "通信 ETF 富国",     "sub_domain": "cpo"},
    {"code": "515050.SH", "name": "通信 ETF 华夏",     "sub_domain": "cpo"},
    {"code": "159363.SZ", "name": "创业板 AI 华宝",    "sub_domain": "ai_app"},
    {"code": "159819.SZ", "name": "AI ETF 易方达",     "sub_domain": "ai_app"},
    {"code": "588790.SH", "name": "科创 AI ETF",       "sub_domain": "ai_app"},
]


# ─── tushare fetch ────────────────────────────────────────────────────────

def _tushare_csv(api: str, **params) -> list[dict[str, str]]:
    args = ["python3", str(_TUSHARE), api]
    for k, v in params.items():
        if k == "fields":
            args.append(f"--fields={v}")
        else:
            args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def fetch_full_daily(ts_code: str) -> list[dict[str, Any]]:
    """完整 fund_daily (后复权) 史; 返回按 trade_date 升序."""
    rows = _tushare_csv("fund_daily", ts_code=ts_code,
                        fields="ts_code,trade_date,close,vol,amount")
    if not rows:
        return []
    adj_rows = _tushare_csv("fund_adj", ts_code=ts_code, fields="ts_code,trade_date,adj_factor")
    adj_by_date: dict[str, float] = {}
    for ar in adj_rows:
        try:
            adj_by_date[ar["trade_date"]] = float(ar["adj_factor"])
        except (KeyError, ValueError):
            continue
    latest_adj = adj_by_date[max(adj_by_date.keys())] if adj_by_date else 1.0

    parsed = []
    for r in rows:
        try:
            raw = float(r["close"])
            adj = adj_by_date.get(r["trade_date"], latest_adj)
            parsed.append({
                "trade_date": r["trade_date"],
                "close": raw * adj / latest_adj,  # 后复权
            })
        except (KeyError, ValueError):
            continue
    parsed.sort(key=lambda x: x["trade_date"])
    return parsed


def fetch_full_share(ts_code: str) -> dict[str, float]:
    """完整 fund_share 史; 返回 {trade_date: fd_share (万份)}."""
    rows = _tushare_csv("fund_share", ts_code=ts_code,
                        fields="ts_code,trade_date,fd_share")
    by_date: dict[str, float] = {}
    for r in rows:
        try:
            by_date[r["trade_date"]] = float(r["fd_share"])
        except (KeyError, ValueError):
            continue
    return by_date


# ─── 信号计算 (as-of date) ─────────────────────────────────────────────────

def percentile_rank(window: list[float], val: float) -> int:
    """val 在 window 中的百分位 (0-100). 与 etf_data._percentile_of 一致."""
    if not window:
        return 50
    lower = sum(1 for x in window if x < val)
    equal = sum(1 for x in window if x == val)
    return int(round((lower + 0.5 * equal) / len(window) * 100))


@dataclass
class AsOfMetrics:
    ts_code: str
    date: str               # YYYYMMDD
    close: float
    nav_pct_5d: float
    pct_rank_60d: int
    pct_rank_120d: int
    pct_rank_250d: int
    share_chg_5d_cny: float  # 5 日累计份额变动 × close (单位: 元)
    share_data_days: int     # 算 share_5d 实际用了多少天 share 数据
    fwd_20d_return: float | None  # T+20 NAV 涨跌 %, None = 数据不足
    fwd_40d_return: float | None  # T+40
    phase: str               # distribution / accumulation / neutral / unknown


def compute_asof_metrics(daily: list[dict], shares_by_date: dict[str, float],
                         idx: int, fwd_20: int, fwd_40: int) -> AsOfMetrics | None:
    """对 daily[idx] 这天 (as-of) 计算所有指标 + 前瞻收益.
    需要 idx >= 60 (基本位置窗口) 和能取到一定数量 share 数据.
    """
    if idx < 60:
        return None  # 位置窗口不够
    today = daily[idx]
    closes_up_to = [d["close"] for d in daily[:idx + 1]]
    last_close = today["close"]

    # nav_pct_5d
    if idx >= 5 and daily[idx - 5]["close"] > 0:
        nav_5d = (last_close / daily[idx - 5]["close"] - 1) * 100
    else:
        nav_5d = 0.0

    # 位置 (3 个窗口)
    win_60 = closes_up_to[-60:]
    win_120 = closes_up_to[-120:] if len(closes_up_to) >= 120 else closes_up_to
    win_250 = closes_up_to[-250:] if len(closes_up_to) >= 250 else closes_up_to
    pct_60 = percentile_rank(win_60, last_close)
    pct_120 = percentile_rank(win_120, last_close)
    pct_250 = percentile_rank(win_250, last_close)

    # share_chg_5d_cny: 用 daily 最近 5 天的 trade_date, 在 shares_by_date 找匹配
    # 计算 (share[d] - share[d-1]) * close[d]
    share_chg_5d = 0.0
    share_data_days = 0
    for j in range(max(0, idx - 4), idx + 1):
        d_today = daily[j]["trade_date"]
        # 找上一个交易日 (在 daily 中 j-1)
        if j == 0:
            continue
        d_prev = daily[j - 1]["trade_date"]
        s_today = shares_by_date.get(d_today)
        s_prev = shares_by_date.get(d_prev)
        if s_today is None or s_prev is None:
            continue
        delta_cny = (s_today - s_prev) * 10000 * daily[j]["close"]
        share_chg_5d += delta_cny
        share_data_days += 1

    # 前瞻收益
    fwd_20 = None
    fwd_40 = None
    if idx + 20 < len(daily) and last_close > 0:
        fwd_20 = (daily[idx + 20]["close"] / last_close - 1) * 100
    if idx + 40 < len(daily) and last_close > 0:
        fwd_40 = (daily[idx + 40]["close"] / last_close - 1) * 100

    # phase
    phase = classify_phase(nav_5d, pct_60, share_chg_5d, share_data_days)

    return AsOfMetrics(
        ts_code="",  # 调用方填
        date=today["trade_date"],
        close=last_close,
        nav_pct_5d=round(nav_5d, 2),
        pct_rank_60d=pct_60,
        pct_rank_120d=pct_120,
        pct_rank_250d=pct_250,
        share_chg_5d_cny=round(share_chg_5d, 0),
        share_data_days=share_data_days,
        fwd_20d_return=round(fwd_20, 2) if fwd_20 is not None else None,
        fwd_40d_return=round(fwd_40, 2) if fwd_40 is not None else None,
        phase=phase,
    )


def classify_phase(nav_5d: float, pct_60: int, share_5d_cny: float,
                   share_days: int) -> str:
    """phase 分类规则.
    distribution_phase: 高位 + 价格涨 + 份额减 (典型机构派发)
    accumulation_phase: 低位 + 价格跌 + 份额增 (典型机构吸筹)
    其他               : neutral
    """
    if share_days < 3:
        return "unknown"  # share 数据不足无法判定
    DIST_FLOW_THRESH = -3e8   # -3 亿
    ACC_FLOW_THRESH = +3e8    # +3 亿
    if pct_60 >= 95 and share_5d_cny <= DIST_FLOW_THRESH and nav_5d >= 5.0:
        return "distribution"
    if pct_60 <= 30 and share_5d_cny >= ACC_FLOW_THRESH and nav_5d <= -3.0:
        return "accumulation"
    return "neutral"


# ─── 主流程 ────────────────────────────────────────────────────────────────

def run_backtest(start_date: str, end_date: str) -> dict[str, Any]:
    """
    start_date / end_date: YYYYMMDD. 信号生成日的范围.
    返回 dict {meta, per_etf, all_signals, aggregated}
    """
    # 拉所有数据
    print(f"📥 加载 {len(AI_ETFS)} 只 AI ETF 数据 ...", file=sys.stderr)
    etf_data = {}
    for e in AI_ETFS:
        code = e["code"]
        daily = fetch_full_daily(code)
        shares = fetch_full_share(code)
        etf_data[code] = {
            "name": e["name"],
            "sub_domain": e["sub_domain"],
            "daily": daily,
            "shares": shares,
        }
        print(f"  {code} {e['name']}: daily {len(daily)} rows ({daily[0]['trade_date'] if daily else '?'} ~ {daily[-1]['trade_date'] if daily else '?'}), share {len(shares)} dates", file=sys.stderr)

    # 走每个 ETF, 每个交易日生成 metrics
    print(f"\n🔄 walking dates {start_date} → {end_date} ...", file=sys.stderr)
    all_signals: list[dict] = []
    per_etf_summary: dict[str, dict] = {}

    for code, info in etf_data.items():
        daily = info["daily"]
        shares = info["shares"]
        if len(daily) < 100:
            print(f"  ⚠️  {code}: daily 数据不足 ({len(daily)}); 跳过", file=sys.stderr)
            continue

        # 找 idx 范围
        signals_for_etf = []
        for idx, d in enumerate(daily):
            td = d["trade_date"]
            if td < start_date or td > end_date:
                continue
            m = compute_asof_metrics(daily, shares, idx, 20, 40)
            if not m:
                continue
            m.ts_code = code
            d_dict = asdict(m)
            d_dict["sub_domain"] = info["sub_domain"]
            d_dict["name"] = info["name"]
            signals_for_etf.append(d_dict)
            all_signals.append(d_dict)

        # 单 ETF 统计
        phase_counts: dict[str, int] = {}
        for s in signals_for_etf:
            phase_counts[s["phase"]] = phase_counts.get(s["phase"], 0) + 1
        per_etf_summary[code] = {
            "name": info["name"],
            "sub_domain": info["sub_domain"],
            "total_days": len(signals_for_etf),
            "phase_counts": phase_counts,
        }

    # 计算 AI 板块整体均值 (按日期), 用于 rel_return
    print(f"\n📊 计算每日 AI 板块均值 (benchmark) ...", file=sys.stderr)
    by_date_returns: dict[str, dict[str, list[float]]] = {}
    for s in all_signals:
        d = s["date"]
        if d not in by_date_returns:
            by_date_returns[d] = {"fwd_20": [], "fwd_40": []}
        if s["fwd_20d_return"] is not None:
            by_date_returns[d]["fwd_20"].append(s["fwd_20d_return"])
        if s["fwd_40d_return"] is not None:
            by_date_returns[d]["fwd_40"].append(s["fwd_40d_return"])
    benchmark = {}
    for d, ret in by_date_returns.items():
        benchmark[d] = {
            "ai_avg_fwd_20": (statistics.mean(ret["fwd_20"]) if ret["fwd_20"] else None),
            "ai_avg_fwd_40": (statistics.mean(ret["fwd_40"]) if ret["fwd_40"] else None),
        }

    # 给每条信号挂 rel_return
    for s in all_signals:
        bm = benchmark.get(s["date"], {})
        if s["fwd_20d_return"] is not None and bm.get("ai_avg_fwd_20") is not None:
            s["rel_fwd_20"] = round(s["fwd_20d_return"] - bm["ai_avg_fwd_20"], 2)
        else:
            s["rel_fwd_20"] = None
        if s["fwd_40d_return"] is not None and bm.get("ai_avg_fwd_40") is not None:
            s["rel_fwd_40"] = round(s["fwd_40d_return"] - bm["ai_avg_fwd_40"], 2)
        else:
            s["rel_fwd_40"] = None

    # 按 phase 聚合
    print(f"\n📈 按 phase 聚合 ...", file=sys.stderr)
    aggregated = aggregate_by_phase(all_signals)

    # 时序分布 (各 phase 落地的年/季度)
    time_dist = time_distribution(all_signals)

    return {
        "meta": {
            "start_date": start_date,
            "end_date": end_date,
            "etf_count": len(AI_ETFS),
            "total_signals": len(all_signals),
            "thresholds": {
                "distribution": "pct_rank_60d>=95 AND share_5d_cny<=-3亿 AND nav_5d>=+5%",
                "accumulation": "pct_rank_60d<=30 AND share_5d_cny>=+3亿 AND nav_5d<=-3%",
            },
        },
        "per_etf": per_etf_summary,
        "aggregated": aggregated,
        "time_dist": time_dist,
        "all_signals": all_signals,
    }


def aggregate_by_phase(signals: list[dict]) -> dict[str, Any]:
    """对每个 phase 算 mean / median / hit_rate / count, 同时拆 abs vs rel."""
    out = {}
    for phase in ["distribution", "accumulation", "neutral"]:
        sub = [s for s in signals if s["phase"] == phase]
        if not sub:
            out[phase] = {"count": 0}
            continue

        def stat(field: str, hit_op):
            vals = [s[field] for s in sub if s.get(field) is not None]
            if not vals:
                return None
            return {
                "n": len(vals),
                "mean": round(statistics.mean(vals), 2),
                "median": round(statistics.median(vals), 2),
                "stdev": round(statistics.stdev(vals), 2) if len(vals) >= 2 else 0,
                "hit_rate": round(sum(1 for v in vals if hit_op(v)) / len(vals) * 100, 1),
                "p25": round(statistics.quantiles(vals, n=4)[0], 2) if len(vals) >= 4 else None,
                "p75": round(statistics.quantiles(vals, n=4)[2], 2) if len(vals) >= 4 else None,
            }

        # 对 distribution: hit = underperform (return < 0 / rel_return < 0)
        # 对 accumulation: hit = outperform (return > 0 / rel_return > 0)
        if phase == "distribution":
            hit_abs = lambda v: v < 0
            hit_rel = lambda v: v < 0
        elif phase == "accumulation":
            hit_abs = lambda v: v > 0
            hit_rel = lambda v: v > 0
        else:  # neutral
            hit_abs = lambda v: v > 0  # 中性参考
            hit_rel = lambda v: v > 0

        out[phase] = {
            "count": len(sub),
            "abs_fwd_20": stat("fwd_20d_return", hit_abs),
            "abs_fwd_40": stat("fwd_40d_return", hit_abs),
            "rel_fwd_20": stat("rel_fwd_20", hit_rel),
            "rel_fwd_40": stat("rel_fwd_40", hit_rel),
        }
    return out


def time_distribution(signals: list[dict]) -> dict[str, dict[str, int]]:
    """信号按 phase × 年份 的分布."""
    out: dict[str, dict[str, int]] = {}
    for s in signals:
        phase = s["phase"]
        year = s["date"][:4]
        out.setdefault(phase, {}).setdefault(year, 0)
        out[phase][year] += 1
    return out


# ─── 报告 ──────────────────────────────────────────────────────────────────

def fmt_pct(v) -> str:
    if v is None:
        return "  N/A"
    return f"{v:+6.2f}%"


def print_report(result: dict[str, Any]):
    m = result["meta"]
    print(f"\n{'='*80}")
    print(f"  反向信号回测报告  ({m['start_date']} → {m['end_date']})")
    print(f"{'='*80}")
    print(f"  ETF 数: {m['etf_count']}, 信号样本数: {m['total_signals']}")
    print(f"  Distribution 定义: {m['thresholds']['distribution']}")
    print(f"  Accumulation 定义: {m['thresholds']['accumulation']}")

    # per-ETF
    print(f"\n{'─'*80}\n  per-ETF 信号触发统计\n{'─'*80}")
    print(f"  {'ETF':<14}{'name':<22}{'sub_domain':<12}{'days':>6}  phase 分布")
    for code, s in result["per_etf"].items():
        pc = s["phase_counts"]
        pc_str = f"dist={pc.get('distribution',0):>3}  acc={pc.get('accumulation',0):>3}  neut={pc.get('neutral',0):>4}  unk={pc.get('unknown',0):>3}"
        print(f"  {code:<14}{s['name']:<22}{s['sub_domain']:<12}{s['total_days']:>6}  {pc_str}")

    # 主表
    print(f"\n{'─'*80}\n  按 phase 聚合 — 4 周 / 8 周前瞻收益\n{'─'*80}")
    agg = result["aggregated"]
    for phase in ["distribution", "neutral", "accumulation"]:
        info = agg.get(phase, {"count": 0})
        print(f"\n  ━━━ {phase.upper()} (n={info['count']}) ━━━")
        if info["count"] == 0:
            print(f"    没有触发样本.")
            continue
        for label, key in [("绝对收益 T+20", "abs_fwd_20"),
                            ("绝对收益 T+40", "abs_fwd_40"),
                            ("相对 AI 板块 T+20", "rel_fwd_20"),
                            ("相对 AI 板块 T+40", "rel_fwd_40")]:
            d = info.get(key)
            if not d:
                continue
            print(f"    {label:<22}  n={d['n']:>4}  mean={fmt_pct(d['mean'])}  median={fmt_pct(d['median'])}  stdev={d['stdev']:>5.2f}  hit_rate={d['hit_rate']:>5.1f}%  [p25={fmt_pct(d['p25'])}, p75={fmt_pct(d['p75'])}]")

    # 时序
    print(f"\n{'─'*80}\n  各 phase 历史触发分布 (按年份)\n{'─'*80}")
    for phase, by_year in result["time_dist"].items():
        years = sorted(by_year.keys())
        ystr = "  ".join(f"{y}={by_year[y]}" for y in years)
        print(f"  {phase:<14}  {ystr}")

    # 判定
    print(f"\n{'='*80}\n  判定\n{'='*80}")
    dist_rel20 = agg.get("distribution", {}).get("rel_fwd_20")
    dist_rel40 = agg.get("distribution", {}).get("rel_fwd_40")
    acc_rel20 = agg.get("accumulation", {}).get("rel_fwd_20")
    acc_rel40 = agg.get("accumulation", {}).get("rel_fwd_40")

    def verdict(d, expect_neg=True):
        if not d:
            return "❓ 无数据"
        mean = d["mean"]
        hit = d["hit_rate"]
        if expect_neg:
            if mean <= -3.0 and hit >= 60:
                return "✅ 反向信号显著有效"
            if mean <= -1.0:
                return "⚠️  弱有效 (mean 在 -1% 到 -3% 之间)"
            if abs(mean) < 1.0:
                return "❌ 失效 (mean 接近 0)"
            return f"❌ 反向 (mean={mean:+.1f}%, 与预期相反)"
        else:
            if mean >= 3.0 and hit >= 60:
                return "✅ 反向信号显著有效"
            if mean >= 1.0:
                return "⚠️  弱有效"
            if abs(mean) < 1.0:
                return "❌ 失效"
            return f"❌ 反向 (mean={mean:+.1f}%)"

    print(f"  Distribution (高位+涨+份额减) → 期望 4-8 周相对 AI 板块 underperform:")
    print(f"    T+20: {verdict(dist_rel20, expect_neg=True)}")
    print(f"    T+40: {verdict(dist_rel40, expect_neg=True)}")
    print(f"  Accumulation (低位+跌+份额增) → 期望 4-8 周相对 AI 板块 outperform:")
    print(f"    T+20: {verdict(acc_rel20, expect_neg=False)}")
    print(f"    T+40: {verdict(acc_rel40, expect_neg=False)}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20240101", help="信号生成起始日 YYYYMMDD")
    ap.add_argument("--end", default="20260331", help="信号生成结束日 (留 40 日前瞻空间)")
    ap.add_argument("--json", action="store_true", help="JSON 输出 (省略 all_signals)")
    ap.add_argument("--full-json", action="store_true", help="JSON 输出含 all_signals (大)")
    args = ap.parse_args()

    result = run_backtest(args.start, args.end)

    if args.full_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.json:
        slim = {k: v for k, v in result.items() if k != "all_signals"}
        print(json.dumps(slim, ensure_ascii=False, indent=2))
        return

    print_report(result)


if __name__ == "__main__":
    main()
