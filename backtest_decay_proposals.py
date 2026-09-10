#!/usr/bin/env python3
"""backtest_decay_proposals.py — 观察池 decay 提案的前瞻收益回测.

动机 (2026-09-10 用户指令):
  "12 个 watchlist decay 提案堆积未审批 ... 需要你定一个吸收策略,
   然后回测 OK 就接入, 不 OK 就放弃"

被审计对象: watchlist_decay.py 的移除提案 (watchlist/proposed/*-decay.yaml).
  每周日 09:00 cron 生成, 12 周 (2026-06-14 → 2026-09-07) 全部未审批 →
  提案流实际上是死信队列.

核心问题 (不是"提案写得好不好", 而是):
  **被判定 stale (score >= 5) 的观察池成员, 在之后 5/20/40 个交易日的
    收益, 是否真的比同池子保留成员更差?**
  如果否 —— 这套机制在做"卖掉已经跌透的票", 与 REVERSAL 通道
  (跌透的票 20 日超额 +2.96%) 直接冲突, 应当退役.

实验设计 (点内样本, 无前瞻污染):
  - 12 个提案日期 (周日) 为决策点, 入场 = 提案日之后第一个交易日收盘.
  - treated = 当周 proposals[] (score >= 5)
    near    = 当周 near_stale_monitor[] (score 3-4)
    fresh   = 当周已过 age_floor(30d) 的池成员, 且不在上两组 (score <= 2 隐含)
    三组同池同周, 差别只在 decay score → 天然控制市场 beta 与池子选择偏差.
  - 收益: close[t+h]/close[t]-1, h ∈ {5,20,40} 交易日
  - 超额: 减 CSI300 同窗口收益 (primary, 与 decay 自己的 underperform_csi300
    信号同基准) + 减同池等权 (secondary, 剥离 beta 的纯横截面差)
  - 诊断: 入场前 60/120 日收益 + 距 120 日高点回撤 —— 检验"卖在跌透处"假设.
  - 统计: 逐周 spread (treated 等权 - kept 等权) 的 t 检验 + 按 score 分桶
    单调性 + 周内去均值的 Spearman(score, fwd_excess).

已知局限 (报告必须写明):
  - 12 周 × 32 只池子, 样本小; 同一只股在多周重复出现 (未做独立性校正),
    同时给出"逐股先聚合再统计"的稳健口径.
  - fresh 组是"未被列出"推断的, 不是脚本直出的 rating.
  - 池成员是 2026-05 一次性加入的 (人工智能/半导体/光模块主题集中),
    结论外推到其他主题池子需谨慎.
  - 未做多重假设检验校正 (4 组 threshold × 3 窗口).

Usage:
  python3 backtest_decay_proposals.py [--out /tmp/decay_backtest.json]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from backtest_leftside_reversal import _ts, fetch_stock  # noqa: E402

PROPOSED_DIR = _ROOT / "watchlist" / "proposed"
WATCHLIST = _ROOT / "watchlist.yaml"
HORIZONS = (5, 20, 40)
POOL_TIER = "观察池"
AGE_FLOOR_DAYS = 30
THRESHOLD = 5


# ─── 数据装载 ────────────────────────────────────────────────────────────

def load_pool() -> list[dict]:
    wl = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8"))
    out = []
    for e in wl.get("entries") or []:
        if e.get("tier") != POOL_TIER:
            continue
        out.append({
            "code": e["code"],
            "name": e.get("name") or e["code"],
            "added_at": str(e.get("added_at") or "")[:10],
            "themes": e.get("themes") or [],
        })
    return out


def load_weeks() -> list[dict]:
    weeks = []
    for f in sorted(PROPOSED_DIR.glob("*-decay.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        date = f.name[:10]
        treated, near = [], []
        for p in d.get("proposals") or []:
            if p.get("op") != "remove":
                continue
            ev = p.get("evidence") or {}
            treated.append({
                "code": p["code"], "name": p.get("name"),
                "score": ev.get("score"),
                "signals": ev.get("signals_fired") or [],
            })
        for n in d.get("near_stale_monitor") or []:
            near.append({
                "code": n["code"], "name": n.get("name"),
                "score": n.get("score"), "signals": n.get("signals_fired") or [],
            })
        meta = d.get("decay_meta") or {}
        weeks.append({
            "date": date,
            "src": f.name,
            "threshold": meta.get("threshold", THRESHOLD),
            "total_evaluated": meta.get("total_evaluated"),
            "treated": treated,
            "near": near,
        })
    return weeks


def _days_held(added_at: str, as_of: str) -> int | None:
    try:
        a = datetime.strptime(added_at[:10], "%Y-%m-%d")
        b = datetime.strptime(as_of, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return (b - a).days


def assign_groups(weeks: list[dict], pool: list[dict]) -> None:
    """给每周补上 fresh 组 (已过 age_floor 的池成员, 不在 treated/near)."""
    for w in weeks:
        listed = {x["code"] for x in w["treated"]} | {x["code"] for x in w["near"]}
        fresh = []
        for e in pool:
            dh = _days_held(e["added_at"], w["date"])
            if dh is None or dh < AGE_FLOOR_DAYS:
                continue
            if e["code"] in listed:
                continue
            fresh.append({"code": e["code"], "name": e["name"], "score": None,
                          "signals": [], "implied": True})
        w["fresh"] = fresh
        w["eligible"] = len(listed) + len(fresh)


# ─── 行情 ────────────────────────────────────────────────────────────────

def load_prices(codes: list[str], start: str) -> dict[str, dict]:
    series = {}
    for i, c in enumerate(sorted(codes), 1):
        r = fetch_stock(c, start)
        if not r:
            print(f"  [warn] 无行情: {c}", file=sys.stderr)
            continue
        dates, closes, _ = r
        series[c] = {"dates": dates, "closes": closes,
                     "idx": {d: i for i, d in enumerate(dates)}}
        if dates[-1] < "20260901":
            print(f"  [warn] {c} sequence ends {dates[-1]} (cache stale?)", file=sys.stderr)
        if i % 10 == 0:
            print(f"  ... {i}/{len(codes)}", file=sys.stderr)
    return series


def load_index(code: str, start: str) -> dict:
    rows = _ts("index_daily", ts_code=code, start_date=start,
               fields="trade_date,close")
    rows = [r for r in rows if r.get("trade_date")]
    rows.sort(key=lambda r: r["trade_date"])
    return {"dates": [r["trade_date"] for r in rows],
            "closes": [float(r["close"]) for r in rows]}


def _ret_at(s: dict, d0: str, h: int, *, forward: bool) -> float | None:
    """close[t±h]/close[t]-1 (以 d0 为锚, forward or backward). s = 单个序列."""
    if not s:
        return None
    i = s["idx"].get(d0)
    if i is None:
        # d0 非交易日 (理论上入场日已对齐交易日) → 取之后第一个
        nxt = [j for j, d in enumerate(s["dates"]) if d > d0]
        if not nxt:
            return None
        i = nxt[0]
    j = i + h if forward else i - h
    if j < 0 or j >= len(s["dates"]):
        return None
    c0, c1 = s["closes"][i], s["closes"][j]
    if c0 <= 0:
        return None
    return (c1 / c0 - 1) * 100


def _ret(series: dict, code: str, d0: str, h: int, *, forward: bool) -> float | None:
    return _ret_at(series.get(code), d0, h, forward=forward)


def _next_trading_day(series: dict, date: str, pool_codes: list[str]) -> str | None:
    cands = []
    for c in pool_codes:
        s = series.get(c)
        if not s:
            continue
        for d in s["dates"]:
            if d > date:
                cands.append(d)
                break
    return min(cands) if cands else None


def _pos120(series: dict, code: str, d0: str) -> float | None:
    s = series.get(code)
    if not s:
        return None
    i = s["idx"].get(d0)
    if i is None or i < 120:
        return None
    win = s["closes"][i - 119:i + 1]
    lo, hi = min(win), max(win)
    if hi <= lo:
        return None
    return (s["closes"][i] - lo) / (hi - lo) * 100


def _dd120(series: dict, code: str, d0: str) -> float | None:
    s = series.get(code)
    if not s:
        return None
    i = s["idx"].get(d0)
    if i is None or i < 120:
        return None
    hi = max(s["closes"][i - 119:i + 1])
    if hi <= 0:
        return None
    return (s["closes"][i] / hi - 1) * 100


# ─── 统计工具 ────────────────────────────────────────────────────────────

def _stat(vals: list[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 2),
        "median": round(statistics.median(vals), 2),
        "sd": round(statistics.stdev(vals), 2) if len(vals) > 1 else 0.0,
        "pos_pct": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
    }


def _spearman(pairs: list[tuple[float, float]]) -> float | None:
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if len(pairs) < 8:
        return None

    def rank(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank([a for a, _ in pairs]), rank([b for _, b in pairs])
    n = len(pairs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((v - mx) ** 2 for v in rx) ** 0.5
    dy = sum((v - my) ** 2 for v in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return round(num / (dx * dy), 3)


def _ttest(vals: list[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if len(vals) < 3:
        return {"n": len(vals)}
    m = statistics.mean(vals)
    sd = statistics.stdev(vals)
    se = sd / len(vals) ** 0.5
    t = m / se if se > 0 else 0.0
    return {"n": len(vals), "mean": round(m, 2), "sd": round(sd, 2),
            "t": round(t, 2), "pos_weeks": sum(1 for v in vals if v > 0)}


# ─── 主流程 ──────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/decay_backtest.json")
    ap.add_argument("--start", default="20250101")
    ap.add_argument("--index", default="000300.SH")
    args = ap.parse_args()

    pool = load_pool()
    weeks = load_weeks()
    assign_groups(weeks, pool)
    codes = [e["code"] for e in pool]
    print(f"池 {len(codes)} 只 · {len(weeks)} 周提案", file=sys.stderr)

    series = load_prices(codes, args.start)
    idx = load_index(args.index, args.start)
    # _ret/_pos120 expect a {code: {dates, closes, idx}} mapping, so wrap the
    # index series under its own code. Before 2026-09-10 this was a bare
    # {dates, closes, idx} dict, so series.get(args.index) always returned None
    # -> every ex{h} (excess vs index) came out None -> all group stats n=0.
    ix = {args.index: {"dates": idx["dates"], "closes": idx["closes"],
                       "idx": {d: i for i, d in enumerate(idx["dates"])}}}

    rows: list[dict] = []          # 逐 (周, 股)
    week_tables = []
    for w in weeks:
        d0 = w["date"].replace("-", "")
        entry = _next_trading_day(series, d0, codes)
        if not entry:
            print(f"  [warn] {w['date']} 无交易日对齐", file=sys.stderr)
            continue
        w["entry"] = entry
        groups = [("treated", w["treated"]), ("near", w["near"]), ("fresh", w["fresh"])]
        wk = {"date": w["date"], "entry": entry, "total_evaluated": w["total_evaluated"],
              "eligible": w["eligible"], "groups": {}}
        for gname, gitems in groups:
            for it in gitems:
                rec = {"week": w["date"], "entry": entry, "group": gname,
                       "code": it["code"], "name": it.get("name"),
                       "score": it.get("score"), "signals": it.get("signals") or []}
                rec["pre_60d"] = _ret(series, it["code"], entry, 60, forward=False)
                rec["pre_120d"] = _ret(series, it["code"], entry, 120, forward=False)
                rec["pos120"] = _pos120(series, it["code"], entry)
                rec["dd120"] = _dd120(series, it["code"], entry)
                for h in HORIZONS:
                    r = _ret(series, it["code"], entry, h, forward=True)
                    ri = _ret(ix, args.index, entry, h, forward=True)
                    rec[f"fwd{h}"] = r
                    rec[f"ex{h}"] = (r - ri) if (r is not None and ri is not None) else None
                rows.append(rec)
                wk["groups"].setdefault(gname, []).append(rec)

        # 同池等权 (剥离 beta): 逐窗口算, 需要所有成员收益 → 后处理
        for gname, items in wk["groups"].items():
            passes = 0
            for h in HORIZONS:
                vals = [r[f"ex{h}"] for r in items if r.get(f"ex{h}") is not None]
                wk.setdefault("h", {}).setdefault(str(h), {})[gname] = _stat(vals)
                if vals:
                    passes += 1
        # 逐周 spread: treated − kept(near+fresh)
        kept = wk["groups"].get("near", []) + wk["groups"].get("fresh", [])
        wk["spread"] = {}
        for h in HORIZONS:
            tv = [r[f"ex{h}"] for r in wk["groups"].get("treated", []) if r.get(f"ex{h}") is not None]
            kv = [r[f"ex{h}"] for r in kept if r.get(f"ex{h}") is not None]
            if tv and kv:
                wk["spread"][str(h)] = round(statistics.mean(tv) - statistics.mean(kv), 2)
        week_tables.append(wk)

    # ── 同池等权超额 (每只股减当周池均值) ──
    by_week: dict[str, list[dict]] = {}
    for r in rows:
        by_week.setdefault(r["week"], []).append(r)
    for wk_date, rs in by_week.items():
        for h in HORIZONS:
            vals = [r[f"fwd{h}"] for r in rs if r.get(f"fwd{h}") is not None]
            if len(vals) < 3:
                continue
            mu = statistics.mean(vals)
            for r in rs:
                if r.get(f"fwd{h}") is not None:
                    r[f"pool_ex{h}"] = round(r[f"fwd{h}"] - mu, 3)

    # ── 组统计 ──
    summary: dict = {"n_weeks": len(week_tables), "n_rows": len(rows), "horizons": {}}
    for h in HORIZONS:
        hh: dict = {}
        for g in ("treated", "near", "fresh"):
            rs = [r for r in rows if r["group"] == g and r.get(f"ex{h}") is not None]
            hh[g] = _stat([r[f"ex{h}"] for r in rs])
            hh[g]["pool_rel"] = _stat([r.get(f"pool_ex{h}") for r in rs])
            hh[g]["pre_60d"] = _stat([r.get("pre_60d") for r in rs])
            hh[g]["pre_120d"] = _stat([r.get("pre_120d") for r in rs])
            hh[g]["dd120"] = _stat([r.get("dd120") for r in rs])
            hh[g]["pos120"] = _stat([r.get("pos120") for r in rs])
        summary["horizons"][str(h)] = hh

    # ── 逐周 spread t 检验 (只在 treated 非空的周 + 有前瞻数据的周) ──
    spreads: dict = {}
    for h in HORIZONS:
        vs = [wk["spread"].get(str(h)) for wk in week_tables]
        spreads[str(h)] = _ttest([v for v in vs if v is not None])
    summary["weekly_spread"] = spreads

    # ── score 分桶单调性 (treated+near, 有数值 score 的) ──
    bins = {"3-4": [], "5-6": [], "7-8": [], "9+": []}
    for r in rows:
        s = r.get("score")
        if s is None:
            continue
        key = "3-4" if s <= 4 else "5-6" if s <= 6 else "7-8" if s <= 8 else "9+"
        for h in HORIZONS:
            if r.get(f"ex{h}") is not None:
                bins[key].append((h, r[f"ex{h}"], r.get(f"pool_ex{h}"), r["week"]))
    bucket_out = {}
    for k, items in bins.items():
        bucket_out[k] = {
            ("h%d" % h): {
                "abs": _stat([v for hh, v, _, _ in items if hh == h]),
                "pool_rel": _stat([p for hh, _, p, _ in items if hh == h]),
            } for h in HORIZONS
        }
    summary["score_buckets"] = bucket_out

    # ── 周内去均值的 Spearman(score, 前瞻超额) ──
    ic = {}
    for h in HORIZONS:
        pairs = []
        for r in rows:
            if r.get("score") is None or r.get(f"ex{h}") is None:
                continue
            pairs.append((float(r["score"]), r[f"ex{h}"]))
        ic[str(h)] = {"pooled": _spearman(pairs), "n": len(pairs)}
        # 逐周
        per_week = []
        for d, rs in by_week.items():
            p = [(float(r["score"]), r[f"ex{h}"]) for r in rs
                 if r.get("score") is not None and r.get(f"ex{h}") is not None]
            s = _spearman(p)
            if s is not None:
                per_week.append(s)
        if per_week:
            ic[str(h)]["weekly_mean"] = round(statistics.mean(per_week), 3)
            ic[str(h)]["weekly_neg"] = sum(1 for x in per_week if x < 0)
            ic[str(h)]["weekly_n"] = len(per_week)
    summary["ic_score_vs_excess"] = ic

    # ── 逐股先聚合 (处理重复观测) ──
    per_code: dict = {}
    for h in HORIZONS:
        agg = {}
        for g in ("treated", "near", "fresh"):
            byc: dict[str, list[float]] = {}
            for r in rows:
                if r["group"] != g or r.get(f"ex{h}") is None:
                    continue
                byc.setdefault(r["code"], []).append(r[f"ex{h}"])
            means = [statistics.mean(v) for v in byc.values()]
            agg[g] = _stat(means)
            agg[g]["n_codes"] = len(byc)
        per_code[str(h)] = agg
    summary["per_code_dedup"] = per_code

    # ── 最新未验证提案的实现收益 (scoreboard) ──
    scoreboard = []
    latest_close = None
    for c in codes:
        s = series.get(c)
        if s:
            latest_close = max(latest_close or "", s["dates"][-1])
    for r in rows:
        if r["entry"] > "20260812":      # 20 日窗口未必走满
            continue
        s = series.get(r["code"])
        if not s:
            continue
        i = s["idx"].get(r["entry"])
        if i is None:
            continue
        cur = s["closes"][-1]
        r["since_pct"] = round((cur / s["closes"][i] - 1) * 100, 2)
        scoreboard.append(r)

    out = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "params": {"pool": len(codes), "weeks": len(weeks), "threshold": THRESHOLD,
                   "age_floor_days": AGE_FLOOR_DAYS, "index": args.index,
                   "horizons": list(HORIZONS)},
        "week_index": [{"date": w["date"], "entry": w.get("entry"),
                        "total_evaluated": w["total_evaluated"],
                        "eligible": w["eligible"],
                        "n": {g: len(w["groups"].get(g, []))
                              for g in ("treated", "near", "fresh")},
                        "spread": w.get("spread"),
                        "h": w.get("h")} for w in week_tables],
        "summary": summary,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    Path("/tmp/decay_rows.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")

    # ── 打印 ──
    print(f"\n{'='*78}\n观察池 decay 提案前瞻回测 · {len(codes)} 只池 · {len(week_tables)} 周\n{'='*78}")
    print(f"{'周':<12}{'入场':<10}{'评估':>4}{'treated':>8}{'near':>6}{'fresh':>7}"
          f"{'spread20':>10}{'spread40':>10}")
    for wk in week_tables:
        n = wk["n"] if "n" in wk else {g: len(wk["groups"].get(g, [])) for g in ("treated", "near", "fresh")}
        print(f"{wk['date']:<12}{wk.get('entry',''):<10}{wk.get('eligible') or 0:>4}"
              f"{n['treated']:>8}{n['near']:>6}{n['fresh']:>7}"
              f"{(wk['spread'] or {}).get('20',''):>10}{(wk['spread'] or {}).get('40',''):>10}")
    for h in HORIZONS:
        hh = summary["horizons"][str(h)]
        print(f"\n── 前瞻 {h} 交易日 · 超额 vs {args.index} (点内样本) ──")
        print(f"{'组':<9}{'n':>5}{'均值':>9}{'中位':>9}{'胜率%':>8}{'池内相对':>10}"
              f"{'前60日':>9}{'前120日':>9}{'距高120':>9}{'pos120':>8}")
        for g in ("treated", "near", "fresh"):
            s = hh[g]
            if not s.get("n"):
                print(f"{g:<9}{'0':>5}")
                continue
            print(f"{g:<9}{s['n']:>5}{s['mean']:>9}{s['median']:>9}{s['pos_pct']:>8}"
                  f"{s['pool_rel'].get('mean','-'):>10}{s['pre_60d'].get('mean','-'):>9}"
                  f"{s['pre_120d'].get('mean','-'):>9}{s['dd120'].get('mean','-'):>9}"
                  f"{s['pos120'].get('mean','-'):>8}")
        sp = summary["weekly_spread"][str(h)]
        if sp.get("n"):
            print(f"  逐周 spread(treated − 保留): 均值 {sp['mean']} · sd {sp['sd']} · "
                  f"t {sp['t']} · {sp['pos_weeks']}/{sp['n']} 周为正 (spread=被移除组−保留组; 负=移除是对的)")
    print("\n── score 分桶 (超额 vs index, 点内) ──")
    for k, v in summary["score_buckets"].items():
        parts = []
        for h in HORIZONS:
            a = v[f"h{h}"]["abs"]
            parts.append(f"h{h}: n={a.get('n',0)} mean={a.get('mean','-')} "
                         f"pool_rel={v[f'h{h}']['pool_rel'].get('mean','-')}")
        print(f"  score {k:<5} " + " | ".join(parts))
    print("\n── Spearman(score, 前瞻超额) 负=高分跑得差(机制有效) ──")
    for h in HORIZONS:
        print(f"  h{h}: {summary['ic_score_vs_excess'][str(h)]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
