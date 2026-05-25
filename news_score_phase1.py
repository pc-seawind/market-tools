"""news_score_phase1.py — sector_score news_score 子模块 Phase 1 (FINAL 终态).

🔒 FINAL 2026-05-25: 确认 phase1_v1 (纯 z_repur) 为 news_score 终态.
   Phase 2 候选 A (z_unlock) / B (z_block) 均未通过 fwd_20d 上线门槛, 已 REJECTED.
   z_block fwd_5d 信号有效, 已搬到 sector_picks 短线层 (sector_picks_block_trade.py).
   详见: docs/news_score_phase2_design.md (用户决策章节).

⚠️ 已上线版本: phase1_v1 (纯 z_repur, base + clip*3). 北向 top10 / 龙虎榜机构
   被回测证明为反指/无信号, 已弃用 (代码留作参考).

回测验证 (backtest_news_phase1.py, 2026-01 ~ 2026-05, 6 个月 panel):
  | 信号 | fwd_5d IC | fwd_20d IC | 上线? |
  |---|---|---|---|
  | z_north | +0.005 | -0.104 | ❌ 反指 |
  | z_inst  | -0.028 | -0.070 | ❌ 弱反指 |
  | z_repur | +0.220 | **+0.334** | ✅ |

  增量 IC (fund_score 单 vs fund+news_v1):
    fwd_5d:  +0.191 → +0.226  Δ=+18.4%
    fwd_20d: +0.310 → +0.386  Δ=+24.6%  ⭐ 远超 +5% 门槛

  月度 IC (z_repur):
    202601 +0.213, 202602 +0.326, 202603 +0.360, 202604 **-0.291** (普涨被 beta 淹没)
    → 普涨行情下信号失效, 限制 swing ±3 防止单月反转损害

打分公式 (phase1_v1):
  base 7.5 + clip(z_repur, -2, +2) * 1.5  → 范围 [4.5, 10.5] (满分 15)
  设计: ±1.5 swing (而非 ±3 或 ±6), 让信号 contribute 但不主导, 单月反转时损害有限

上线门槛: 全过 ✓ (单变量 IC_20d=+0.334 > 0.10; 增量 IC=+24.6% > +5%; n=448 > 2 月)
"""
from __future__ import annotations

import csv
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"
_CACHE_DIR = _HERE / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)


# ─── Tushare fetch helpers ──────────────────────────────────────────────────

def _ts_csv(api: str, **params) -> list[dict[str, str]]:
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


def _yyyymmdd(d: datetime) -> str: return d.strftime("%Y%m%d")


def fetch_hsgt_top10_history(start_date: str, end_date: str,
                             cache_key: str = "hsgt_top10") -> list[dict]:
    """全市场北向 top10 持仓 (沪+深 各 10), 按 trade_date 拉.

    cache 到 {_CACHE_DIR}/news_{cache_key}.jsonl. 已存在则增量补.
    """
    cache_file = _CACHE_DIR / f"news_{cache_key}.jsonl"
    cached_dates = set()
    if cache_file.exists():
        with open(cache_file) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    cached_dates.add(r["trade_date"])
                except: pass

    cur = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    new_rows = []
    while cur <= end:
        d = _yyyymmdd(cur)
        cur += timedelta(days=1)
        if cur.weekday() in (5, 6) or d in cached_dates:
            cur += timedelta(days=0)
            continue
        # 沪 (market_type=1) + 深 (market_type=3)
        for mkt in ("1", "3"):
            rows = _ts_csv("hsgt_top10", trade_date=d, market_type=mkt)
            for r in rows:
                r["market_type"] = mkt
                r["trade_date"] = d
                new_rows.append(r)

    if new_rows:
        with open(cache_file, "a") as f:
            for r in new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  💾 hsgt_top10: cached +{len(new_rows)} rows", file=sys.stderr)

    # 全部读
    out = []
    if cache_file.exists():
        with open(cache_file) as f:
            for line in f:
                try: out.append(json.loads(line))
                except: pass
    return [r for r in out if start_date <= r["trade_date"] <= end_date]


def fetch_top_inst_history(start_date: str, end_date: str,
                           cache_key: str = "top_inst") -> list[dict]:
    """龙虎榜机构席位每日明细."""
    cache_file = _CACHE_DIR / f"news_{cache_key}.jsonl"
    cached_dates = set()
    if cache_file.exists():
        with open(cache_file) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    cached_dates.add(r["trade_date"])
                except: pass

    cur = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    new_rows = []
    while cur <= end:
        d = _yyyymmdd(cur)
        if cur.weekday() in (5, 6) or d in cached_dates:
            cur += timedelta(days=1)
            continue
        rows = _ts_csv("top_inst", trade_date=d)
        for r in rows: r["trade_date"] = d
        new_rows.extend(rows)
        cur += timedelta(days=1)

    if new_rows:
        with open(cache_file, "a") as f:
            for r in new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  💾 top_inst: cached +{len(new_rows)} rows", file=sys.stderr)

    out = []
    if cache_file.exists():
        with open(cache_file) as f:
            for line in f:
                try: out.append(json.loads(line))
                except: pass
    return [r for r in out if start_date <= r["trade_date"] <= end_date]


def fetch_repurchase_history(start_date: str, end_date: str,
                             cache_key: str = "repurchase") -> list[dict]:
    """回购公告 by ann_date.

    repurchase 表的 ann_date 是公告日, 字段 amount 是回购金额(元).
    """
    cache_file = _CACHE_DIR / f"news_{cache_key}.jsonl"
    cached_dates = set()
    if cache_file.exists():
        with open(cache_file) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    cached_dates.add(r.get("ann_date"))
                except: pass

    cur = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    new_rows = []
    while cur <= end:
        d = _yyyymmdd(cur)
        if cur.weekday() in (5, 6) or d in cached_dates:
            cur += timedelta(days=1)
            continue
        rows = _ts_csv("repurchase", ann_date=d)
        new_rows.extend(rows)
        cur += timedelta(days=1)

    if new_rows:
        with open(cache_file, "a") as f:
            for r in new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  💾 repurchase: cached +{len(new_rows)} rows", file=sys.stderr)

    out = []
    if cache_file.exists():
        with open(cache_file) as f:
            for line in f:
                try: out.append(json.loads(line))
                except: pass
    return [r for r in out if r.get("ann_date") and start_date <= r["ann_date"] <= end_date]


# ─── 信号计算 ──────────────────────────────────────────────────────────────

def _to_f(x: Any) -> float:
    try: return float(x)
    except: return 0.0


def aggregate_concept_signals(
    members: list[tuple[str, str]],
    hsgt_rows: list[dict], top_inst_rows: list[dict], repurchase_rows: list[dict],
    as_of: str, win_short: int = 5, win_long: int = 30, hist_days: int = 60,
) -> dict[str, float | None]:
    """对一个 concept (members 是 [(ts_code, name), ...]),
    在 as_of 这天向后回看 win_short / win_long 算各信号汇总, 再算 hist_days z-score.

    返回:
      north_z   -- 北向最近 5d 净买入累计的历史 z (正向 = 北向加仓)
      inst_z    -- 龙虎榜机构最近 5d 净买入次数累计的历史 z
      repur_z   -- 最近 30d 回购金额累计的历史 z
    """
    member_codes = {ts for ts, _ in members}

    # 把所有 events 索引到 (date, ts_code)
    # hsgt: 日个股 amount = 当日成交额 (元), 没有 net_inflow, 用 amount 作为 proxy
    # 其实 hsgt_top10 没有"净买入额", 只有 close+change+rank+amount.
    # 真正的净买入: 用全市场 north_money (moneyflow_hsgt 表), 但那是市场层
    # → 这里用 amount (成交额) 作为"被北向关注度" proxy (正交关注度更合理)

    # top_inst: 一行 = 一个机构席位的买/卖, 关注 net_buy
    # 我们汇总到 (date, ts_code) → 这只票当天所有机构净买入合计
    inst_by_date_ts = defaultdict(float)
    for r in top_inst_rows:
        if r.get("ts_code") not in member_codes: continue
        inst_by_date_ts[(r["trade_date"], r["ts_code"])] += _to_f(r.get("net_buy"))

    # hsgt_top10: ts_code 出现 = 当天进 top10, 可作"流入信号"
    # 但没有方向, 用 change(涨跌幅) 间接推 (涨买跌卖) — too noisy
    # → 简化: 当天 ts_code 进 top10 = 计 1 (count signal)
    north_by_date_ts = defaultdict(int)
    for r in hsgt_rows:
        if r.get("ts_code") in member_codes:
            north_by_date_ts[(r["trade_date"], r["ts_code"])] = 1

    # repurchase: ann_date = 公告日, amount = 金额(元)
    # 用"公告频度 + 金额" 双指标. concept 成员在最近 30d 公告的总金额
    repur_by_date_ts = defaultdict(float)
    for r in repurchase_rows:
        if r.get("ts_code") not in member_codes: continue
        d = r.get("ann_date")
        if not d: continue
        amt = _to_f(r.get("amount"))
        repur_by_date_ts[(d, r["ts_code"])] += amt

    # 算 as_of 当前窗口 + 历史 z
    def _series_for(window_days: int, ev_dict: dict, all_dates: list[str]) -> list[float]:
        """对 all_dates 中每个 d, 算往前 window_days 内 concept 成员的累计值."""
        # 先按 date 聚合到 concept 总值
        by_date_total = defaultdict(float)
        for (d, ts), v in ev_dict.items():
            by_date_total[d] += v
        # 滚动求和
        sorted_dates = sorted(by_date_total)
        cum = []
        for d in all_dates:
            d_dt = datetime.strptime(d, "%Y%m%d")
            start_dt = d_dt - timedelta(days=window_days)
            total = sum(v for k, v in by_date_total.items()
                        if start_dt <= datetime.strptime(k, "%Y%m%d") <= d_dt)
            cum.append(total)
        return cum

    # 用所有相关 dates 作 z 计算的总集
    all_dates_set = set()
    for d, _ in inst_by_date_ts: all_dates_set.add(d)
    for d, _ in north_by_date_ts: all_dates_set.add(d)
    for d, _ in repur_by_date_ts: all_dates_set.add(d)
    if not all_dates_set: return {"north_z": None, "inst_z": None, "repur_z": None}

    # 取 [as_of - hist_days, as_of] 之间的 dates
    as_dt = datetime.strptime(as_of, "%Y%m%d")
    hist_start_dt = as_dt - timedelta(days=hist_days + win_long)
    hist_dates = sorted(d for d in all_dates_set
                        if hist_start_dt <= datetime.strptime(d, "%Y%m%d") <= as_dt)

    if len(hist_dates) < 10:
        return {"north_z": None, "inst_z": None, "repur_z": None}

    # 算每个 hist_date 上的 win_short / win_long 累计值
    inst_series = _series_for(win_short, inst_by_date_ts, hist_dates)
    north_series = _series_for(win_short, north_by_date_ts, hist_dates)
    repur_series = _series_for(win_long, repur_by_date_ts, hist_dates)

    def _z_of_last(series: list[float]) -> float | None:
        if len(series) < 10: return None
        cur = series[-1]
        hist = series[:-1]  # 历史 (排除当前)
        if not hist: return None
        mu = statistics.mean(hist)
        sd = statistics.stdev(hist) if len(hist) > 1 else 0
        if sd == 0: return 0.0
        return (cur - mu) / sd

    return {
        "north_z": _z_of_last(north_series),
        "inst_z": _z_of_last(inst_series),
        "repur_z": _z_of_last(repur_series),
        "n_hist_dates": len(hist_dates),
    }


# ─── 主入口 ────────────────────────────────────────────────────────────────

def news_score_phase1(concept: str, as_of: str | None = None,
                      preloaded: dict | None = None) -> tuple[float, str, dict]:
    """Compute Phase 1 news score for a concept (phase1_v1: 纯 z_repur).

    Returns (score: float [0, 15], note: str, diag: dict)

    回测验证 (n=448 obs, 6 mo): 单变量 IC_20d=+0.334, 增量 IC +24.6%.
    z_north / z_inst 已在回测里证为反指/无信号, 不参与打分 (诊断用途仍计算).
    """
    from concepts_data import stocks_of

    as_of = as_of or datetime.now().strftime("%Y%m%d")
    members = stocks_of(concept) or []
    if not members:
        return 7.5, "(no members; stub-7.5)", {"error": "no members", "concept": concept}

    if preloaded is None:
        # 拉 90d 历史 (含 30d win_long + 60d hist_days)
        hist_start_dt = datetime.strptime(as_of, "%Y%m%d") - timedelta(days=90)
        hist_start = hist_start_dt.strftime("%Y%m%d")
        preloaded = {
            "hsgt": fetch_hsgt_top10_history(hist_start, as_of),
            "top_inst": fetch_top_inst_history(hist_start, as_of),
            "repurchase": fetch_repurchase_history(hist_start, as_of),
        }

    sigs = aggregate_concept_signals(
        members, preloaded["hsgt"], preloaded["top_inst"], preloaded["repurchase"], as_of)

    base = 7.5  # stub mid
    z_north = sigs.get("north_z")  # 诊断保留
    z_inst  = sigs.get("inst_z")
    z_repur = sigs.get("repur_z")

    # phase1_v1: 只用 z_repur, ±1.5 swing
    if z_repur is None:
        score = base
        note = f"无回购公告/历史 (n_members={len(members)}); stub-{base}"
    else:
        z_repur_c = max(-2.0, min(2.0, z_repur))
        score = base + 1.5 * z_repur_c
        score = max(0.0, min(15.0, round(score, 2)))
        zr_str = f"{z_repur:+.2f}σ"
        note = f"base 7.5 + repur_z {zr_str} ({1.5 * z_repur_c:+.2f}) = {score}/15"

    return score, note, {
        "concept": concept, "as_of": as_of,
        "n_members": len(members),
        "z_north": z_north, "z_inst": z_inst, "z_repur": z_repur,
        "score": score, "version": "phase1_v1",
    }


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--concept", required=False)
    p.add_argument("--as-of", default=None)
    p.add_argument("--prefetch-days", type=int, default=180)
    p.add_argument("--prefetch-only", action="store_true",
                   help="只拉历史 cache, 不算分")
    args = p.parse_args()

    as_of = args.as_of or datetime.now().strftime("%Y%m%d")
    start_dt = datetime.strptime(as_of, "%Y%m%d") - timedelta(days=args.prefetch_days)
    start = start_dt.strftime("%Y%m%d")
    print(f"📥 prefetch {start} ~ {as_of}", file=sys.stderr)
    hsgt = fetch_hsgt_top10_history(start, as_of)
    inst = fetch_top_inst_history(start, as_of)
    repur = fetch_repurchase_history(start, as_of)
    print(f"  hsgt n={len(hsgt)}  top_inst n={len(inst)}  repur n={len(repur)}",
          file=sys.stderr)

    if args.prefetch_only:
        return

    if args.concept:
        score, note, diag = news_score_phase1(args.concept, as_of,
                                              preloaded={"hsgt": hsgt, "top_inst": inst,
                                                         "repurchase": repur})
        print(f"score: {score}/15")
        print(f"note: {note}")
        print(json.dumps(diag, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
