"""news_score_phase2_unlock.py — Phase 2 候选 A: 解禁压力 (Unlock Pressure).

❌❌ REJECTED 2026-05-25 — 未上线 news_score, 代码保留备查 ❌❌
  原因: 11 BK-mapped concept 上 fwd_20d IC=+0.174 (符号反!), 高压组 fwd_20d 反而
        +14.45% (vs 其他 +6.29%, gap +8.16pp UPSIDE), 月度反指失效率 50%,
        增量 IC vs fund -5.0%.
  根因: 11 个 concept 高度集中于 AI/算力 bull-run leader, sector beta 淹没解禁信号.
  后续: 不接 sector_score; 若未来扩 BK universe (11→30+) 可重测.
  详见: docs/news_score_phase2_design.md (Phase 2 A REJECTED 章节)

设计文档: docs/news_score_phase2_design.md

信号定义:
  unlock_30d_ratio(concept, t) = Σ_{stock in concept} clip(future_30d_float_ratio, 0, 50%)
  z_unlock_60d = (cur - mean_60d) / std_60d  (per concept rolling)
  news_pts = clip(-z_unlock, -2, +2) * 1.0  (反指 → 取负, swing ±1.0)

数据源: tushare share_float
  字段: ts_code, ann_date, float_date, float_share, float_ratio, holder_name, share_type
  注意: 一笔解禁可能多 holder 行 → 同一 (ts_code, float_date) 累加 float_ratio
  cap: 单股贡献上限 50% (防止寒武纪式单股放大)

cache 策略:
  按 ts_code 拉每只票的全量历史 (一只股一次 API 调用), 缓存到
  .cache/news_share_float_by_ts.jsonl. 历史不变, 永久 cache.
  也保留旧的 by-float_date cache (.cache/news_share_float.jsonl) 用于兼容.
"""
from __future__ import annotations

import csv
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"
_CACHE_DIR = _HERE / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)
_CACHE_FILE = _CACHE_DIR / "news_share_float.jsonl"           # legacy by-float_date
_CACHE_BY_TS = _CACHE_DIR / "news_share_float_by_ts.jsonl"    # by ts_code

_SINGLE_STOCK_CAP = 50.0  # % — 单股 30d 累计 float_ratio 上限


# ─── Tushare fetch ─────────────────────────────────────────────────────────

def _ts_csv(api: str, **params) -> list[dict[str, str]]:
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


def _yyyymmdd(d: datetime) -> str:
    return d.strftime("%Y%m%d")


def fetch_share_float_by_ts(ts_codes: list[str], verbose: bool = True,
                            ttl_days: int = 14) -> list[dict]:
    """按 ts_code 拉全量 share_float 历史. 增量补缺.

    每只票一次 API 调用; 缓存到 _CACHE_BY_TS, 用 _cached_at 字段做 TTL.
    历史数据基本不变, 但定增有时事后修正, ttl_days=14 让旧 cache 偶尔刷新.
    """
    today = datetime.now().strftime("%Y%m%d")
    cached_by_ts: dict[str, str] = {}  # ts -> latest _cached_at
    if _CACHE_BY_TS.exists():
        with open(_CACHE_BY_TS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts = r.get("ts_code")
                    ca = r.get("_cached_at", "")
                    if ts and ca > cached_by_ts.get(ts, ""):
                        cached_by_ts[ts] = ca
                except Exception:
                    pass

    fresh_threshold_dt = datetime.now() - timedelta(days=ttl_days)
    fresh_threshold = fresh_threshold_dt.strftime("%Y%m%d")

    to_fetch = []
    for ts in ts_codes:
        ca = cached_by_ts.get(ts, "")
        if ca < fresh_threshold:
            to_fetch.append(ts)

    if verbose and to_fetch:
        print(f"  📥 share_float_by_ts: fetching {len(to_fetch)}/{len(ts_codes)} stocks "
              f"(others fresh, ttl={ttl_days}d)", file=sys.stderr)

    new_rows: list[dict] = []
    for i, ts in enumerate(to_fetch, 1):
        rows = _ts_csv("share_float", ts_code=ts)
        if not rows:
            # cache empty marker
            new_rows.append({"ts_code": ts, "_cached_at": today, "_empty": True})
        else:
            for r in rows:
                r["_cached_at"] = today
                new_rows.append(r)
        if verbose and i % 50 == 0:
            print(f"    fetched {i}/{len(to_fetch)} stocks", file=sys.stderr)

    if new_rows:
        # If we re-fetched a ts, drop its old rows and append new ones
        refetched_ts = {ts for ts in to_fetch}
        if _CACHE_BY_TS.exists() and refetched_ts:
            kept: list[dict] = []
            with open(_CACHE_BY_TS) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                        if r.get("ts_code") not in refetched_ts:
                            kept.append(r)
                    except Exception:
                        pass
            with open(_CACHE_BY_TS, "w") as f:
                for r in kept:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

        with open(_CACHE_BY_TS, "a") as f:
            for r in new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if verbose:
            print(f"  💾 share_float_by_ts: cached +{len(new_rows)} rows "
                  f"({len(to_fetch)} stocks)", file=sys.stderr)

    # Read all + filter requested
    out: list[dict] = []
    want = set(ts_codes)
    if _CACHE_BY_TS.exists():
        with open(_CACHE_BY_TS) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("_empty"):
                        continue
                    if r.get("ts_code") in want:
                        out.append(r)
                except Exception:
                    pass
    return out


def fetch_share_float_history(start_date: str, end_date: str,
                              verbose: bool = True) -> list[dict]:
    """按 float_date 增量拉 share_float, cache 到 _CACHE_FILE.

    Args:
        start_date, end_date: float_date 范围 (YYYYMMDD)
        verbose: 打印进度

    cache 行格式:
        - 真实数据行: 原始 tushare 字段 + {"_cached_for": float_date}
        - 空标记行: {"_cached_for": float_date, "_empty": true}
    """
    cached_dates = set()
    if _CACHE_FILE.exists():
        with open(_CACHE_FILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    cd = r.get("_cached_for")
                    if cd:
                        cached_dates.add(cd)
                except Exception:
                    pass

    cur = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")
    new_rows: list[dict] = []
    new_dates: list[str] = []

    while cur <= end:
        d = _yyyymmdd(cur)
        cur += timedelta(days=1)
        # 周末跳过 (解禁 float_date 一般是交易日, 但偶尔有非交易日 — API 返回空即可)
        # 不强制跳, 让 API 决定
        if d in cached_dates:
            continue
        rows = _ts_csv("share_float", float_date=d)
        if rows:
            for r in rows:
                r["_cached_for"] = d
            new_rows.extend(rows)
        else:
            # 空日子也 cache 一个 stub
            new_rows.append({"_cached_for": d, "_empty": True})
        new_dates.append(d)
        if verbose and len(new_dates) % 50 == 0:
            print(f"  📥 share_float fetched {len(new_dates)} days, "
                  f"latest={d}, +{len(new_rows)} rows so far", file=sys.stderr)

    if new_rows:
        with open(_CACHE_FILE, "a") as f:
            for r in new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        if verbose:
            print(f"  💾 share_float: cached +{len(new_rows)} rows over "
                  f"{len(new_dates)} days", file=sys.stderr)

    # 全读 + filter
    out: list[dict] = []
    if _CACHE_FILE.exists():
        with open(_CACHE_FILE) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("_empty"):
                        continue
                    fd = r.get("float_date") or r.get("_cached_for")
                    if fd and start_date <= fd <= end_date:
                        out.append(r)
                except Exception:
                    pass
    return out


# ─── 信号计算 ───────────────────────────────────────────────────────────────

def _to_f(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _index_by_stock_date(rows: list[dict]) -> dict[tuple[str, str], float]:
    """聚合到 (ts_code, float_date) → 累计 float_ratio (跨 holder 求和).

    注意: rows 是原始 tushare 数据, 一笔解禁多 holder → 同 (ts_code, float_date)
    多行求和.
    """
    by_sd: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        ts = r.get("ts_code")
        fd = r.get("float_date")
        if not ts or not fd:
            continue
        ratio = _to_f(r.get("float_ratio"))
        by_sd[(ts, fd)] += ratio
    return dict(by_sd)


def compute_concept_unlock_30d(
    members: list[tuple[str, str]],
    as_of: str,
    by_sd: dict[tuple[str, str], float],
) -> tuple[float, dict]:
    """concept 在 as_of 这天向后看 30 个日历日的 unlock_30d_ratio.

    Args:
        members: [(ts_code, name), ...]
        as_of: YYYYMMDD
        by_sd: (ts_code, float_date) -> cumulative float_ratio

    Returns:
        (unlock_30d_ratio, detail_dict)
    """
    member_codes = {ts for ts, _ in members}
    as_dt = datetime.strptime(as_of, "%Y%m%d")
    end_dt = as_dt + timedelta(days=30)

    # 按 stock 累计在 (as_of, as_of+30d] 的 ratio
    per_stock: dict[str, float] = defaultdict(float)
    n_events = 0
    for (ts, fd), r in by_sd.items():
        if ts not in member_codes:
            continue
        try:
            fdt = datetime.strptime(fd, "%Y%m%d")
        except Exception:
            continue
        if as_dt < fdt <= end_dt:
            per_stock[ts] += r
            n_events += 1

    # cap 单股
    capped: dict[str, float] = {}
    n_capped = 0
    for ts, v in per_stock.items():
        cv = min(v, _SINGLE_STOCK_CAP)
        if cv < v:
            n_capped += 1
        capped[ts] = cv

    total = sum(capped.values())
    return total, {
        "n_events": n_events,
        "n_stocks_with_unlock": len(per_stock),
        "n_capped": n_capped,
        "per_stock_capped": dict(sorted(capped.items(), key=lambda x: -x[1])[:5]),
    }


def z_unlock_60d(
    concept_members: list[tuple[str, str]],
    as_of: str,
    by_sd: dict[tuple[str, str], float],
    hist_days: int = 60,
) -> tuple[float | None, dict]:
    """对 concept 在过去 hist_days 个日历日上各自的 unlock_30d_ratio 算 z-score.

    Returns:
        (z, diag)
        z is None if hist 样本 < 20.
    """
    as_dt = datetime.strptime(as_of, "%Y%m%d")

    # 算每个 t in [as_of - hist_days, as_of] 的 unlock_30d_ratio
    series: list[float] = []
    for back in range(hist_days, 0, -1):
        t_dt = as_dt - timedelta(days=back)
        t = _yyyymmdd(t_dt)
        v, _ = compute_concept_unlock_30d(concept_members, t, by_sd)
        series.append(v)

    cur, cur_detail = compute_concept_unlock_30d(concept_members, as_of, by_sd)

    if len(series) < 20:
        return None, {"reason": "insufficient_history", "n_hist": len(series),
                      "cur": cur, **cur_detail}

    mu = statistics.mean(series)
    sd = statistics.stdev(series) if len(series) > 1 else 0.0
    if sd == 0:
        # 历史全 0 但当前有值 → 给一个固定大 z; 否则 z=0
        z = 0.0 if cur == 0 else 3.0
    else:
        z = (cur - mu) / sd

    return z, {
        "cur": cur,
        "hist_mean": round(mu, 4),
        "hist_std": round(sd, 4),
        "n_hist": len(series),
        "n_hist_nonzero": sum(1 for v in series if v > 0),
        "z": round(z, 3),
        **cur_detail,
    }


# ─── 主入口 ────────────────────────────────────────────────────────────────

def news_score_phase2_unlock(
    concept: str,
    as_of: str | None = None,
    preloaded_rows: list[dict] | None = None,
) -> tuple[float, str, dict]:
    """Compute Phase 2 unlock signal for a concept.

    Returns (pts: float in [-2, +2], note: str, diag: dict)

    pts 是相对 base 的 swing, 不是绝对分. 调用方 (news_score) 自己加 base.
    pts > 0 → 解禁压力低, 利好; pts < 0 → 高压, 利空.
    """
    from concepts_data import stocks_of

    as_of = as_of or datetime.now().strftime("%Y%m%d")
    members = stocks_of(concept) or []
    if not members:
        return 0.0, "(no members)", {"error": "no members", "concept": concept}

    if preloaded_rows is None:
        # 默认走 by-ts cache (覆盖整个历史, 一次性)
        member_codes = [ts for ts, _ in members]
        preloaded_rows = fetch_share_float_by_ts(member_codes, verbose=False)

    by_sd = _index_by_stock_date(preloaded_rows)
    z, diag = z_unlock_60d(members, as_of, by_sd)

    if z is None:
        return 0.0, f"insufficient hist (n_hist={diag.get('n_hist',0)})", {
            "concept": concept, "as_of": as_of, "z_unlock": None,
            "n_members": len(members), **diag,
        }

    z_clip = max(-2.0, min(2.0, z))
    pts = -z_clip * 1.0  # 反指, swing ±1.0  (写为 -z 让 +z=高压 → 负分)

    note = (f"unlock_30d_ratio cur={diag['cur']:.2f}% "
            f"(mean={diag['hist_mean']:.2f}, std={diag['hist_std']:.2f}) "
            f"→ z={z:+.2f}σ → pts={pts:+.2f}")

    return round(pts, 3), note, {
        "concept": concept, "as_of": as_of, "z_unlock": round(z, 3),
        "pts": round(pts, 3), "n_members": len(members),
        "version": "phase2_unlock_v1", **diag,
    }


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--concept", default=None,
                   help="计算某个 concept 的 phase2 unlock 分; 不传只 prefetch")
    p.add_argument("--as-of", default=None)
    p.add_argument("--prefetch-all-concepts", action="store_true",
                   help="拉所有 concepts 成员的 share_float (推荐: 全量历史一次拉好)")
    p.add_argument("--prefetch-only", action="store_true")
    p.add_argument("--ttl-days", type=int, default=14)
    args = p.parse_args()

    as_of = args.as_of or datetime.now().strftime("%Y%m%d")

    if args.prefetch_all_concepts:
        from concepts_data import CONCEPTS
        all_ts = sorted({ts for ms in CONCEPTS.values() for ts, _ in ms})
        print(f"📥 prefetch share_float for {len(all_ts)} concept members",
              file=sys.stderr)
        rows = fetch_share_float_by_ts(all_ts, ttl_days=args.ttl_days)
        print(f"  loaded {len(rows)} share_float rows from {len(all_ts)} stocks",
              file=sys.stderr)

    if args.prefetch_only:
        return

    if args.concept:
        pts, note, diag = news_score_phase2_unlock(args.concept, as_of)
        print(f"pts: {pts:+.2f} (swing ±1.0)")
        print(f"note: {note}")
        print(json.dumps(diag, ensure_ascii=False, indent=2, default=str))
    else:
        # 跑全 concepts 摘要
        from concepts_data import CONCEPTS
        print(f"\n📊 全 concepts unlock 信号 @{as_of}:", file=sys.stderr)
        rows_per_c: list[tuple[str, float | None, float, int]] = []
        for c in CONCEPTS:
            pts, _, diag = news_score_phase2_unlock(c, as_of)
            z = diag.get("z_unlock")
            cur = diag.get("cur", 0)
            n_ev = diag.get("n_events", 0)
            rows_per_c.append((c, z, cur, n_ev))
        # sort by abs(z) desc
        rows_per_c.sort(key=lambda x: (abs(x[1]) if x[1] is not None else -1), reverse=True)
        print(f"  {'concept':<35} {'z_unlock':>10} {'unlock30d%':>10} {'n_ev':>5}")
        for c, z, cur, n_ev in rows_per_c[:15]:
            zs = f"{z:+.2f}σ" if z is not None else "  N/A"
            print(f"  {c:<35} {zs:>10} {cur:>10.2f} {n_ev:>5}")


if __name__ == "__main__":
    main()
