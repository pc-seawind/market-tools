"""sector_picks.py — Tier 2-4 板块内个股筛选 (framework v2.3).

对一个 concept 跑完整 Tier 2-4 管道:
  Tier 2 (基本面): ROE > 板块中位数, 净利 YoY > +10%, 毛利 > 板块中位数
  Tier 3 (估值乖离): fair_value = method C (标准) 或 method B (业态剧变)
                     flow 正板块阈值 +20%, flow 负板块 +30%
  Tier 4 (技术时机): RS vs 板块 ETF, 位置, 量比, 1W, LAGGARD 窗口

输入: concept name (必须在 concepts_data.CONCEPTS 里)
输出: 每只成分股的四层评估 + 最终推荐 (BUY/WATCH/AVOID)

用法:
  sector_picks.py --sector "光通信 (光模块/CPO)"
  sector_picks.py --sector "金融-证券" --json
  sector_picks.py --sector "光通信 (光模块/CPO)" --min-deviation 30  (提高乖离门槛)
"""
from __future__ import annotations
import argparse
import csv
import json
import statistics
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from etf_data import sector_signals, etfs_for, SectorSignals
from sector_score import score_sector

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"


def _ts(api: str, **params) -> list[dict[str, str]]:
    args = ["python3", str(_TUSHARE), api]
    for k, v in params.items():
        if k == "fields":
            args.append(f"--fields={v}")
        else:
            args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def get_sector_stocks(concept: str) -> list[tuple[str, str]]:
    """Get 成分股 list. Returns [(ts_code, name), ...].

    Fallback: 若 concept 不在 concepts_data, 返回空 (用户需手工指定).
    Phase 2 后续: 用 ETF 持仓 (fund_portfolio 季报) 自动派生.
    """
    try:
        from concepts_data import CONCEPTS
        return list(CONCEPTS.get(concept, []))
    except ImportError:
        return []


@dataclass
class StockMetrics:
    code: str
    name: str
    trade_date: str
    close: float
    pct_1d: float
    pct_1w: float
    pct_1m: float
    position_pct: int           # v1 min-max 120d
    pct_rank_60d: int           # v2
    pct_rank_120d: int
    pct_rank_250d: int
    vol_ratio: float
    # daily_basic
    pe_ttm: float | None
    pb: float | None
    mkt_cap_yi: float | None    # 亿
    turnover: float | None      # % 换手率
    # fina_indicator latest
    fina_as_of: str | None
    roe: float | None
    gross_margin: float | None
    net_yoy: float | None
    rev_yoy: float | None
    # 估值历史
    pe_median_3y: float | None


def _percentile_of(window: list, val: float) -> int:
    if not window:
        return 50
    lower = sum(1 for x in window if x < val)
    equal = sum(1 for x in window if x == val)
    return int(round((lower + 0.5 * equal) / len(window) * 100))


def compute_stock(code: str, name: str) -> StockMetrics | None:
    """Pull and compute all per-stock metrics."""
    daily = _ts("daily", ts_code=code, fields="trade_date,close,vol,amount")
    if not daily:
        return None
    daily.sort(key=lambda x: x["trade_date"])
    closes = [float(r["close"]) for r in daily if r.get("close")]
    vols = [float(r["vol"]) for r in daily if r.get("vol")]
    if len(closes) < 20:
        return None

    close = closes[-1]
    trade_date = daily[-1]["trade_date"]
    pct_1d = (close / closes[-2] - 1) * 100 if len(closes) >= 2 else 0
    pct_1w = (close / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    pct_1m = (close / closes[-21] - 1) * 100 if len(closes) >= 21 else 0

    w120 = closes[-120:] if len(closes) >= 120 else closes
    hi, lo = max(w120), min(w120)
    position_pct = int(round((close - lo) / (hi - lo) * 100)) if hi > lo else 50

    pct60 = _percentile_of(closes[-60:] if len(closes) >= 60 else closes, close)
    pct120 = _percentile_of(w120, close)
    pct250 = _percentile_of(closes[-250:] if len(closes) >= 250 else closes, close)

    vol5 = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    vol20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0
    vol_ratio = vol5 / vol20 if vol20 > 0 else 0

    # daily_basic
    basic = _ts("daily_basic", ts_code=code, trade_date=trade_date,
                fields="pe_ttm,pb,total_mv,turnover_rate")
    b = basic[0] if basic else {}

    def bf(k: str) -> float | None:
        v = b.get(k)
        if v in (None, "", "None"):
            return None
        try:
            return float(v)
        except ValueError:
            return None

    # PE history
    pe_hist_rows = _ts("daily_basic", ts_code=code, fields="trade_date,pe_ttm")
    pes = [float(r["pe_ttm"]) for r in pe_hist_rows if r.get("pe_ttm") and r["pe_ttm"] not in ("", "None")]
    valid_pes = [p for p in pes if 5 < p < 500]
    pe_median = round(statistics.median(valid_pes), 1) if len(valid_pes) >= 30 else None

    # fina_indicator
    fina = _ts("fina_indicator", ts_code=code,
               fields="end_date,roe,grossprofit_margin,netprofit_yoy,or_yoy")
    fina_latest = {}
    if fina:
        fina.sort(key=lambda x: x.get("end_date", ""), reverse=True)
        fina_latest = fina[0]

    def ff(k: str) -> float | None:
        v = fina_latest.get(k)
        if v in (None, "", "None"):
            return None
        try:
            return float(v)
        except ValueError:
            return None

    return StockMetrics(
        code=code, name=name, trade_date=trade_date, close=close,
        pct_1d=round(pct_1d, 2), pct_1w=round(pct_1w, 2), pct_1m=round(pct_1m, 2),
        position_pct=position_pct,
        pct_rank_60d=pct60, pct_rank_120d=pct120, pct_rank_250d=pct250,
        vol_ratio=round(vol_ratio, 2),
        pe_ttm=bf("pe_ttm"),
        pb=bf("pb"),
        mkt_cap_yi=round(float(b["total_mv"])/10000, 1) if b.get("total_mv") else None,
        turnover=bf("turnover_rate"),
        fina_as_of=fina_latest.get("end_date"),
        roe=ff("roe"),
        gross_margin=ff("grossprofit_margin"),
        net_yoy=ff("netprofit_yoy"),
        rev_yoy=ff("or_yoy"),
        pe_median_3y=pe_median,
    )


# ─── Tier 2-4 评估 ────────────────────────────────────────────────────────

@dataclass
class PickEvaluation:
    stock: dict[str, Any]                 # StockMetrics as dict
    # Tier 2
    tier2_pass: bool
    tier2_reasons: list[str]
    # Tier 3
    fair_value: float | None
    fv_method: str                        # "C" | "B_only" | "skip"
    deviation_pct: float | None           # (fv - price) / fv × 100
    # Tier 4
    rs_5d: float                          # stock pct_1w - sector nav_5d (matching windows)
    rs_tier: str                          # LEADER / FOLLOWER / LAGGARD / STUCK
    position_band: str                    # < 70 / 70-85 / > 85
    tier4_position_size_max: float        # ≤ 8 / 5 / 3
    # 最终
    verdict: str                          # BUY / WATCH / AVOID
    reason: str                           # 一句话解释


def tier2_quality(s: StockMetrics, sector_roe_median: float, sector_margin_median: float) -> tuple[bool, list[str]]:
    """Tier 2 基本面筛选."""
    reasons = []
    ok = True

    if s.roe is None:
        reasons.append("ROE 数据缺")
        ok = False
    elif s.roe < 0:
        reasons.append(f"ROE 负 ({s.roe}%)")
        ok = False
    elif s.roe < sector_roe_median:
        reasons.append(f"ROE {s.roe}% < 板块中位 {sector_roe_median}%")
        # 不 hard-fail, 但扣分

    if s.net_yoy is None:
        reasons.append("净利 YoY 数据缺")
    elif s.net_yoy < 10:
        reasons.append(f"净利 YoY {s.net_yoy}% < +10%")
        ok = False

    if s.gross_margin is None:
        reasons.append("毛利率数据缺")
    elif s.gross_margin < sector_margin_median:
        reasons.append(f"毛利 {s.gross_margin}% < 板块中位 {sector_margin_median}%")
        # 不 hard-fail

    return ok, reasons


def tier3_fair_value(s: StockMetrics, peer_pe_median: float) -> tuple[float | None, str, float | None]:
    """Tier 3 估值乖离.
    Return (fair_value, method, deviation_pct).
    framework v2.3: 业态剧变场景 (|A-B|/B > 50%) 只用 method B.
    """
    if s.pe_ttm is None or s.pe_ttm <= 0 or peer_pe_median is None:
        return None, "skip", None
    eps_ttm = s.close / s.pe_ttm

    if s.pe_median_3y is not None:
        fv_a = s.pe_median_3y * eps_ttm
        fv_b = peer_pe_median * eps_ttm
        # 业态剧变判定
        if fv_b > 0 and abs(fv_a - fv_b) / fv_b > 0.5:
            fv = fv_b
            method = "B_only (业态剧变)"
        else:
            fv = (fv_a + fv_b) / 2
            method = "C (平均)"
    else:
        fv = peer_pe_median * eps_ttm
        method = "B_only (无历史 PE)"

    deviation = round((fv - s.close) / fv * 100, 1) if fv > 0 else None
    return round(fv, 2), method, deviation


def tier4_timing(s: StockMetrics, sector_nav_5d: float) -> dict[str, Any]:
    """Tier 4 技术面 + RS + 仓位上限."""
    rs = round(s.pct_1w - sector_nav_5d, 2)
    if rs > 10:
        rs_tier = "LEADER"
    elif rs < -10:
        rs_tier = "LAGGARD"
    else:
        rs_tier = "FOLLOWER"
    # STUCK (持续 LAGGARD > 2 周): TODO 需要 2 周板块数据, 暂不实现

    # 用 pct_rank_120d 判位置 band (也可以用 pct_rank_60d)
    pos = s.pct_rank_120d
    if pos < 70:
        band = "< 70"
        max_size = 8.0
    elif pos <= 85:
        band = "70-85"
        max_size = 5.0
    else:
        band = "> 85"
        max_size = 3.0

    return {"rs_5d": rs, "rs_tier": rs_tier, "position_band": band, "max_size": max_size}


def evaluate(s: StockMetrics, sector_sig: SectorSignals,
             sector_roe_median: float, sector_margin_median: float, peer_pe_median: float,
             min_deviation: float) -> PickEvaluation:
    # Tier 2
    t2_ok, t2_reasons = tier2_quality(s, sector_roe_median, sector_margin_median)
    # Tier 3
    fv, method, deviation = tier3_fair_value(s, peer_pe_median)
    # Tier 4
    t4 = tier4_timing(s, sector_sig.nav_pct_5d)

    # Verdict
    verdict = "AVOID"
    reason = ""

    if not t2_ok and "ROE 负" in " ".join(t2_reasons):
        verdict = "AVOID"
        reason = f"基本面破产 ({'; '.join(t2_reasons[:2])})"
    elif not t2_ok:
        verdict = "AVOID"
        reason = f"基本面不过关 ({'; '.join(t2_reasons[:2])})"
    elif deviation is None:
        verdict = "WATCH"
        reason = "估值无法计算"
    elif deviation >= min_deviation:
        # 够便宜
        if t4["position_band"] == "> 85" and s.pct_1w > 15:
            # 位置末期, framework v2.3 允许 if flow 强正 + 乖离 > 40%
            if sector_sig.flow_5d_cny > 0 and deviation >= 40:
                verdict = "BUY"
                reason = f"乖离 {deviation:+.1f}% 大 + 板块 flow 正 ({sector_sig.flow_5d_cny/1e8:+.1f}亿/5d), 位置末期允许小仓 (≤ 3%)"
            else:
                verdict = "WATCH"
                reason = f"乖离 {deviation:+.1f}% 但位置 > 85 + 1W+{s.pct_1w}% 末期 + flow 不够正, 等回调"
        else:
            verdict = "BUY"
            reason = f"乖离 {deviation:+.1f}% + {t4['rs_tier']} + 位置 {t4['position_band']}, 仓位 ≤ {t4['max_size']}%"
    elif deviation >= min_deviation - 10:  # 接近阈值
        verdict = "WATCH"
        reason = f"乖离 {deviation:+.1f}% 接近阈值 {min_deviation}%, 观察"
    else:
        verdict = "AVOID"
        reason = f"乖离 {deviation:+.1f}% 不够"

    return PickEvaluation(
        stock=asdict(s),
        tier2_pass=t2_ok, tier2_reasons=t2_reasons,
        fair_value=fv, fv_method=method, deviation_pct=deviation,
        rs_5d=t4["rs_5d"], rs_tier=t4["rs_tier"],
        position_band=t4["position_band"], tier4_position_size_max=t4["max_size"],
        verdict=verdict, reason=reason,
    )


# ─── 主流程 ───────────────────────────────────────────────────────────────

def sector_picks(concept: str, min_deviation: float = 20.0) -> dict[str, Any]:
    """Full pipeline for one concept."""
    # 1. Score the sector (Tier 1)
    score = score_sector(concept)
    if not score:
        return {"error": f"sector {concept} 无数据"}

    # 2. Get sector signals (for nav_5d baseline + flow 判阈值)
    sig = sector_signals(concept)
    if not sig:
        return {"error": f"sector {concept} 无 ETF 数据, 无法计算 RS"}

    # flow 正板块用 +20%, flow 负板块用 +30% (framework v2.3)
    if sig.flow_20d_cny < 0:
        min_dev_effective = max(min_deviation, 30.0)
    else:
        min_dev_effective = min_deviation

    # 3. Get 成分股
    stocks_list = get_sector_stocks(concept)
    if not stocks_list:
        return {"error": f"concept {concept} 不在 concepts_data.CONCEPTS, 需手工指定成分股"}

    # 4. Compute per-stock metrics (并行可优化, 先串行)
    stocks_m: list[StockMetrics] = []
    for code, name in stocks_list:
        m = compute_stock(code, name)
        if m:
            stocks_m.append(m)

    if not stocks_m:
        return {"error": f"concept {concept} 成分股全部 fetch 失败"}

    # 5. 板块中位数 (for Tier 2)
    roes = [s.roe for s in stocks_m if s.roe is not None]
    gms = [s.gross_margin for s in stocks_m if s.gross_margin is not None]
    pes = [s.pe_ttm for s in stocks_m if s.pe_ttm is not None and 5 < s.pe_ttm < 300]

    sector_roe_median = round(statistics.median(roes), 1) if roes else 0
    sector_margin_median = round(statistics.median(gms), 1) if gms else 0
    peer_pe_median = round(statistics.median(pes), 1) if pes else 0

    # 6. Evaluate each
    evals = [evaluate(s, sig, sector_roe_median, sector_margin_median, peer_pe_median, min_dev_effective)
             for s in stocks_m]

    # 7. Rank by deviation (desc)
    evals.sort(key=lambda e: -(e.deviation_pct or -999))

    return {
        "concept": concept,
        "sector_score": asdict(score),
        "sector_signals": {
            "nav_5d": sig.nav_pct_5d, "nav_1m": sig.nav_pct_1m,
            "flow_5d_cny": sig.flow_5d_cny, "flow_20d_cny": sig.flow_20d_cny,
            "pct_rank_60d": sig.pct_rank_60d, "pct_rank_250d": sig.pct_rank_250d,
        },
        "sector_benchmarks": {
            "roe_median": sector_roe_median,
            "gross_margin_median": sector_margin_median,
            "peer_pe_median": peer_pe_median,
            "min_deviation_effective": min_dev_effective,
        },
        "evaluations": [asdict(e) for e in evals],
    }


# ─── CLI ──────────────────────────────────────────────────────────────────

def _print_report(result: dict[str, Any]):
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    c = result["concept"]
    ss = result["sector_signals"]
    sb = result["sector_benchmarks"]
    score = result["sector_score"]

    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  sector_picks: {c}")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Tier 1 总分: {score['total_score']} / 100  ({score['tier']})")
    print(f"  板块: nav_5d={ss['nav_5d']:+.2f}%  nav_1m={ss['nav_1m']:+.2f}%  pos60={ss['pct_rank_60d']}  pos250={ss['pct_rank_250d']}")
    print(f"        flow_5d={ss['flow_5d_cny']/1e8:+.2f}亿  flow_20d={ss['flow_20d_cny']/1e8:+.2f}亿")
    print(f"  板块 benchmark: ROE_med={sb['roe_median']}%  毛利_med={sb['gross_margin_median']}%  同业 PE_med={sb['peer_pe_median']}")
    print(f"  乖离阈值: {sb['min_deviation_effective']}% (flow 负板块自动抬高)")
    print()

    def fmt_row(e):
        s = e["stock"]
        verdict_icon = {"BUY": "🥇", "WATCH": "👀", "AVOID": "❌"}.get(e["verdict"], "?")
        dev = e["deviation_pct"]
        dev_s = f"{dev:+.1f}%" if dev is not None else "  -"
        roe = s.get("roe")
        yoy = s.get("net_yoy")
        return (
            f"{verdict_icon} {s['code']:<10} {s['name'][:8]:<8} "
            f"close={s['close']:>7.2f}  PE={str(s.get('pe_ttm','--'))[:6]:>6}  "
            f"1W={s['pct_1w']:>6.1f}%  位置={s.get('pct_rank_120d',0):>3}%  "
            f"FV={str(e.get('fair_value','-'))[:7]:>7}  乖离={dev_s:>7}  "
            f"ROE={str(roe)[:5] if roe is not None else '-':>5}%  "
            f"净利YoY={str(yoy)[:6] if yoy is not None else '-':>6}%  "
            f"{e['rs_tier']:<8}"
        )

    print("  === 成分股评估 (按乖离降序) ===")
    for e in result["evaluations"]:
        print(f"  {fmt_row(e)}")
        print(f"     → {e['verdict']}: {e['reason']}")
    print()

    # BUY 候选总结
    buys = [e for e in result["evaluations"] if e["verdict"] == "BUY"]
    watches = [e for e in result["evaluations"] if e["verdict"] == "WATCH"]
    if buys:
        print(f"  🥇 BUY 候选 ({len(buys)}):")
        for e in buys:
            print(f"     {e['stock']['code']} {e['stock']['name']}  仓位 ≤ {e['tier4_position_size_max']}%  乖离 {e['deviation_pct']:+.1f}%")
    if watches:
        print(f"  👀 WATCH ({len(watches)}):  " + ", ".join(f"{e['stock']['code']} {e['stock']['name']}" for e in watches[:5]))


def main():
    ap = argparse.ArgumentParser(description="Tier 2-4 板块内个股筛选 (framework v2.3).")
    ap.add_argument("--sector", required=True, help="concept name (must be in concepts_data.CONCEPTS)")
    ap.add_argument("--min-deviation", type=float, default=20.0,
                    help="最低乖离阈值 %% (flow 负板块会自动抬高到 30)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args()

    result = sector_picks(args.sector, min_deviation=args.min_deviation)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_report(result)


if __name__ == "__main__":
    main()
