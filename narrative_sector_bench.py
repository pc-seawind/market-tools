"""narrative_sector_bench.py — 板块对照组 benchmark 计算。

用途: 在 narrative_track.py 验证 event-ticker T+N 表现时，除了大盘 (CSI300) 对照外，
再加一层"同行业板块"对照，用于剔除 beta 噪声 — 区分 "narrative 真有 alpha" vs
"赶上行业整体涨/跌"。

设计:
  - 行业映射来自 tushare.stock_basic.industry (THS L1 行业名, e.g. "半导体"/"白酒")
  - 板块涨跌 = 同行业全部股票的日内收益均值 (等权)
  - 结果按 (industry, baseline_date, verify_date) 缓存到本地 json，避免重复算

API:
  - industry_for(ts_code) -> Optional[str]
  - compute_sector_perf(ts_code, baseline_date, verify_date) -> dict | None
      返回 {sector_name, sector_baseline_pct: 0.0, sector_current_pct: ..., sector_pct, n_members}
      其中 sector_pct = mean(close_verify / close_baseline - 1) 跨同行业全部成员

注: HK / US 股不计算板块对照 (tushare 行业分类只覆盖 A 股), 返回 None。
"""
from __future__ import annotations
import csv
import json
import subprocess
import statistics
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"
_CACHE_DIR = Path.home() / ".homespace" / "cache" / "market-tools" / "sector_bench"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _ts_csv(api: str, **params) -> list[dict[str, str]]:
    """thin wrapper, 镜像 narrative_track 内同名函数。"""
    args = ["python3", str(_TUSHARE), api]
    for k, v in params.items():
        args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


# ─── industry lookup ──────────────────────────────────────────────────────

_BASIC_CACHE: dict[str, dict[str, str]] = {}


def _load_stock_basic() -> dict[str, dict[str, str]]:
    """{ts_code: {name, industry}} from tushare stock_basic cache (~5000 行)."""
    global _BASIC_CACHE
    if _BASIC_CACHE:
        return _BASIC_CACHE
    rows = _ts_csv("stock_basic", list_status="L", fields="ts_code,name,industry")
    out = {}
    for r in rows:
        code = r.get("ts_code", "").strip()
        if code:
            out[code] = {"name": r.get("name", ""), "industry": r.get("industry", "")}
    _BASIC_CACHE = out
    return out


def industry_for(ts_code: str) -> Optional[str]:
    """ts_code → 行业名 (e.g. '半导体'). HK/US 返回 None."""
    code = (ts_code or "").upper()
    if not code or code.endswith(".HK") or "." not in code:
        return None
    basic = _load_stock_basic()
    rec = basic.get(code) or basic.get(ts_code)
    if not rec:
        return None
    ind = rec.get("industry", "").strip()
    return ind or None


def _members_of(industry: str) -> list[str]:
    """该行业的全部 ts_code。等权聚合用。"""
    basic = _load_stock_basic()
    return [code for code, b in basic.items() if b.get("industry") == industry]


# ─── price fetchers (sector aggregate) ────────────────────────────────────

_DAILY_SNAPSHOT_CACHE: dict[str, dict[str, float]] = {}


def _fetch_daily_snapshot(trade_date: str) -> dict[str, float]:
    """全市场 close 字典 {ts_code: close} on trade_date. 一次 API 调用搞定。

    ≤trade_date 最近交易日 fallback (周末/节假日)。
    """
    import datetime as dt
    if trade_date in _DAILY_SNAPSHOT_CACHE:
        return _DAILY_SNAPSHOT_CACHE[trade_date]

    rows = _ts_csv("daily", trade_date=trade_date)
    if not rows:
        # fallback: 找 ≤trade_date 最近交易日
        try:
            d = dt.datetime.strptime(trade_date, "%Y%m%d").date()
        except ValueError:
            return {}
        for back in range(1, 11):
            d2 = (d - dt.timedelta(days=back)).strftime("%Y%m%d")
            rows = _ts_csv("daily", trade_date=d2)
            if rows:
                break

    snap = {}
    for r in rows:
        code = r.get("ts_code", "").strip()
        try:
            snap[code] = float(r["close"])
        except (KeyError, ValueError):
            continue
    _DAILY_SNAPSHOT_CACHE[trade_date] = snap
    return snap


def _cache_path(industry: str, baseline_date: str, verify_date: str) -> Path:
    safe = industry.replace("/", "_").replace(" ", "_")
    return _CACHE_DIR / f"{safe}__{baseline_date}__{verify_date}.json"


def compute_sector_perf(ts_code: str, baseline_date: str,
                        verify_date: str,
                        max_members: int = 50) -> Optional[dict]:
    """计算该 ts_code 所属行业的等权平均涨跌 (baseline_date → verify_date).

    Returns:
        {
            "sector_name": "半导体",
            "sector_pct": +5.32,      # 百分比 (该行业全部成员的等权平均收益)
            "n_members": 47,           # 实际有数据的成员数
        }
        失败返回 None。

    缓存:
        每个 (industry, baseline_date, verify_date) 三元组缓存到本地 json，
        避免重复计算 (同一交易日多个 event 命中同行业时显著加速)。

    限制:
        max_members 默认 50 — 拉太多 ts code 太慢, 取行业内前 50 个 (按 ts_code 字典序,
        足够代表性)。可通过参数调大用于精确分析。
    """
    industry = industry_for(ts_code)
    if not industry:
        return None

    # 缓存命中？
    cp = _cache_path(industry, baseline_date, verify_date)
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass  # 损坏，重算

    members = _members_of(industry)
    if not members:
        return None
    if len(members) > max_members:
        members = sorted(members)[:max_members]

    # 一次拉两个日期的全市场 snapshot, 然后查行业成员
    snap_baseline = _fetch_daily_snapshot(baseline_date)
    snap_verify = _fetch_daily_snapshot(verify_date)

    returns: list[float] = []
    for code in members:
        cb = snap_baseline.get(code)
        cv = snap_verify.get(code)
        if cb and cv and cb > 0:
            returns.append((cv / cb - 1) * 100)

    if not returns:
        return None

    result = {
        "sector_name": industry,
        "sector_pct": round(statistics.mean(returns), 2),
        "n_members": len(returns),
    }
    try:
        cp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return result


if __name__ == "__main__":
    # 自检: 默认拉一只测试
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "002475.SZ"
    bd = sys.argv[2] if len(sys.argv) > 2 else "20260512"
    vd = sys.argv[3] if len(sys.argv) > 3 else "20260528"
    print(f"industry({code}) = {industry_for(code)}")
    print(json.dumps(compute_sector_perf(code, bd, vd), ensure_ascii=False, indent=2))
