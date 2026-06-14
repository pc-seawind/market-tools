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

# 短线层 z_block 信号 (2026-05-25 P0-2: news_score 拒收后转 sector_picks 短线层)
# 见 sector_picks_block_trade.py docstring + docs/news_score_phase2_design.md
try:
    import sector_picks_block_trade as _spbt
    _SPBT_AVAILABLE = True
except Exception:
    _SPBT_AVAILABLE = False

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"
_HISTORY_LOG = _HERE / "sector_picks_history.jsonl"


def _append_history(concept: str, score_total: float, score_tier: str,
                    evaluations: list[dict[str, Any]]) -> None:
    """每次 sector_picks() 跑完, append 一行 / 股 到 sector_picks_history.jsonl.

    用途: watchlist_decay.py 的 "从未触发 BUY" 信号需要历史 verdict 数据.
    schema 跟 rec_log.jsonl 区分 — 这里是 sector 评估的 PER-STOCK verdict 流,
    不是用户操作记录, 也不带成本 / 持仓信息.
    """
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    try:
        with _HISTORY_LOG.open("a", encoding="utf-8") as f:
            for e in evaluations:
                s = e.get("stock", {})
                rec = {
                    "ts": ts,
                    "concept": concept,
                    "code": s.get("code"),
                    "name": s.get("name"),
                    "verdict": e.get("verdict"),
                    "reason": e.get("reason"),
                    "deviation_pct": e.get("deviation_pct"),
                    "rs_tier": e.get("rs_tier"),
                    "tier0_pass": e.get("tier0_pass", False),
                    "sector_score_total": score_total,
                    "sector_score_tier": score_tier,
                    "pct_rank_120d": s.get("pct_rank_120d"),
                    "pct_rank_250d": s.get("pct_rank_250d"),
                    "roe": s.get("roe"),
                    "net_yoy": s.get("net_yoy"),
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as ex:
        # 落盘失败不阻断主流程
        import sys as _sys
        print(f"[warn] sector_picks_history.jsonl append failed: {ex}", file=_sys.stderr)


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
    pct_5d: float               # 新增 2026-05-20: 5d 累计涨幅, 配 vol_ratio_5d
    pct_1w: float
    pct_1m: float
    position_pct: int           # v1 min-max 120d
    pct_rank_60d: int           # v2
    pct_rank_120d: int
    pct_rank_250d: int
    # 量价 (修 2026-05-20: 涨跌窗口必须 ↔ 量比窗口对齐)
    vol_ratio_1d: float         # 今日量 / 过去5日均量 (排除今日) — 配 pct_1d
    vol_ratio_5d: float         # 5日均量 / 20日均量 — 配 pct_5d, 看趋势
    volume_signal: str          # DRY_UP / NORMAL / EXPANDING / BLOWOFF
    pv_alignment: str           # 价量同窗口对照: 涨而不补量(真) / 放量上涨(真) / 放量下跌 / 中性 / ...
    vol_ratio: float            # 兼容字段 = vol_ratio_5d (别处可能引用)
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
    # 复权 (rule 2 P0, 新增 2026-05-21): Tushare daily 默认未复权, 行权除权造成虚假"高点"
    # qfq_applied: 是否已对历史 closes/vols 应用前复权
    # adj_events_60d: 60 个交易日内除权事件次数 (>0 说明近期有除权, 数据需特别注意)
    qfq_applied: bool = False
    adj_events_60d: int = 0


def _percentile_of(window: list, val: float) -> int:
    if not window:
        return 50
    lower = sum(1 for x in window if x < val)
    equal = sum(1 for x in window if x == val)
    return int(round((lower + 0.5 * equal) / len(window) * 100))


def _classify_volume(vol_1d_ratio: float, vol_5d_ratio: float) -> str:
    """量能分级.

    vol_1d_ratio = 今日量 / 过去5日均量 (排除今日) — 短期 burst
    vol_5d_ratio = 5日均量 / 20日均量             — 中期趋势
    """
    if vol_1d_ratio < 0.7 and vol_5d_ratio < 0.9:
        return "DRY_UP"        # 持续萎缩
    if vol_1d_ratio > 1.8 and vol_5d_ratio > 1.2:
        return "BLOWOFF"       # 爆量, 短期高潮
    if vol_1d_ratio > 1.3 or vol_5d_ratio > 1.15:
        return "EXPANDING"     # 持续放量
    return "NORMAL"


def _classify_pv(pct_1d: float, pct_5d: float, vol_1d_ratio: float, vol_5d_ratio: float) -> str:
    """价量同窗口对照.

    1d 涨跌幅 vs 1d 量比, 5d 涨跌幅 vs 5d 量比. 把两层信号合成一句结论.
    """
    # 1d 维度
    if pct_1d > 0.5 and vol_1d_ratio < 0.85:
        d1 = "1d涨而不补量"
    elif pct_1d > 2 and vol_1d_ratio < 0.7:
        d1 = "1d强涨萎量(警)"
    elif pct_1d > 0.5 and vol_1d_ratio > 1.3:
        d1 = "1d放量上涨"
    elif pct_1d < -0.5 and vol_1d_ratio > 1.3:
        d1 = "1d放量下跌"
    elif pct_1d < -0.5 and vol_1d_ratio < 0.85:
        d1 = "1d缩量回调"
    else:
        d1 = ""

    # 5d 维度
    if pct_5d > 3 and vol_5d_ratio > 1.15:
        d5 = "5d放量推升"
    elif pct_5d > 3 and vol_5d_ratio < 0.95:
        d5 = "5d缩量上涨"
    elif pct_5d < -3 and vol_5d_ratio > 1.15:
        d5 = "5d放量调整"
    else:
        d5 = ""

    if d1 and d5:
        return f"{d1} | {d5}"
    return d1 or d5 or "中性"


def compute_stock(code: str, name: str) -> StockMetrics | None:
    """Pull and compute all per-stock metrics.

    Rule 2 (P0, 新增 2026-05-21): Tushare daily 默认返回未复权价. 当个股发生送转/配股/
    限售解禁等机械除权时, 历史 close 会保留事件前的虚高 (例: 寒武纪 2026-05-08 行权,
    adj_factor 1.0 → 1.4912, 真实 1966 → qfq 1318), 直接用未复权数据算 pos250 / 距高点
    回撤会得出"还有 30% 安全垫"的假象, 但实际已创历史新高. 这里强制拉 adj_factor 应用
    前复权, 让所有 closes 在今日口径下可比.
    """
    daily = _ts("daily", ts_code=code, fields="trade_date,close,vol,amount")
    if not daily:
        return None
    daily.sort(key=lambda x: x["trade_date"])

    # 构造对齐的 (date, close, vol) 三元组, 过滤无效行 — 避免 closes 和 vols 长度不一致
    rows: list[tuple[str, float, float]] = []
    for r in daily:
        try:
            d = r["trade_date"]
            c = float(r["close"])
            v = float(r.get("vol") or 0)
            if c > 0:
                rows.append((d, c, v))
        except (ValueError, KeyError, TypeError):
            continue
    if len(rows) < 20:
        return None

    # ── 前复权处理 (rule 2 P0) ──
    qfq_applied = False
    adj_events_60d = 0
    adj_rows = _ts("adj_factor", ts_code=code, fields="trade_date,adj_factor")
    if adj_rows:
        adj_map: dict[str, float] = {}
        for r in adj_rows:
            try:
                a = float(r["adj_factor"])
                if a > 0:
                    adj_map[r["trade_date"]] = a
            except (ValueError, KeyError, TypeError):
                continue
        latest_date = rows[-1][0]
        latest_adj = adj_map.get(latest_date)
        if latest_adj and latest_adj > 0:
            # 全窗口 adj_factor 是否变化 → 决定是否需要前复权
            window_adjs = [adj_map.get(d) for d, _, _ in rows]
            window_adjs_clean = [a for a in window_adjs if a is not None]
            if window_adjs_clean and len({round(a, 4) for a in window_adjs_clean}) > 1:
                qfq_applied = True
                # 60 日内除权事件计数 (近期数据警告用)
                last_60 = rows[-60:] if len(rows) >= 60 else rows
                seen_adjs_60 = {round(adj_map.get(d, latest_adj), 4) for d, _, _ in last_60}
                adj_events_60d = max(0, len(seen_adjs_60) - 1)
                # 应用前复权: close_qfq = close × adj_factor / latest_adj
                rows = [(d, c * (adj_map.get(d, latest_adj) / latest_adj), v)
                        for d, c, v in rows]

    closes = [c for _, c, _ in rows]
    vols = [v for _, _, v in rows]

    close = closes[-1]
    trade_date = rows[-1][0]
    pct_1d = (close / closes[-2] - 1) * 100 if len(closes) >= 2 else 0
    pct_5d = (close / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
    pct_1w = pct_5d  # 1W ≈ 5个交易日, 兼容旧字段
    pct_1m = (close / closes[-21] - 1) * 100 if len(closes) >= 21 else 0

    w120 = closes[-120:] if len(closes) >= 120 else closes
    hi, lo = max(w120), min(w120)
    position_pct = int(round((close - lo) / (hi - lo) * 100)) if hi > lo else 50

    pct60 = _percentile_of(closes[-60:] if len(closes) >= 60 else closes, close)
    pct120 = _percentile_of(w120, close)
    pct250 = _percentile_of(closes[-250:] if len(closes) >= 250 else closes, close)

    # ── 量能 (修 2026-05-20: 涨跌幅 ↔ 量比 必须同窗口对齐) ──
    # 1d: 今日量 vs 过去 5 天均量 (排除今天) → 配 pct_1d
    # 5d: 5d MA vs 20d MA                  → 配 pct_5d
    vol_today = vols[-1] if vols else 0
    vol_5d_excl = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else 0
    vol_ratio_1d = vol_today / vol_5d_excl if vol_5d_excl > 0 else 0
    vol5 = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    vol20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0
    vol_ratio_5d = vol5 / vol20 if vol20 > 0 else 0
    volume_signal = _classify_volume(vol_ratio_1d, vol_ratio_5d)
    pv_alignment = _classify_pv(pct_1d, pct_5d, vol_ratio_1d, vol_ratio_5d)

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
        pct_1d=round(pct_1d, 2), pct_5d=round(pct_5d, 2),
        pct_1w=round(pct_1w, 2), pct_1m=round(pct_1m, 2),
        position_pct=position_pct,
        pct_rank_60d=pct60, pct_rank_120d=pct120, pct_rank_250d=pct250,
        vol_ratio_1d=round(vol_ratio_1d, 2),
        vol_ratio_5d=round(vol_ratio_5d, 2),
        volume_signal=volume_signal,
        pv_alignment=pv_alignment,
        vol_ratio=round(vol_ratio_5d, 2),  # 兼容
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
        qfq_applied=qfq_applied,
        adj_events_60d=adj_events_60d,
    )


# ─── Tier 2-4 评估 ────────────────────────────────────────────────────────

@dataclass
class PickEvaluation:
    stock: dict[str, Any]                 # StockMetrics as dict
    # Tier 0 (旁路, 新增 2026-05-20)
    tier0_pass: bool                      # 趋势龙头通道是否触发
    tier0_reasons: list[str]              # 满足/未满足项
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
    verdict: str                          # BUY / WATCH / AVOID / TREND_BUY / TREND_WATCH
    reason: str                           # 一句话解释


def tier0_trend_leader(s: StockMetrics, sector_sig: SectorSignals,
                       sector_score_total: float) -> tuple[bool, list[str]]:
    """Tier 0 趋势龙头旁路 (新增 2026-05-20).

    动机: 高估值景气龙头 (寒武纪 PE 270 / 中际旭创 PE 50+) 在 Tier 3 估值乖离过滤
          下必然被砍, 但板块 HOT + 主力疯狂流入 + 北向重仓 + 业绩同比 +100% 时,
          系统应该承认"在 HOT 板块跟龙头"是合法策略, 不能用"防垃圾股"的滤网套上来.

    触发 (全部 AND, 任一不满足都不进 Tier 0):
      [板块层]
        - sector_score >= 60   (HOT 或顶部 NEUTRAL)
        - sector_sig.flow_5d >= +3 亿 (强资金正流入, 不是单点反弹)
      [个股层]
        - pct_rank_120d >= 75  (120日位置高位, 趋势已确认)
        - pct_1m >= 8          (1月动量正)
        - net_yoy >= 50        (业绩硬底, 防纯炒作壳股)
        - vol_ratio_5d >= 0.95 (5日量能不萎缩)
        - volume_signal != "DRY_UP"  (短期不持续干涸)

    Return:
      (True, [全部满足项])  → 走 Tier 0 通道, 跳过 Tier 3 估值否决
      (False, [缺哪些])     → 回归常规 Tier 2-4 流程
    """
    checks: list[tuple[bool, str]] = []

    checks.append((sector_score_total >= 60,
                   f"板块 Tier 1 = {sector_score_total:.1f} {'✓' if sector_score_total >= 60 else f'< 60 ✗'}"))
    flow_yi = sector_sig.flow_5d_cny / 1e8
    checks.append((flow_yi >= 3,
                   f"板块 flow_5d {flow_yi:+.1f}亿 {'✓' if flow_yi >= 3 else '< +3亿 ✗'}"))
    checks.append((s.pct_rank_120d >= 75,
                   f"位置 pos120 = {s.pct_rank_120d}% {'✓' if s.pct_rank_120d >= 75 else '< 75 ✗'}"))
    checks.append((s.pct_1m >= 8,
                   f"1M 动量 = {s.pct_1m:+.1f}% {'✓' if s.pct_1m >= 8 else '< +8% ✗'}"))
    if s.net_yoy is None:
        checks.append((False, "净利 YoY 数据缺 ✗"))
    else:
        checks.append((s.net_yoy >= 50,
                       f"净利 YoY = {s.net_yoy:+.1f}% {'✓' if s.net_yoy >= 50 else '< +50% ✗'}"))
    checks.append((s.vol_ratio_5d >= 0.95,
                   f"vol_5d/20d = {s.vol_ratio_5d:.2f} {'✓' if s.vol_ratio_5d >= 0.95 else '< 0.95 ✗'}"))
    checks.append((s.volume_signal != "DRY_UP",
                   f"vol_signal = {s.volume_signal} {'✓' if s.volume_signal != 'DRY_UP' else '✗ DRY_UP'}"))

    ok = all(c[0] for c in checks)
    return ok, [c[1] for c in checks]


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


def _position_guard(s: StockMetrics, sector_score_total: float, sector_sig: SectorSignals,
                    verdict: str, reason: str) -> tuple[str, str]:
    """末端抱团 guard (rule 1, 新增 2026-05-21).

    板块退潮 + 个股逼近 / 创历史新高 + 持续推升 = 教科书"板块退潮龙头末段抱团"形态.
    A 股最经典的顶部信号: 资金集中往最后一只龙头里塞, 拉抬掩护出货 (last call rally).
    在这种条件下即使 Tier 0 趋势通道触发 / 个股资金面强 / 业绩硬, 也强制降级 AVOID.

    来源: 2026-05-21 W21 寒武纪 case study.
      - 复权后实际创历史新高 (pos250 ≈ 100%)
      - 板块 ETF -57亿 / 20d 派发 (sector_score 27.8 < 30 COLD)
      - 5/19-20 急涨 +12.7% 持续推升
      → 最初判 WATCH+ 错; 加这条 guard 后自动否决.

    触发 (前 2 条 AND, momentum 是 OR):
      - sector_score_total < 30           (板块 COLD)
      - s.pct_rank_250d >= 95             (250 日位置高位 ≈ 接近历史新高;
                                           前提是 rule 2 已应用 qfq, 否则 pos250 不可信)
      - momentum 信号 (任一即触发):
          (a) s.pct_5d >= 5               (5 日持续推升)
          (b) s.pct_1m >= 25              (1 月暴涨进高点 — 抓"先杀后拉的洗盘"形态;
                                           今日寒武纪 5/14-15 急跌后 5/19-20 暴拉, 5d
                                           只 +3.5% 但 1m +52%, 真正末段抱团信号在 1m)

    动作: 任何非 AVOID 判定 → AVOID + 顶部警告
    保留 Tier 0 的 reasons 不动 (报告里仍能看到 Tier 0 通过, 只是被 guard 否决).

    NOTE: 不再 require adj_events_60d == 0 — 早期版本加这条试图避免"复权口径变化误报",
    但 rule 2 的 qfq 已经把历史价校准到今日口径, pos250 本来就反映真实位置. 加这条
    会把"60 日内有除权 + 创新高"的真 case (例: 寒武纪) 错误放过, 已删除.

    TODO 未来扩展: 引入个股 20d 主力净流入数据, 当 inflow > +20亿 时 reason 描述更精确
    (当前版本只用价格位置 + 板块 flow, 已经够堵今日 case 漏洞).
    """
    if verdict == "AVOID":
        return verdict, reason
    flow_yi_20d = sector_sig.flow_20d_cny / 1e8
    momentum_5d = s.pct_5d >= 5
    momentum_1m = s.pct_1m >= 25
    if (sector_score_total < 30
            and s.pct_rank_250d >= 95
            and (momentum_5d or momentum_1m)):
        # 描述选最强的那个 momentum 信号
        if momentum_1m and not momentum_5d:
            mom_desc = f"1m {s.pct_1m:+.1f}% 暴涨进新高"
        elif momentum_5d and momentum_1m:
            mom_desc = f"5d {s.pct_5d:+.1f}% / 1m {s.pct_1m:+.1f}% 持续推升"
        else:
            mom_desc = f"5d {s.pct_5d:+.1f}% 持续推升"
        new_reason = (
            f"⚠️ 末端抱团警告 (rule 1): 板块 Tier1={sector_score_total:.0f}<30 退潮 "
            f"(flow_20d {flow_yi_20d:+.1f}亿) + 个股 pos250={s.pct_rank_250d}% 历史新高区 "
            f"+ {mom_desc} = 板块退潮龙头末段抱团形态, 强制 AVOID 即使 [{verdict}] 通道通过."
            f" (原: {reason[:50]})"
        )
        return "AVOID", new_reason
    return verdict, reason


def evaluate(s: StockMetrics, sector_sig: SectorSignals,
             sector_score_total: float,
             sector_roe_median: float, sector_margin_median: float, peer_pe_median: float,
             min_deviation: float) -> PickEvaluation:
    # Tier 0 (旁路检查) — 即使触发也仍计算 Tier 2-4 用于报告
    t0_ok, t0_reasons = tier0_trend_leader(s, sector_sig, sector_score_total)
    # Tier 2
    t2_ok, t2_reasons = tier2_quality(s, sector_roe_median, sector_margin_median)
    # Tier 3
    fv, method, deviation = tier3_fair_value(s, peer_pe_median)
    # Tier 4
    t4 = tier4_timing(s, sector_sig.nav_pct_5d)

    # Verdict
    verdict = "AVOID"
    reason = ""

    # 硬否决: ROE 负 (业绩破产) — Tier 0 也救不回来
    if s.roe is not None and s.roe < 0:
        verdict = "AVOID"
        reason = f"ROE 负 ({s.roe}%) 基本面破产"
        return PickEvaluation(
            stock=asdict(s),
            tier0_pass=t0_ok, tier0_reasons=t0_reasons,
            tier2_pass=t2_ok, tier2_reasons=t2_reasons,
            fair_value=fv, fv_method=method, deviation_pct=deviation,
            rs_5d=t4["rs_5d"], rs_tier=t4["rs_tier"],
            position_band=t4["position_band"], tier4_position_size_max=t4["max_size"],
            verdict=verdict, reason=reason,
        )

    # ── Tier 0 旁路: 趋势龙头通道 ──
    if t0_ok:
        # 末期超买 (位置 > 90 + 5d > 25% 极速拉升) 才降级到 TREND_WATCH
        # 这比常规 > 85 更宽容, 因为 Tier 0 标的本来就是位置高位的趋势股
        if s.pct_rank_120d >= 90 and s.pct_5d > 25:
            verdict = "TREND_WATCH"
            reason = (f"Tier 0 趋势龙头 + 5d{s.pct_5d:+.1f}% 极速拉升 + 位置 {s.pct_rank_120d}% 顶部, "
                      f"等回调或量能验证再加仓 (仓位 ≤ {min(t4['max_size'], 3.0)}%)")
        else:
            # 仓位上限按 t4 算的位置 band 限制
            verdict = "TREND_BUY"
            flow_yi = sector_sig.flow_5d_cny / 1e8
            yoy_str = f"+{s.net_yoy:.0f}%" if s.net_yoy is not None else "?"
            reason = (f"Tier 0 趋势龙头 (估值乖离不适用): 板块 {sector_score_total:.0f} HOT + flow_5d {flow_yi:+.1f}亿"
                      f", 业绩 YoY {yoy_str}, pos120={s.pct_rank_120d}%, 仓位 ≤ {t4['max_size']}%")
        # rule 1 末端抱团 guard 也要对 Tier 0 通道应用 (Tier 0 触发但板块同时退潮 → 顶部抱团)
        verdict, reason = _position_guard(s, sector_score_total, sector_sig, verdict, reason)
        return PickEvaluation(
            stock=asdict(s),
            tier0_pass=t0_ok, tier0_reasons=t0_reasons,
            tier2_pass=t2_ok, tier2_reasons=t2_reasons,
            fair_value=fv, fv_method=method, deviation_pct=deviation,
            rs_5d=t4["rs_5d"], rs_tier=t4["rs_tier"],
            position_band=t4["position_band"], tier4_position_size_max=t4["max_size"],
            verdict=verdict, reason=reason,
        )

    # ── 常规路径: Tier 2 → Tier 3 → Tier 4 ──
    if not t2_ok:
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

    # rule 1 末端抱团 guard (新增 2026-05-21) — 应用于常规路径
    verdict, reason = _position_guard(s, sector_score_total, sector_sig, verdict, reason)

    return PickEvaluation(
        stock=asdict(s),
        tier0_pass=t0_ok, tier0_reasons=t0_reasons,
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

    # sector_score 现在会把 active fund-flow source 写入 raw_signals:
    #   - primary: Tushare moneyflow_ind_dc (BK z-score)
    #   - fallback: THS/AkShare 5d/20d 板块资金流
    # ETF share-flow 已被回测判定反向, 不应继续驱动 sector_picks 的阈值/报告。
    raw = score.raw_signals or {}
    active_flow_5d = raw.get("flow_5d_cny", sig.flow_5d_cny)
    active_flow_20d = raw.get("flow_20d_cny", sig.flow_20d_cny)
    active_flow_source = raw.get("flow_source", "etf_share_deprecated")
    sig.flow_5d_cny = active_flow_5d
    sig.flow_20d_cny = active_flow_20d

    # flow 正板块用 +20%, flow 负板块用 +30% (framework v2.3)
    if active_flow_20d < 0:
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

    # 6. Evaluate each (Tier 0 趋势龙头通道需要 sector total score, 这里传)
    evals = [evaluate(s, sig, score.total_score,
                      sector_roe_median, sector_margin_median, peer_pe_median, min_dev_effective)
             for s in stocks_m]

    # 7. Rank by deviation (desc)
    evals.sort(key=lambda e: -(e.deviation_pct or -999))

    # 8. Concept-level 短线 z_block signal (P0-2 2026-05-25, informational only)
    block_signal: dict[str, Any] | None = None
    if _SPBT_AVAILABLE:
        try:
            sig_block = _spbt.concept_block_signal(concept)
            block_signal = _spbt.to_dict(sig_block)
        except Exception as e:
            block_signal = {"error": f"block_signal compute failed: {e}"}

    evaluations_dict = [asdict(e) for e in evals]

    # 9. 落盘 history (每次跑都 append, 用于 watchlist_decay 的 "从未触发 BUY" 信号)
    _append_history(concept, score.total_score, score.tier, evaluations_dict)

    return {
        "concept": concept,
        "sector_score": asdict(score),
        "sector_signals": {
            "nav_5d": sig.nav_pct_5d, "nav_1m": sig.nav_pct_1m,
            "flow_5d_cny": active_flow_5d, "flow_20d_cny": active_flow_20d,
            "flow_source": active_flow_source,
            "pct_rank_60d": sig.pct_rank_60d, "pct_rank_250d": sig.pct_rank_250d,
        },
        "sector_benchmarks": {
            "roe_median": sector_roe_median,
            "gross_margin_median": sector_margin_median,
            "peer_pe_median": peer_pe_median,
            "min_deviation_effective": min_dev_effective,
        },
        "block_signal": block_signal,  # 短线 5d horizon, 不影响 verdict, 仅渲染
        "evaluations": evaluations_dict,
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
    src = ss.get("flow_source", "")
    src_note = f" [{src}]" if src else ""
    print(f"        flow_5d={ss['flow_5d_cny']/1e8:+.2f}亿  flow_20d={ss['flow_20d_cny']/1e8:+.2f}亿{src_note}")
    print(f"  板块 benchmark: ROE_med={sb['roe_median']}%  毛利_med={sb['gross_margin_median']}%  同业 PE_med={sb['peer_pe_median']}")
    print(f"  乖离阈值: {sb['min_deviation_effective']}% (flow 负板块自动抬高)")

    # 短线 z_block (P0-2 2026-05-25): concept 维度 5d horizon 大宗交易折溢价信号
    bsig = result.get("block_signal")
    if bsig and not bsig.get("error"):
        z = bsig.get("z_block_60d")
        if z is not None:
            if z >= 1.0:
                tone = "📈 短线偏多"
            elif z <= -1.0:
                tone = "📉 短线偏空"
            else:
                tone = "🟡 短线中性"
            print(f"  {tone} (5d): block_premium z={z:+.2f}σ  "
                  f"cur={bsig['cur_premium_pct']:+.2f}%  hist={bsig['hist_mean_pct']:+.2f}±{bsig['hist_std_pct']:.2f}  "
                  f"({bsig['n_trades']}笔/{bsig['n_stocks']}股/{bsig['total_amt_wan']:,.0f}万元)")
        else:
            print(f"  ⚪ 短线 z_block: 信号不足 (n_trades={bsig.get('n_trades',0)}, hist={bsig.get('n_hist_nonzero',0)}/60)")
    print()

    def fmt_row(e):
        s = e["stock"]
        verdict_icon = {
            "BUY": "🥇", "WATCH": "👀", "AVOID": "❌",
            "TREND_BUY": "🚀", "TREND_WATCH": "🔭",
        }.get(e["verdict"], "?")
        dev = e["deviation_pct"]
        dev_s = f"{dev:+.1f}%" if dev is not None else "  -"
        roe = s.get("roe")
        yoy = s.get("net_yoy")
        return (
            f"{verdict_icon} {s['code']:<10} {s['name'][:8]:<8} "
            f"close={s['close']:>7.2f}  PE={str(s.get('pe_ttm','--'))[:6]:>6}  "
            f"1d={s['pct_1d']:>6.1f}% 5d={s.get('pct_5d',0):>6.1f}%  "
            f"位置={s.get('pct_rank_120d',0):>3}%  "
            f"v1d={s.get('vol_ratio_1d',0):>4.2f} v5d={s.get('vol_ratio_5d',0):>4.2f}  "
            f"FV={str(e.get('fair_value','-'))[:7]:>7}  乖离={dev_s:>7}  "
            f"ROE={str(roe)[:5] if roe is not None else '-':>5}%  "
            f"净利YoY={str(yoy)[:6] if yoy is not None else '-':>6}%  "
            f"{e['rs_tier']:<8}"
        )

    print("  === 成分股评估 (按乖离降序) ===")
    for e in result["evaluations"]:
        s = e["stock"]
        print(f"  {fmt_row(e)}")
        # 量价信号 (新增 2026-05-20)
        if s.get("pv_alignment") and s["pv_alignment"] != "中性":
            print(f"     量价: {s['pv_alignment']}  vol_signal={s.get('volume_signal','?')}")
        # 复权警告 (rule 2 P0, 新增 2026-05-21) — 60 日内有除权事件需提醒人工核对
        if s.get("adj_events_60d", 0) > 0:
            print(f"     ⚠️ 60日内有 {s['adj_events_60d']} 次除权事件 (qfq_applied={s.get('qfq_applied')}); 数据已前复权但请人工确认时间线")
        elif s.get("qfq_applied"):
            print(f"     (qfq: 历史价已前复权, 60日内无新事件)")
        # Tier 0 trace (仅打印通过的 + AVOID/WATCH 中差临门一脚的)
        if e.get("tier0_pass"):
            print(f"     Tier 0 ✅ 趋势龙头通道触发: {' / '.join(e['tier0_reasons'])}")
        elif e["verdict"] in ("WATCH", "AVOID") and e.get("tier0_reasons"):
            failed = [r for r in e["tier0_reasons"] if "✗" in r]
            if failed and len(failed) <= 2:
                print(f"     Tier 0 差: {' / '.join(failed)}")
        print(f"     → {e['verdict']}: {e['reason']}")
    print()

    # 候选总结
    buys = [e for e in result["evaluations"] if e["verdict"] == "BUY"]
    trend_buys = [e for e in result["evaluations"] if e["verdict"] == "TREND_BUY"]
    watches = [e for e in result["evaluations"] if e["verdict"] == "WATCH"]
    trend_watches = [e for e in result["evaluations"] if e["verdict"] == "TREND_WATCH"]
    if trend_buys:
        print(f"  🚀 TREND_BUY ({len(trend_buys)}) [Tier 0 趋势龙头通道]:")
        for e in trend_buys:
            print(f"     {e['stock']['code']} {e['stock']['name']}  仓位 ≤ {e['tier4_position_size_max']}%  pos120={e['stock']['pct_rank_120d']}%  YoY+{e['stock']['net_yoy']:.0f}%")
    if buys:
        print(f"  🥇 BUY ({len(buys)}) [估值乖离通道]:")
        for e in buys:
            print(f"     {e['stock']['code']} {e['stock']['name']}  仓位 ≤ {e['tier4_position_size_max']}%  乖离 {e['deviation_pct']:+.1f}%")
    if trend_watches:
        print(f"  🔭 TREND_WATCH ({len(trend_watches)}):  " + ", ".join(f"{e['stock']['code']} {e['stock']['name']}" for e in trend_watches[:5]))
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
