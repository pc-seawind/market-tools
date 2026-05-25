"""backtest_score_full.py — 全样本 sector_score 评估.

目的:
  backtest_phase_signal.py 只看了极端 phase trigger (n=127+57),
  本脚本对每个 (ETF, 交易日) 重算 sector_score (movable 部分:
  fund_flow_score 40 + technical_score 20), 看分桶 forward
  returns 是否符合 "score 高 = 后续涨" 的设计预期.

  额外: 对 fund_flow_score (40 max) 和 technical_score (20 max)
  各自独立分桶, 拆出到底是哪个子维度反向.

设计:
  对每个 AI ETF, 每个交易日 (idx >= 250 保证窗口足):
    - 计算 nav_pct_5d, nav_pct_1m
    - 计算 pct_rank_60d/120d/250d
    - 计算 share_chg_5d_cny, share_chg_20d_cny (用 close × Δshare 累加)
    - 计算 vol_ratio (今日 vol / 20d mean vol)
  然后:
    - movable_total = fund_flow_score(s5,s20) + technical_score(nav,pos,vol)
    - total_score = movable_total + 12 (fundamentals neutral) + 9 (news neutral)
  前瞻收益: T+5 / T+20 / T+40 / T+60, 绝对 + 相对 AI 8-ETF 板块均值
  分桶:
    HOT (≥60), NEUTRAL (45-60), COLD (30-45), AVOID (<30)
  另外按 fund_flow_score 单独分 (HIGH ≥30 / MID 15-30 / LOW <15)
  按 technical_score 单独分 (HIGH ≥15 / MID 8-15 / LOW <8)
  按年度切片 (2024 / 2025 / 2026), 看是否是 regime 问题.

  最后: pearson correlation (score, T+20 abs return)
        + (score, T+20 rel return), 数值越接近 0 越说明无信号,
        负值 = 反向, 正值 = 正向.

CLI:
  python3 backtest_score_full.py
  python3 backtest_score_full.py --start 20240101 --end 20260331
  python3 backtest_score_full.py --json > out.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"


# AI 链 8 只核心 ETF — 与 backtest_phase_signal.py 保持一致
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
    """完整 fund_daily (含 vol 后复权 close); 返回按 trade_date 升序."""
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
            vol = float(r.get("vol") or 0)
            parsed.append({
                "trade_date": r["trade_date"],
                "close": raw * adj / latest_adj,  # 后复权
                "raw_close": raw,                  # 原始 close, 用于 share × close 计算
                "vol": vol,
            })
        except (KeyError, ValueError):
            continue
    parsed.sort(key=lambda x: x["trade_date"])
    return parsed


def fetch_full_share(ts_code: str) -> dict[str, float]:
    """完整 fund_share; {trade_date: fd_share (万份)}."""
    rows = _tushare_csv("fund_share", ts_code=ts_code,
                        fields="ts_code,trade_date,fd_share")
    by_date: dict[str, float] = {}
    for r in rows:
        try:
            by_date[r["trade_date"]] = float(r["fd_share"])
        except (KeyError, ValueError):
            continue
    return by_date


# ─── 信号计算 (复刻 sector_score.py 的 score 函数, 与生产对齐) ─────────────

def percentile_rank(window: list[float], val: float) -> int:
    if not window:
        return 50
    lower = sum(1 for x in window if x < val)
    equal = sum(1 for x in window if x == val)
    return int(round((lower + 0.5 * equal) / len(window) * 100))


def fund_flow_score(flow_5d_cny: float, flow_20d_cny: float) -> float:
    """复刻 sector_score.fund_flow_score: 0-40."""
    def step_20(cny: float) -> float:
        if cny >= 5e8: return 20
        if cny >= 1e8: return 16
        if cny >= 3e7: return 13
        if cny >= 0:   return 10
        if cny >= -3e7: return 7
        if cny >= -1e8: return 4
        if cny >= -5e8: return 2
        return 0
    return step_20(flow_5d_cny) + step_20(flow_20d_cny)


def technical_score(nav_5d: float, pct_60: int, pct_250: int, vol_ratio: float) -> float:
    """复刻 sector_score.technical_score: 0-20."""
    if nav_5d > 10:    nav_sc = 10
    elif nav_5d > 5:   nav_sc = 8
    elif nav_5d > 0:   nav_sc = 6
    elif nav_5d > -3:  nav_sc = 3
    else:              nav_sc = 0

    if pct_60 >= 85 and pct_250 >= 85:
        pos_sc = 2
    elif 50 <= pct_60 < 85 and pct_250 < 85:
        pos_sc = 7
    elif pct_60 >= 85 and pct_250 < 70:
        pos_sc = 5
    elif pct_60 < 30:
        pos_sc = 3
    else:
        pos_sc = 5

    if vol_ratio >= 1.5:    vol_sc = 3
    elif vol_ratio >= 1.2:  vol_sc = 2
    elif vol_ratio >= 0.8:  vol_sc = 1
    else:                   vol_sc = 0

    return nav_sc + pos_sc + vol_sc


@dataclass
class AsOfFull:
    ts_code: str
    sub_domain: str
    name: str
    date: str
    close: float

    # raw signals
    nav_5d: float
    nav_1m: float
    pct_60: int
    pct_120: int
    pct_250: int
    flow_5d_cny: float       # share Δ × close 累加 5d
    flow_20d_cny: float      # 20d
    vol_ratio_20d: float

    # scores
    fund_flow_score: float   # 0-40
    technical_score: float   # 0-20
    movable_total: float     # 0-60 (前两个之和)
    total_score: float       # movable_total + 12 + 9 = max 81

    # forward returns
    fwd_5d_abs: float | None
    fwd_20d_abs: float | None
    fwd_40d_abs: float | None
    fwd_60d_abs: float | None

    # filled later
    fwd_5d_rel: float | None = None
    fwd_20d_rel: float | None = None
    fwd_40d_rel: float | None = None
    fwd_60d_rel: float | None = None


def compute_asof_full(daily: list[dict], shares: dict[str, float], idx: int) -> AsOfFull | None:
    """对 daily[idx] 计算所有 score + 前瞻收益."""
    if idx < 250:
        return None  # 250d 窗口必备

    today = daily[idx]
    last_close = today["close"]
    if last_close <= 0:
        return None

    closes = [d["close"] for d in daily[:idx + 1]]

    # nav return
    nav_5d = (last_close / daily[idx - 5]["close"] - 1) * 100 if idx >= 5 else 0.0
    nav_1m = (last_close / daily[idx - 20]["close"] - 1) * 100 if idx >= 20 else 0.0

    # 位置
    pct_60 = percentile_rank(closes[-60:], last_close)
    pct_120 = percentile_rank(closes[-120:], last_close)
    pct_250 = percentile_rank(closes[-250:], last_close)

    # share 流入: share Δ × close 累加 5d / 20d
    def share_flow_window(n: int) -> tuple[float, int]:
        total = 0.0
        days_used = 0
        for j in range(max(1, idx - n + 1), idx + 1):
            d_today = daily[j]["trade_date"]
            d_prev = daily[j - 1]["trade_date"]
            s_t = shares.get(d_today)
            s_p = shares.get(d_prev)
            if s_t is None or s_p is None:
                continue
            # 用当日 raw_close (与 fund_share 同期, 不用复权)
            delta = (s_t - s_p) * 10000 * daily[j]["raw_close"]
            total += delta
            days_used += 1
        return total, days_used

    flow_5d, days_5 = share_flow_window(5)
    flow_20d, days_20 = share_flow_window(20)
    if days_5 < 3 or days_20 < 10:
        return None  # share 数据不足

    # 量比 (今天的 vol / 过去 20 天均值)
    vols = [daily[j]["vol"] for j in range(idx - 20, idx)]
    mean_vol = sum(vols) / len(vols) if vols else 0
    vol_ratio = today["vol"] / mean_vol if mean_vol > 0 else 1.0

    # scores
    ff = fund_flow_score(flow_5d, flow_20d)
    ts = technical_score(nav_5d, pct_60, pct_250, vol_ratio)
    movable = ff + ts
    total = movable + 12 + 9  # fundamentals + news neutral

    # forward returns
    def fwd_n(n: int) -> float | None:
        if idx + n < len(daily) and daily[idx + n]["close"] > 0:
            return (daily[idx + n]["close"] / last_close - 1) * 100
        return None

    return AsOfFull(
        ts_code="", sub_domain="", name="",
        date=today["trade_date"], close=last_close,
        nav_5d=round(nav_5d, 2), nav_1m=round(nav_1m, 2),
        pct_60=pct_60, pct_120=pct_120, pct_250=pct_250,
        flow_5d_cny=round(flow_5d, 0), flow_20d_cny=round(flow_20d, 0),
        vol_ratio_20d=round(vol_ratio, 2),
        fund_flow_score=ff, technical_score=ts,
        movable_total=round(movable, 1), total_score=round(total, 1),
        fwd_5d_abs=round(fwd_n(5), 2) if fwd_n(5) is not None else None,
        fwd_20d_abs=round(fwd_n(20), 2) if fwd_n(20) is not None else None,
        fwd_40d_abs=round(fwd_n(40), 2) if fwd_n(40) is not None else None,
        fwd_60d_abs=round(fwd_n(60), 2) if fwd_n(60) is not None else None,
    )


# ─── 主流程 ────────────────────────────────────────────────────────────────

def run_full_backtest(start_date: str, end_date: str) -> dict[str, Any]:
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
        print(f"  {code} {e['name']}: daily {len(daily)} rows, share {len(shares)} dates",
              file=sys.stderr)

    # 走 walk
    print(f"\n🔄 walking {start_date} → {end_date} (idx >= 250) ...", file=sys.stderr)
    all_signals: list[dict] = []
    per_etf_count: dict[str, int] = {}

    for code, info in etf_data.items():
        daily = info["daily"]
        shares = info["shares"]
        if len(daily) < 250:
            print(f"  ⚠️  {code}: daily 不足 250 ({len(daily)}); 跳过", file=sys.stderr)
            continue

        cnt = 0
        for idx, d in enumerate(daily):
            td = d["trade_date"]
            if td < start_date or td > end_date:
                continue
            m = compute_asof_full(daily, shares, idx)
            if not m:
                continue
            m.ts_code = code
            m.sub_domain = info["sub_domain"]
            m.name = info["name"]
            all_signals.append(asdict(m))
            cnt += 1
        per_etf_count[code] = cnt
        print(f"  {code}: {cnt} 信号", file=sys.stderr)

    # benchmark per-day
    print(f"\n📊 计算每日 AI 板块均值 ...", file=sys.stderr)
    by_date: dict[str, dict[str, list[float]]] = {}
    for s in all_signals:
        d = s["date"]
        by_date.setdefault(d, {"5": [], "20": [], "40": [], "60": []})
        for k, fld in [("5", "fwd_5d_abs"), ("20", "fwd_20d_abs"),
                       ("40", "fwd_40d_abs"), ("60", "fwd_60d_abs")]:
            if s[fld] is not None:
                by_date[d][k].append(s[fld])
    bm: dict[str, dict[str, float | None]] = {}
    for d, val in by_date.items():
        bm[d] = {}
        for k in ("5", "20", "40", "60"):
            bm[d][k] = statistics.mean(val[k]) if val[k] else None

    # 挂 rel_return
    for s in all_signals:
        for k, abs_fld, rel_fld in [
            ("5", "fwd_5d_abs", "fwd_5d_rel"),
            ("20", "fwd_20d_abs", "fwd_20d_rel"),
            ("40", "fwd_40d_abs", "fwd_40d_rel"),
            ("60", "fwd_60d_abs", "fwd_60d_rel"),
        ]:
            base = bm.get(s["date"], {}).get(k)
            if s[abs_fld] is not None and base is not None:
                s[rel_fld] = round(s[abs_fld] - base, 2)
            else:
                s[rel_fld] = None

    print(f"\n📈 聚合分析 ...", file=sys.stderr)
    return {
        "meta": {
            "start_date": start_date, "end_date": end_date,
            "etf_count": len(AI_ETFS), "total_signals": len(all_signals),
        },
        "per_etf_count": per_etf_count,
        "all_signals": all_signals,
    }


# ─── 分析: 分桶 + 相关性 ──────────────────────────────────────────────────

def bucket_total(score: float) -> str:
    if score >= 60: return "HOT"        # 设计为 BUY
    if score >= 45: return "NEUTRAL"
    if score >= 30: return "COLD"
    return "AVOID"                       # 设计为 SELL


def bucket_fund_flow(score: float) -> str:
    if score >= 30: return "FF_HIGH"
    if score >= 15: return "FF_MID"
    return "FF_LOW"


def bucket_tech(score: float) -> str:
    if score >= 15: return "T_HIGH"
    if score >= 8:  return "T_MID"
    return "T_LOW"


def stat(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"n": 0}
    res = {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 2),
        "median": round(statistics.median(vals), 2),
        "stdev": round(statistics.stdev(vals), 2) if len(vals) >= 2 else 0,
        "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
    }
    if len(vals) >= 4:
        q = statistics.quantiles(vals, n=4)
        res["p25"] = round(q[0], 2)
        res["p75"] = round(q[2], 2)
    return res


def aggregate_by_bucket(signals: list[dict], bucket_fn, bucket_field: str,
                        windows=("5", "20", "40", "60")) -> dict[str, Any]:
    """对每个 bucket × 每个 fwd 窗口, 算 abs 和 rel 的统计."""
    buckets: dict[str, list[dict]] = {}
    for s in signals:
        b = bucket_fn(s[bucket_field])
        buckets.setdefault(b, []).append(s)

    out = {}
    for b, sub in buckets.items():
        out[b] = {"count": len(sub)}
        for w in windows:
            abs_vals = [s[f"fwd_{w}d_abs"] for s in sub if s[f"fwd_{w}d_abs"] is not None]
            rel_vals = [s[f"fwd_{w}d_rel"] for s in sub if s[f"fwd_{w}d_rel"] is not None]
            out[b][f"abs_{w}"] = stat(abs_vals)
            out[b][f"rel_{w}"] = stat(rel_vals)
    return out


def pearson_corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 30 or len(xs) != len(ys):
        return None
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return num / (den_x * den_y)


def correlation_table(signals: list[dict]) -> dict[str, Any]:
    """对 fund_flow_score / technical_score / total_score, 跑 fwd_20/40 abs+rel pearson."""
    out = {}
    for score_field in ("fund_flow_score", "technical_score", "movable_total", "total_score"):
        out[score_field] = {}
        for w in ("5", "20", "40", "60"):
            for kind in ("abs", "rel"):
                pairs = [(s[score_field], s[f"fwd_{w}d_{kind}"])
                         for s in signals
                         if s[f"fwd_{w}d_{kind}"] is not None]
                if not pairs:
                    out[score_field][f"{kind}_{w}"] = None
                    continue
                xs, ys = zip(*pairs)
                r = pearson_corr(list(xs), list(ys))
                out[score_field][f"{kind}_{w}"] = {
                    "n": len(pairs),
                    "pearson_r": round(r, 4) if r is not None else None,
                }
    return out


def by_year_breakdown(signals: list[dict]) -> dict[str, Any]:
    out = {}
    by_year: dict[str, list[dict]] = {}
    for s in signals:
        y = s["date"][:4]
        by_year.setdefault(y, []).append(s)
    for y, sub in by_year.items():
        out[y] = {
            "n": len(sub),
            "by_total_score": aggregate_by_bucket(sub, bucket_total, "total_score",
                                                  windows=("20", "40")),
        }
    return out


def by_subdomain_breakdown(signals: list[dict]) -> dict[str, Any]:
    out = {}
    by_sd: dict[str, list[dict]] = {}
    for s in signals:
        by_sd.setdefault(s["sub_domain"], []).append(s)
    for sd, sub in by_sd.items():
        out[sd] = {
            "n": len(sub),
            "by_total_score": aggregate_by_bucket(sub, bucket_total, "total_score",
                                                  windows=("20", "40")),
            "by_fund_flow": aggregate_by_bucket(sub, bucket_fund_flow, "fund_flow_score",
                                                windows=("20", "40")),
        }
    return out


# ─── 报告 ──────────────────────────────────────────────────────────────────

def fmt_pct(v) -> str:
    if v is None: return "  N/A"
    return f"{v:+6.2f}%"


def print_bucket_table(title: str, agg: dict, order: list[str], windows=("20", "40")):
    print(f"\n  ━━━ {title} ━━━")
    header = f"  {'bucket':<14}{'n':>6}"
    for w in windows:
        header += f"  abs T+{w} mean(win%)  rel T+{w} mean(win%)"
    print(header)
    for b in order:
        info = agg.get(b)
        if not info:
            continue
        line = f"  {b:<14}{info['count']:>6}"
        for w in windows:
            a = info.get(f"abs_{w}", {})
            r = info.get(f"rel_{w}", {})
            line += f"   {fmt_pct(a.get('mean'))}({a.get('win_rate','N/A'):>5}%)  {fmt_pct(r.get('mean'))}({r.get('win_rate','N/A'):>5}%)"
        print(line)


def print_report(result: dict[str, Any]):
    sigs = result["all_signals"]
    m = result["meta"]
    print(f"\n{'='*100}")
    print(f"  全样本 sector_score 评估  ({m['start_date']} → {m['end_date']})")
    print(f"{'='*100}")
    print(f"  ETF 数: {m['etf_count']}, 信号样本数: {m['total_signals']}")

    print(f"\n  per-ETF 触发样本数:")
    for code, n in result["per_etf_count"].items():
        print(f"    {code:<12} {n} 天")

    # 总分桶分布
    score_dist = {b: 0 for b in ("HOT", "NEUTRAL", "COLD", "AVOID")}
    for s in sigs:
        score_dist[bucket_total(s["total_score"])] += 1
    total = sum(score_dist.values())
    print(f"\n  ━━━ total_score 分桶分布 (n={total}) ━━━")
    for b, n in score_dist.items():
        pct = n / total * 100 if total > 0 else 0
        print(f"    {b:<10} {n:>5} ({pct:>5.1f}%)")

    # 按 total_score 分桶 (主要关注)
    agg_total = aggregate_by_bucket(sigs, bucket_total, "total_score")
    print_bucket_table("按 total_score 分桶 — 全周期 (T+5/20/40/60)",
                       agg_total, ["HOT", "NEUTRAL", "COLD", "AVOID"],
                       windows=("5", "20", "40", "60"))

    # 拆 fund_flow_score
    agg_ff = aggregate_by_bucket(sigs, bucket_fund_flow, "fund_flow_score")
    print_bucket_table("按 fund_flow_score 单独分桶 (40 max)",
                       agg_ff, ["FF_HIGH", "FF_MID", "FF_LOW"],
                       windows=("5", "20", "40", "60"))

    # 拆 technical_score
    agg_t = aggregate_by_bucket(sigs, bucket_tech, "technical_score")
    print_bucket_table("按 technical_score 单独分桶 (20 max)",
                       agg_t, ["T_HIGH", "T_MID", "T_LOW"],
                       windows=("5", "20", "40", "60"))

    # 相关性表
    print(f"\n  ━━━ Pearson 相关系数 (score vs forward return) ━━━")
    print(f"  正值 = 评分高→后续涨 (设计预期); 负值 = 反向; |r|<0.05 ≈ 噪音")
    corr = correlation_table(sigs)
    print(f"  {'score':<22}{'abs_5':>10}{'abs_20':>10}{'abs_40':>10}{'abs_60':>10}{'rel_5':>10}{'rel_20':>10}{'rel_40':>10}{'rel_60':>10}")
    for sf in ("fund_flow_score", "technical_score", "movable_total", "total_score"):
        line = f"  {sf:<22}"
        for w in ("5", "20", "40", "60"):
            d = corr[sf].get(f"abs_{w}")
            line += f"{(d['pearson_r'] if d else 0):>10.4f}"
        for w in ("5", "20", "40", "60"):
            d = corr[sf].get(f"rel_{w}")
            line += f"{(d['pearson_r'] if d else 0):>10.4f}"
        print(line)

    # 按年份
    print(f"\n  ━━━ 按年份切片 (regime 检验) ━━━")
    yb = by_year_breakdown(sigs)
    for y in sorted(yb.keys()):
        print(f"\n  · {y} (n={yb[y]['n']}):")
        for b in ("HOT", "NEUTRAL", "COLD", "AVOID"):
            info = yb[y]["by_total_score"].get(b)
            if not info or info["count"] == 0:
                continue
            r20 = info.get("rel_20", {})
            r40 = info.get("rel_40", {})
            print(f"    {b:<10} n={info['count']:>4}  rel T+20: mean={fmt_pct(r20.get('mean'))} win={r20.get('win_rate','N/A')}%   rel T+40: mean={fmt_pct(r40.get('mean'))} win={r40.get('win_rate','N/A')}%")

    # 按 sub_domain
    print(f"\n  ━━━ 按 sub_domain 切片 ━━━")
    sb = by_subdomain_breakdown(sigs)
    for sd in sorted(sb.keys()):
        print(f"\n  · {sd} (n={sb[sd]['n']}):")
        for b in ("HOT", "NEUTRAL", "COLD", "AVOID"):
            info = sb[sd]["by_total_score"].get(b)
            if not info or info["count"] == 0:
                continue
            r20 = info.get("rel_20", {})
            r40 = info.get("rel_40", {})
            print(f"    {b:<10} n={info['count']:>4}  rel T+20: {fmt_pct(r20.get('mean'))} win={r20.get('win_rate','N/A')}%   T+40: {fmt_pct(r40.get('mean'))} win={r40.get('win_rate','N/A')}%")

    # 判定
    print(f"\n{'='*100}\n  判定\n{'='*100}")
    hot_r20 = agg_total.get("HOT", {}).get("rel_20", {})
    avoid_r20 = agg_total.get("AVOID", {}).get("rel_20", {})
    hot_mean = hot_r20.get("mean", 0) if hot_r20.get("mean") is not None else 0
    avoid_mean = avoid_r20.get("mean", 0) if avoid_r20.get("mean") is not None else 0
    spread = hot_mean - avoid_mean

    print(f"  HOT - AVOID rel T+20 spread: {spread:+.2f}%")
    print(f"     HOT  mean rel T+20 = {fmt_pct(hot_mean)} (n={hot_r20.get('n',0)})")
    print(f"     AVOID mean rel T+20 = {fmt_pct(avoid_mean)} (n={avoid_r20.get('n',0)})")
    if spread > 1.0:
        print(f"  ✅ 评分体系正向: HOT 桶 T+20 跑赢 AVOID 桶 {spread:.1f}%")
    elif spread < -1.0:
        print(f"  ❌ 评分体系反向: HOT 桶 T+20 跑输 AVOID 桶 {abs(spread):.1f}% — sector_score 需要重做")
    else:
        print(f"  ⚠️  评分体系基本失效 (|spread|<1%): HOT 和 AVOID 后续表现没有显著区别")

    # 单维度判定
    ff_corr_r20 = corr["fund_flow_score"].get("rel_20")
    t_corr_r20 = corr["technical_score"].get("rel_20")
    if ff_corr_r20 and ff_corr_r20["pearson_r"] is not None:
        r = ff_corr_r20["pearson_r"]
        print(f"  fund_flow_score → rel T+20 pearson r = {r:+.4f}")
        if r < -0.05:
            print(f"     ❌ 资金面维度反向 — share-based fund_flow 需要反转或归零")
        elif r > 0.05:
            print(f"     ✅ 资金面维度正向")
        else:
            print(f"     ⚠️  资金面维度无信号 (|r|<0.05)")
    if t_corr_r20 and t_corr_r20["pearson_r"] is not None:
        r = t_corr_r20["pearson_r"]
        print(f"  technical_score → rel T+20 pearson r = {r:+.4f}")
        if r > 0.05:
            print(f"     ✅ 技术面维度正向 (momentum)")
        elif r < -0.05:
            print(f"     ❌ 技术面维度反向 (mean reversion 在 AI 趋势市失效)")
        else:
            print(f"     ⚠️  技术面维度无信号 (|r|<0.05)")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20240101")
    ap.add_argument("--end", default="20260331")  # 留 60d 前瞻
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--full-json", action="store_true")
    args = ap.parse_args()

    result = run_full_backtest(args.start, args.end)

    if args.full_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.json:
        slim = {k: v for k, v in result.items() if k != "all_signals"}
        slim["aggregations"] = {
            "by_total_score": aggregate_by_bucket(result["all_signals"], bucket_total, "total_score"),
            "by_fund_flow_score": aggregate_by_bucket(result["all_signals"], bucket_fund_flow, "fund_flow_score"),
            "by_technical_score": aggregate_by_bucket(result["all_signals"], bucket_tech, "technical_score"),
            "correlations": correlation_table(result["all_signals"]),
            "by_year": by_year_breakdown(result["all_signals"]),
            "by_subdomain": by_subdomain_breakdown(result["all_signals"]),
        }
        print(json.dumps(slim, ensure_ascii=False, indent=2))
        return

    print_report(result)


if __name__ == "__main__":
    main()
