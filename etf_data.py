"""etf_data.py — ETF 数据加载 + 板块级资金流/技术面计算.

架构:
    concept_etf_map.yaml  → load_map()   → concept → ETF 列表
    tushare fund_share    → fetch_share() → ETF 日级份额
    tushare fund_daily    → fetch_daily() → ETF 日级净值/成交
    → sector_flow(concept)   : 板块资金净流入 (CNY)
    → sector_tech(concept)   : 板块技术面 (位置/1W/1M/量比)
    → sector_signals(concept): 综合 {flow_1d, flow_5d, nav_1w, nav_1m, position, quality}

多 ETF 混合逻辑:
    对 primary_etfs 的每只 ETF 按 weight 加权.
    - sector_flow = Σ (weight_i × flow_i)
    - sector_nav_pct = Σ (weight_i × nav_pct_i)

Fallback (data_quality=fallback):
    无 ETF 映射 → 返回 None, 调用方 fallback 到 concepts_data.py 成分股均值.

CLI 测试:
    python3 etf_data.py --concept "存储芯片 (HBM/DDR/NAND)"
    python3 etf_data.py --all      # 14 个 concept 都跑一遍
"""

from __future__ import annotations

import csv
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
_MAP_PATH = _HERE / "concept_etf_map.yaml"
_TUSHARE_CLI = _HERE / "tushare.py"


# ─── 映射加载 ──────────────────────────────────────────────────────────────

def load_map() -> dict[str, Any]:
    """Load concept_etf_map.yaml; return full dict."""
    with _MAP_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if data.get("schema_version") != 1:
        raise ValueError(f"concept_etf_map.yaml schema_version != 1")
    return data


def list_concepts() -> list[str]:
    """All concept names in the map."""
    return list((load_map().get("concepts") or {}).keys())


def etfs_for(concept: str) -> tuple[str, list[dict[str, Any]]]:
    """Return (data_quality, [{code, name, weight}, ...]) for a concept.

    Returns ("fallback", []) if not mapped or data_quality=fallback.
    """
    m = load_map().get("concepts") or {}
    entry = m.get(concept)
    if not entry:
        return ("fallback", [])
    quality = entry.get("data_quality", "fallback")
    etfs = entry.get("primary_etfs") or []
    return (quality, etfs)


# ─── tushare 查询 ─────────────────────────────────────────────────────────

def _tushare_csv(api: str, **params) -> list[dict[str, str]]:
    """Invoke local tushare.py wrapper, return list of dicts."""
    args = ["python3", str(_TUSHARE_CLI), api]
    for k, v in params.items():
        if k == "fields":
            args.append(f"--fields={v}")
        else:
            args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def fetch_share(ts_code: str, *, days_back: int = 30) -> list[dict[str, Any]]:
    """Fetch daily fd_share for an ETF, most recent `days_back` rows.

    Returns list of {trade_date, fd_share} sorted ASCENDING by date.
    fd_share unit: 万份 (10,000 shares)
    """
    rows = _tushare_csv("fund_share", ts_code=ts_code, fields="ts_code,trade_date,fd_share")
    if not rows:
        return []
    # tushare returns latest-first; sort ascending + trim
    parsed = []
    for r in rows:
        try:
            parsed.append({"trade_date": r["trade_date"], "fd_share": float(r["fd_share"])})
        except (KeyError, ValueError):
            continue
    parsed.sort(key=lambda x: x["trade_date"])
    return parsed[-days_back:]


def fetch_daily(ts_code: str, *, days_back: int = 30) -> list[dict[str, Any]]:
    """Fetch daily OHLCV for an ETF.

    Returns list sorted ASC by date: [{trade_date, close, vol, amount}, ...]
    close: CNY (net asset value proxy)
    vol: 手 (100-share lots)
    amount: 千元
    """
    rows = _tushare_csv("fund_daily", ts_code=ts_code, fields="ts_code,trade_date,close,vol,amount")
    if not rows:
        return []
    parsed = []
    for r in rows:
        try:
            parsed.append({
                "trade_date": r["trade_date"],
                "close": float(r["close"]),
                "vol": float(r.get("vol") or 0),
                "amount": float(r.get("amount") or 0),
            })
        except (KeyError, ValueError):
            continue
    parsed.sort(key=lambda x: x["trade_date"])
    return parsed[-days_back:]


# ─── 单 ETF 指标计算 ───────────────────────────────────────────────────────

@dataclass
class EtfMetrics:
    ts_code: str
    name: str
    weight: float
    latest_date: str           # fund_daily 最新 trade_date
    share_date: str            # fund_share 最新 trade_date (可能比 latest_date 晚 1 天)
    latest_close: float
    nav_pct_1d: float          # 今日净值涨跌 %
    nav_pct_5d: float
    nav_pct_1m: float          # 20 交易日
    # 位置 v1 (min-max, 保留向后兼容)
    position_pct: int          # 120 日 min-max 位置 0-100
    # 位置 v2 (percentile rank × 3 窗口, 2026-05-13 升级, framework v2.3.1)
    pct_rank_60d: int          # 当前 close 在过去 60 日 closes 分布里的百分位
    pct_rank_120d: int
    pct_rank_250d: int
    vol_ratio: float           # 近 5 日均量 vs 20 日均量
    flow_1d_cny: float         # 今日资金净流入 (元)
    flow_5d_cny: float         # 5 日累计净流入
    flow_20d_cny: float        # 20 日累计净流入
    flow_data_days: int        # flow 实际基于几天 share 数据算的 (诊断用)
    data_ok: bool


def _percentile_of(window: list, val: float) -> int:
    """Percentile rank of val within window (0-100).
    定义: (count(x < val) + 0.5 × count(x == val)) / len(window)
    比 min-max 缩放鲁棒 — 不受单个异常峰值影响, 反映真实密度.
    """
    if not window:
        return 50
    lower = sum(1 for x in window if x < val)
    equal = sum(1 for x in window if x == val)
    return int(round((lower + 0.5 * equal) / len(window) * 100))


def compute_etf_metrics(ts_code: str, name: str = "", weight: float = 1.0) -> EtfMetrics:
    """Compute all per-ETF metrics used by sector scoring."""
    daily = fetch_daily(ts_code, days_back=260)  # 250 日位置 + buffer
    shares = fetch_share(ts_code, days_back=30)

    if not daily or len(daily) < 20:
        return EtfMetrics(ts_code, name, weight, "", "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, data_ok=False)

    last = daily[-1]
    closes = [d["close"] for d in daily]
    last_close = closes[-1]

    # 位置 v1 (min-max 120d, 向后兼容)
    window_120 = closes[-120:] if len(closes) >= 120 else closes
    hi, lo = max(window_120), min(window_120)
    position_pct = int(round((last_close - lo) / (hi - lo) * 100)) if hi > lo else 50

    # 位置 v2 (percentile rank × 3 窗口)
    pct_60 = _percentile_of(closes[-60:] if len(closes) >= 60 else closes, last_close)
    pct_120 = _percentile_of(window_120, last_close)
    pct_250 = _percentile_of(closes[-250:] if len(closes) >= 250 else closes, last_close)

    # 净值涨跌
    def pct(n: int) -> float:
        if len(closes) <= n:
            return 0.0
        base = closes[-n - 1]
        return (last_close / base - 1.0) * 100 if base > 0 else 0.0

    nav_1d = pct(1)
    nav_5d = pct(5)
    nav_1m = pct(20)

    # 量比
    vols = [d["vol"] for d in daily]
    vol_5d = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    vol_20d = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0
    vol_ratio = vol_5d / vol_20d if vol_20d > 0 else 0

    # 资金流入 (CNY) — FIXED 2026-05-13: fund_share 比 fund_daily 常晚 1 天, 不强求日期匹配
    # 改为: 按 share 的可用日期循环, daily close 找得到就算, 找不到跳过
    flow_1d = 0.0
    flow_5d = 0.0
    flow_20d = 0.0
    share_date = ""
    flow_data_days = 0

    if shares and len(shares) >= 2:
        share_date = shares[-1]["trade_date"]
        daily_close_by_date = {d["trade_date"]: d["close"] for d in daily}

        def flow_window(n: int) -> tuple[float, int]:
            """累计 n 个交易日份额变化的 CNY 净流入. Return (cny, effective_days)."""
            if len(shares) < n + 1:
                # 用可用数据尽量算
                effective = len(shares) - 1
            else:
                effective = n
            if effective < 1:
                return 0.0, 0
            total = 0.0
            real_days = 0
            for i in range(len(shares) - effective, len(shares)):
                date_i = shares[i]["trade_date"]
                # 用该日的 close; 若该日无 daily, 用最近一天的 close 作近似
                close_i = daily_close_by_date.get(date_i)
                if close_i is None:
                    # fallback: 用 last close
                    close_i = last_close
                delta_shares = (shares[i]["fd_share"] - shares[i - 1]["fd_share"]) * 10000
                total += delta_shares * close_i
                real_days += 1
            return total, real_days

        flow_1d, _ = flow_window(1)
        flow_5d, _ = flow_window(5)
        flow_20d, flow_data_days = flow_window(20)

    return EtfMetrics(
        ts_code=ts_code, name=name, weight=weight,
        latest_date=last["trade_date"],
        share_date=share_date,
        latest_close=last_close,
        nav_pct_1d=round(nav_1d, 2),
        nav_pct_5d=round(nav_5d, 2),
        nav_pct_1m=round(nav_1m, 2),
        position_pct=position_pct,
        pct_rank_60d=pct_60,
        pct_rank_120d=pct_120,
        pct_rank_250d=pct_250,
        vol_ratio=round(vol_ratio, 2),
        flow_1d_cny=round(flow_1d, 0),
        flow_5d_cny=round(flow_5d, 0),
        flow_20d_cny=round(flow_20d, 0),
        flow_data_days=flow_data_days,
        data_ok=True,
    )


# ─── 板块聚合 ──────────────────────────────────────────────────────────────

@dataclass
class SectorSignals:
    concept: str
    data_quality: str                         # direct | proxy | fallback
    nav_pct_1d: float
    nav_pct_5d: float
    nav_pct_1m: float
    position_pct: int                         # v1 兼容 (120d min-max)
    pct_rank_60d: int                         # v2 (percentile rank 短期)
    pct_rank_120d: int
    pct_rank_250d: int
    vol_ratio: float
    flow_1d_cny: float
    flow_5d_cny: float
    flow_20d_cny: float
    etfs: list[dict[str, Any]]                # per-ETF breakdown


def sector_signals(concept: str) -> SectorSignals | None:
    """Aggregate per-ETF metrics into sector-level signals.

    Returns None if data_quality == 'fallback' (no ETF mapped).
    """
    quality, etfs = etfs_for(concept)
    if quality == "fallback" or not etfs:
        return None

    per_etf_metrics = []
    for e in etfs:
        m = compute_etf_metrics(e["code"], e.get("name", ""), e.get("weight", 1.0))
        if m.data_ok:
            per_etf_metrics.append(m)

    if not per_etf_metrics:
        return None

    # Normalize weights
    total_weight = sum(m.weight for m in per_etf_metrics)
    if total_weight == 0:
        return None

    def wavg(field: str) -> float:
        return sum(getattr(m, field) * m.weight for m in per_etf_metrics) / total_weight

    def wsum(field: str) -> float:
        # Flows are money amounts, weighted sum (not avg) is more meaningful — but
        # since weights express representative share of a composite sector, wavg is cleaner.
        # Use wavg for flows too, so flow magnitude matches "representative ETF" size.
        return sum(getattr(m, field) * m.weight for m in per_etf_metrics) / total_weight

    return SectorSignals(
        concept=concept,
        data_quality=quality,
        nav_pct_1d=round(wavg("nav_pct_1d"), 2),
        nav_pct_5d=round(wavg("nav_pct_5d"), 2),
        nav_pct_1m=round(wavg("nav_pct_1m"), 2),
        position_pct=int(round(wavg("position_pct"))),
        pct_rank_60d=int(round(wavg("pct_rank_60d"))),
        pct_rank_120d=int(round(wavg("pct_rank_120d"))),
        pct_rank_250d=int(round(wavg("pct_rank_250d"))),
        vol_ratio=round(wavg("vol_ratio"), 2),
        flow_1d_cny=round(wsum("flow_1d_cny"), 0),
        flow_5d_cny=round(wsum("flow_5d_cny"), 0),
        flow_20d_cny=round(wsum("flow_20d_cny"), 0),
        etfs=[asdict(m) for m in per_etf_metrics],
    )


# ─── CLI ──────────────────────────────────────────────────────────────────

def _fmt_cny(x: float) -> str:
    """Format CNY with 亿/万 suffix."""
    sign = "+" if x >= 0 else ""
    abx = abs(x)
    if abx >= 1e8:
        return f"{sign}{x/1e8:.2f}亿"
    if abx >= 1e4:
        return f"{sign}{x/1e4:.1f}万"
    return f"{sign}{x:.0f}"


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="ETF sector-level metrics.")
    gp = ap.add_mutually_exclusive_group(required=True)
    gp.add_argument("--concept", help="concept name (see concept_etf_map.yaml)")
    gp.add_argument("--all", action="store_true", help="run all concepts in the map")
    gp.add_argument("--list", action="store_true", help="list all concepts")
    args = ap.parse_args()

    if args.list:
        for c in list_concepts():
            q, es = etfs_for(c)
            print(f"  [{q}]  {c}  ({len(es)} etfs)")
        return

    concepts = [args.concept] if args.concept else list_concepts()
    for c in concepts:
        sig = sector_signals(c)
        print(f"\n━━ {c} ━━")
        if not sig:
            q, _ = etfs_for(c)
            print(f"  data_quality={q}, no ETF data (fallback)")
            continue
        print(f"  quality={sig.data_quality}  ETFs={len(sig.etfs)}")
        print(f"  nav: 1d={sig.nav_pct_1d:+.2f}%  5d={sig.nav_pct_5d:+.2f}%  1m={sig.nav_pct_1m:+.2f}%  量比={sig.vol_ratio:.2f}x")
        print(f"  位置: v1(min-max 120d)={sig.position_pct}%   v2 pct_rank: 60d={sig.pct_rank_60d}%  120d={sig.pct_rank_120d}%  250d={sig.pct_rank_250d}%")
        print(f"  flow: 1d={_fmt_cny(sig.flow_1d_cny)}  5d={_fmt_cny(sig.flow_5d_cny)}  20d={_fmt_cny(sig.flow_20d_cny)}")
        for m in sig.etfs:
            share_note = f" [share_d={m.get('share_date','?')}, flow_days={m.get('flow_data_days',0)}]" if m.get('share_date') != m.get('latest_date') else ""
            print(f"    · {m['ts_code']} {m['name']} (w={m['weight']:.2f})  nav5d={m['nav_pct_5d']:+.2f}%  flow5d={_fmt_cny(m['flow_5d_cny'])}{share_note}")


if __name__ == "__main__":
    _cli()
