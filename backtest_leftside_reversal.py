#!/usr/bin/env python3
"""backtest_leftside_reversal.py — 左向触底 vs 右侧追高 的前瞻收益对照回测.

动机 (2026-09-10 用户质疑):
  现框架的 BUY 通道 (Tier 0 趋势龙头 + Tier 2-4 估值) 实际上都偏向"右侧":
    - Tier 0 硬要求 pos120 >= 75 + 1M >= +8% + vol_5d >= 0.95 + volume_signal != DRY_UP
    - Tier 2 要求 ROE > 板块中位 + 净利 YoY > +10%  (周期底部盈利最差 → 系统性错过)
    - Tier 1 的 Gate (c)/(c') 虽然放行了"flow 拐点 / 可能筑底"的板块,
      但总分排序 + "必须 HOT 才做主推" 的纪律把它挡在门外
  结果: 真正的左侧触底标的 (20d 资金负、板块 COLD、个股 pos120 低、量能干涸)
        永远进不了推荐池.

本脚本回答一个可证伪的问题:
  "pos120 低 + 1M 走平/下跌 + 量能企稳" 这个组合, 其前瞻收益是否
  >= 现框架的右侧组合? 横截面超额 (同日全样本去均值) 口径下比较.

方法:
  1. 股票池 = concepts_data.CONCEPTS 的 A 股成分股并集 (约 214 只).
  2. 每只股票: tushare daily + adj_factor → 前复权 (同 sector_picks.compute_stock 口径).
  3. 评估日 = 2025-01-01 ~ 2026-08-31 之间每 5 个交易日采样一次 (降自相关).
  4. as-of 特征: pos60/120/250, pct_1d/5d/1m, vol_ratio_1d/5d, volume_signal.
  5. 前瞻: fwd_5d / fwd_20d / fwd_40d, 以及**同日全样本去均值的超额**.
  6. 分桶: 左侧触底组合 vs 右侧追高组合 vs 全样本基准.
  7. 板块维度: 以 concept 内成分股 pct_20d 中位数作板块动量代理,
     分 "板块弱势 (< -5%)" / "板块强势 (> +5%)", 看左向信号在不同板块状态下的表现.

输出:
  /tmp/reversal_backtest_<YYYYmmdd-HHMM>.json  (完整分桶统计 + 样本元数据)
  终端: 人类可读 summary

注意 (局限, 报告里必须写明):
  - 股票池是**当前** concept 成分股 → 存在幸存者/选择偏差 (历史被剔除的成分股不在样本里).
  - 未叠加估值乖离 (跨历史 fair_value 需历史 PE 中枢 + EPS, 成本高);
    本脚本只检验**技术面/位置/量能**维度, 估值叠加留给 Phase 2.
  - 板块状态用成分股中位数动量作代理, 不等于 sector_score (后者需重建 ETF/BK 历史).

Usage:
  python3 backtest_leftside_reversal.py [--out /tmp/xxx.json] [--step 5]
                                        [--start 20250101] [--end 20260831]
                                        [--limit N]
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_TUSHARE = _ROOT / "tushare.py"


# ─── tushare 访问 (复用 sector_picks 的 subprocess 模式, 带 tushare.py 自身缓存) ───

def _ts(api: str, **params) -> list[dict[str, str]]:
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


# ─── 特征计算 (与 sector_picks 保持同一口径) ───

def _pctile(vals: list[float], x: float) -> int:
    if not vals:
        return 50
    n = sum(1 for v in vals if v <= x)
    return int(round(n / len(vals) * 100))


def _vol_signal(v1: float, v5: float) -> str:
    if v1 < 0.7 and v5 < 0.9:
        return "DRY_UP"
    if v1 > 1.8 and v5 > 1.2:
        return "BLOWOFF"
    if v1 > 1.3 or v5 > 1.15:
        return "EXPANDING"
    return "NORMAL"


def _features(closes: list[float], vols: list[float]) -> dict | None:
    """as-of 特征, closes/vols 已截断到评估日 (含当日)."""
    n = len(closes)
    if n < 130:
        return None
    c = closes[-1]
    out: dict[str, float | str] = {"close": c}

    def ret(k: int) -> float:
        if n < k + 1 or closes[-k - 1] <= 0:
            return 0.0
        return (c / closes[-k - 1] - 1) * 100

    out["pct_1d"] = ret(1)
    out["pct_5d"] = ret(5)
    out["pct_1m"] = ret(21)

    w60, w120, w250 = closes[-60:], closes[-120:], closes[-250:]
    out["pos60"] = _pctile(w60, c)
    out["pos120"] = _pctile(w120, c)
    out["pos250"] = _pctile(w250, c)

    hi120, lo120 = max(w120), min(w120)
    out["dd_from_hi120"] = (c / hi120 - 1) * 100 if hi120 > 0 else 0.0

    v_today = vols[-1] if vols else 0.0
    v5_excl = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else 0.0
    v1r = v_today / v5_excl if v5_excl > 0 else 0.0
    v5 = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0.0
    v20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0.0
    v5r = v5 / v20 if v20 > 0 else 0.0
    out["vol_1d"] = v1r
    out["vol_5d"] = v5r
    out["vol_signal"] = _vol_signal(v1r, v5r)

    # 距 120 日低点 (左侧深度的另一个视角)
    out["up_from_lo120"] = (c / lo120 - 1) * 100 if lo120 > 0 else 0.0
    # 20 日均线位置
    ma20 = sum(closes[-20:]) / 20
    out["above_ma20"] = 1 if c >= ma20 else 0
    ma60 = sum(closes[-60:]) / 60
    out["above_ma60"] = 1 if c >= ma60 else 0
    return out


# ─── 信号定义 ────────────────────────────────────────────────────────────

def classify(f: dict) -> list[str]:
    """返回该样本命中的信号标签 (可多命中)."""
    tags: list[str] = []
    p, p5, p1m = f["pos120"], f["pct_5d"], f["pct_1m"]
    v1, v5, sig = f["vol_1d"], f["vol_5d"], f["vol_signal"]
    d1 = f["pct_1d"]

    # ── 基准 ──
    tags.append("BASELINE_ALL")

    # ── 左侧触底族 ──
    low = p <= 30
    if low:
        tags.append("L_low30")
    if low and p1m <= 0:
        tags.append("L_low30_calm")
    if low and p1m <= 0:
        # 缩量止跌: 今日缩量但收阳
        if v1 <= 0.9 and d1 > 0:
            tags.append("L_quiet_stab")
        # 地量后放量收阳: 5d 量能萎缩 + 今日放量 + 收阳
        if v1 >= 1.2 and v5 <= 1.0 and d1 > 0:
            tags.append("L_dry_then_pop")
        # 站回 MA20
        if f["above_ma20"] and p1m <= -3:
            tags.append("L_reclaim_ma20")
        # DRY_UP 状态下的低波动 (干涸 + 1M 轻微负)
        if sig == "DRY_UP" and -10 <= p1m <= 0:
            tags.append("L_dryup_flat")
    if p <= 15 and p1m <= -5:
        tags.append("L_deep15")
    if p <= 30 and f["dd_from_hi120"] <= -40:
        tags.append("L_deep_dd40")

    # 组合 (任一种企稳形态)
    if low and p1m <= 0 and ((v1 <= 0.9 and d1 > 0) or (v1 >= 1.2 and v5 <= 1.0 and d1 > 0)):
        tags.append("L_STAB_ANY")

    # 强企稳: 缩量止跌 + 站上 MA20 且仍在低位
    if low and p1m <= 0 and f["above_ma20"] and (d1 > 0 or v1 >= 1.2):
        tags.append("L_STAB_STRONG")

    # 地量后放量收阳 + 站回 MA20 (左侧最严组合)
    if low and p1m <= 0 and v1 >= 1.2 and v5 <= 1.0 and d1 > 0 and f["above_ma20"]:
        tags.append("L_pop_reclaim20")

    # 左侧 + 中期仍在跌 (1M <= -10) 的极端深跌 (接飞刀对照)
    if low and p1m <= -10:
        tags.append("L_low_deep1m")

    # ── 右侧追高族 (现框架口径) ──
    if p >= 75 and p1m >= 8 and v5 >= 0.95 and sig != "DRY_UP":
        tags.append("R_tier0_chase")
    if p >= 85 and v1 >= 1.5 and 0 < p5 <= 10:
        tags.append("R_buy_breakout")
    if p >= 75 and p1m >= 20:
        tags.append("R_momentum20")
    # Tier 3 估值通道常见形态: 位置无关, 只要回调 (无位置门槛)
    if p5 <= -3 and p1m <= -5:
        tags.append("R_pullback_any")

    return tags


# ─── 主流程 ──────────────────────────────────────────────────────────────

def load_universe(limit: int | None) -> dict[str, list[str]]:
    """concept → [ts_code]; 同时返回 code → concepts 反向索引. A 股 only."""
    sys.path.insert(0, str(_ROOT))
    from concepts_data import CONCEPTS  # noqa: E402

    mapping: dict[str, list[str]] = {}
    for concept, members in CONCEPTS.items():
        codes = []
        for m in members:
            code = m[0] if isinstance(m, (list, tuple)) else m
            if str(code).upper().endswith((".SH", ".SZ")):
                codes.append(code)
        if codes:
            mapping[concept] = codes
    if limit:
        # 截断: 每个 concept 取前 N 只, 用于快速冒烟
        mapping = {k: v[:limit] for k, v in mapping.items()}
    return mapping


def fetch_stock(code: str, start: str) -> tuple[list[str], list[float], list[float]] | None:
    daily = _ts("daily", ts_code=code, start_date=start,
                fields="trade_date,close,vol")
    if not daily:
        return None
    rows = []
    for r in daily:
        try:
            d = r["trade_date"]
            c = float(r["close"])
            v = float(r.get("vol") or 0)
        except (ValueError, KeyError, TypeError):
            continue
        if c > 0:
            rows.append((d, c, v))
    if len(rows) < 200:
        return None
    rows.sort(key=lambda x: x[0])

    adj = _ts("adj_factor", ts_code=code, start_date=start,
              fields="trade_date,adj_factor")
    if adj:
        amap = {}
        for r in adj:
            try:
                a = float(r["adj_factor"])
                if a > 0:
                    amap[r["trade_date"]] = a
            except (ValueError, KeyError, TypeError):
                continue
        latest = amap.get(rows[-1][0])
        if latest and latest > 0 and len({round(v, 4) for v in amap.values()}) > 1:
            rows = [(d, c * (amap.get(d, latest) / latest), v) for d, c, v in rows]

    dates = [d for d, _, _ in rows]
    closes = [c for _, c, _ in rows]
    vols = [v for _, _, v in rows]
    return dates, closes, vols


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--step", type=int, default=5, help="每 N 个交易日采样一次")
    ap.add_argument("--start", default="20240601", help="行情起始 (需留 250 日预热)")
    ap.add_argument("--eval-start", default="20250101")
    ap.add_argument("--end", default="20260831")
    ap.add_argument("--limit", type=int, default=0, help="每个 concept 取前 N 只 (冒烟用)")
    ap.add_argument("--fwd", default="5,20,40")
    args = ap.parse_args()

    fwds = [int(x) for x in args.fwd.split(",")]
    t0 = time.time()

    mapping = load_universe(args.limit or None)
    code2concepts: dict[str, list[str]] = defaultdict(list)
    for concept, codes in mapping.items():
        for c in codes:
            code2concepts[c].append(concept)
    universe = sorted(code2concepts)
    print(f"[universe] {len(universe)} A-share stocks across {len(mapping)} concepts",
          flush=True)

    # 交易日历 (用 000001.SZ 的日线日期)
    cal_rows = _ts("daily", ts_code="000001.SZ", start_date=args.start,
                   fields="trade_date")
    calendar = sorted(r["trade_date"] for r in cal_rows) if cal_rows else []
    if not calendar:
        print("ERROR: 无法获取交易日历 (000001.SZ)", file=sys.stderr)
        return 2
    print(f"[calendar] {len(calendar)} trading days {calendar[0]}..{calendar[-1]}",
          flush=True)

    eval_dates = [d for d in calendar if args.eval_start <= d <= args.end]
    eval_dates = eval_dates[::args.step]
    max_fwd = max(fwds)
    # 需要评估日之后还有 max_fwd 个交易日才能算前瞻 → 排除尾部
    idx = {d: i for i, d in enumerate(calendar)}
    eval_dates = [d for d in eval_dates if idx[d] + max_fwd < len(calendar)]
    print(f"[eval] {len(eval_dates)} evaluation dates (step={args.step})",
          flush=True)

    # 采样 (stock, date) → 特征 + 前瞻
    samples: list[dict] = []
    failed: list[str] = []
    for i, code in enumerate(universe, 1):
        got = fetch_stock(code, args.start)
        if not got:
            failed.append(code)
            continue
        dates, closes, vols = got
        didx = {d: k for k, d in enumerate(dates)}
        for d in eval_dates:
            k = didx.get(d)
            if k is None or k < 130:
                continue
            f = _features(closes[:k + 1], vols[:k + 1])
            if not f:
                continue
            fwd = {}
            for h in fwds:
                gi = idx[d] + h
                if gi >= len(calendar):
                    fwd[h] = None
                    continue
                gd = calendar[gi]
                j = didx.get(gd)
                if j is None:
                    fwd[h] = None
                else:
                    fwd[h] = (closes[j] / closes[k] - 1) * 100
            samples.append({
                "code": code, "date": d, "f": f, "fwd": fwd,
                "concepts": code2concepts[code],
            })
        if i % 25 == 0:
            print(f"  [{i}/{len(universe)}] {len(samples)} samples "
                  f"({time.time()-t0:.0f}s)", flush=True)

    print(f"[fetch] {len(samples)} samples, {len(failed)} stocks skipped",
          flush=True)

    # ── 同日横截面去均值 (超额收益) ──
    by_date: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        by_date[s["date"]].append(s)
    for d, rows in by_date.items():
        for h in fwds:
            vals = [r["fwd"][h] for r in rows if r["fwd"][h] is not None]
            if len(vals) < 10:
                for r in rows:
                    r.setdefault("ex", {})[h] = None
                continue
            m = statistics.fmean(vals)
            for r in rows:
                if r["fwd"][h] is None:
                    r.setdefault("ex", {})[h] = None
                else:
                    r.setdefault("ex", {})[h] = r["fwd"][h] - m

    # ── 板块动量代理: concept 内成分股 pct_1m 中位数 ──
    concept_mom: dict[tuple[str, str], float] = {}
    for d, rows in by_date.items():
        acc: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            for c in r["concepts"]:
                acc[c].append(r["f"]["pct_1m"])
        for c, vals in acc.items():
            if vals:
                concept_mom[(d, c)] = statistics.median(vals)

    # ── 分桶统计 ──
    def agg(rows: list[dict]) -> dict:
        out = {"n": len(rows)}
        for h in fwds:
            abs_v = [r["fwd"][h] for r in rows if r["fwd"][h] is not None]
            ex_v = [r["ex"][h] for r in rows if r.get("ex", {}).get(h) is not None]
            out[f"abs_{h}d"] = round(statistics.fmean(abs_v), 2) if abs_v else None
            out[f"ex_{h}d"] = round(statistics.fmean(ex_v), 2) if ex_v else None
            out[f"win_{h}d"] = round(sum(1 for v in ex_v if v > 0) / len(ex_v) * 100, 1) if ex_v else None
        return out

    buckets: dict[str, list[dict]] = defaultdict(list)
    sector_split: dict[str, list[dict]] = defaultdict(list)
    for r in samples:
        tags = classify(r["f"])
        for t in tags:
            buckets[t].append(r)
        # 板块状态分层 (仅左向组合)
        if "L_STAB_ANY" in tags or "L_low30_calm" in tags:
            moms = [concept_mom.get((r["date"], c)) for c in r["concepts"]]
            moms = [m for m in moms if m is not None]
            if moms:
                m = max(moms)
                key = "weak" if m < -5 else ("strong" if m > 5 else "mid")
                for t in tags:
                    if t.startswith("L_"):
                        sector_split[f"{t}@{key}"].append(r)

    result = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "universe_n": len(universe),
            "universe_concepts": len(mapping),
            "samples": len(samples),
            "eval_dates": len(eval_dates),
            "eval_range": [eval_dates[0], eval_dates[-1]] if eval_dates else None,
            "step": args.step,
            "skipped_stocks": failed,
            "caveats": [
                "股票池为当前 concept 成分股 → 存在幸存者/选择偏差",
                "未叠加估值乖离 (跨历史 fair_value 成本高), 仅技术面/位置/量能",
                "板块状态用成分股 pct_1m 中位数代理, 非 sector_score",
                "超额收益 = 同日全样本去均值 (等权)",
            ],
        },
        "buckets": {k: agg(v) for k, v in sorted(buckets.items())},
        "sector_split": {k: agg(v) for k, v in sorted(sector_split.items())},
    }

    out_path = Path(args.out) if args.out else Path(
        f"/tmp/reversal_backtest_{datetime.now():%Y%m%d-%H%M}.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    # ── 终端 summary ──
    def show(name: str, st: dict) -> str:
        cells = [f"{name:<18}", f"n={st['n']:<7}"]
        for h in fwds:
            a, e, w = st.get(f"abs_{h}d"), st.get(f"ex_{h}d"), st.get(f"win_{h}d")
            cells.append(f"{h}d abs={a if a is not None else '-':>7} ex={e if e is not None else '-':>7} win={w if w is not None else '-':>5}")
        return " | ".join(cells)

    print("\n=== 信号分桶 (abs=绝对收益%, ex=横截面超额%, win=超额>0 比例%) ===")
    order = ["BASELINE_ALL", "L_low30", "L_low30_calm", "L_quiet_stab",
             "L_dry_then_pop", "L_pop_reclaim20", "L_reclaim_ma20", "L_dryup_flat",
             "L_low_deep1m", "L_deep15",
             "L_deep_dd40", "L_STAB_ANY", "L_STAB_STRONG",
             "R_tier0_chase", "R_buy_breakout", "R_momentum20", "R_pullback_any"]
    for name in order:
        if name in result["buckets"]:
            print(show(name, result["buckets"][name]))
    print("\n=== 左侧信号 × 板块状态 (weak=成分股 1M 中位 < -5%) ===")
    for name in sorted(result["sector_split"]):
        print(show(name, result["sector_split"][name]))

    print(f"\n[done] {out_path}  ({time.time()-t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
