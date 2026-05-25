"""watchlist_decay.py — 观察池 staleness 评分器 (v1).

设计目标
--------
让 watchlist 有"退出机制"——加入 ≥30 天后, 如果某只观察池股累计触发多个
失活信号, 就自动建议 op:remove 提议给用户审。

只动 `tier == "观察池"`：基础仓 / 博弈仓-追涨 永远不评估 (用户实际持仓,
减仓 / 卖出 由用户自行通知, 这里 default-deny)。

评分规则 (0..max≈22, threshold=5 进 stale)
-----------------------------------------
| 信号                  | 权重 | 说明                                                    |
|----------------------|------|--------------------------------------------------------|
| invalidate_hit        |  10  | entry.trigger.invalidate clause 命中 (manual override) |
| theme_cooled          |   3  | 所有可映射 themes 的板块 nav_1m≤0 且 flow_20d≤0         |
| never_buy             |   3  | sector_picks_history 中 ≥4 次评估全部不是 BUY/TREND_BUY|
| fundamentals_broken   |   2  | ROE ≤ 0 OR q_profit_yoy ≤ -20%                         |
| underperform_csi300   |   2  | A股 only. 自加入日, 累涨 - 沪深300 ≤ -25 pct           |
| vol_dead              |   1  | A股 only. 60d 主力净流入 ≤ -2亿 AND 60d nav ≤ -10%     |

threshold ≥5 (or invalidate_hit) → stale (写 op:remove 提议)
threshold 3-4 → near-stale (人读监控段, 不变成 proposal)
threshold <3 → fresh (略)

数据源 / 复用
-------------
- watchlist.yaml         主清单 (只读)
- sector_score.score_sector(concept)   板块 flow/nav 信号 (复用)
- sector_picks_history.jsonl           历史 verdict (sector_picks.py 落盘)
- tushare fina_indicator               最新 ROE / 净利同比
- tushare daily, index_daily           股票 + 沪深300 收盘
- tushare moneyflow                    个股大单流入 60d 累计

CLI
---
默认: 文本报告
--json:                  JSON 输出
--emit-proposals:        YAML 提议片段 (供 sunday-preview cron 合并到 proposed/<date>.yaml)
--invalidate-hits A,B:   把指定 codes 标记为 invalidate-hit (强制 +10 分)
--threshold N:           调阈值 (default 5)
--age-floor N:           调 age floor 天数 (default 30)
--limit N:               只评估前 N 只 (debug)
"""
from __future__ import annotations
import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Any

import yaml

_HERE = Path(__file__).resolve().parent
_WATCHLIST = _HERE / "watchlist.yaml"
_HISTORY = _HERE / "sector_picks_history.jsonl"
_TUSHARE = _HERE / "tushare.py"

# Signal weights
W = {
    "invalidate_hit":      10,
    "theme_cooled":         3,
    "never_buy":            3,
    "fundamentals_broken":  2,
    "underperform_csi300":  2,
    "vol_dead":             1,
}

DEFAULT_THRESHOLD = 5
DEFAULT_AGE_FLOOR_DAYS = 30
DEFAULT_NEAR_STALE_LO = 3
DEFAULT_NEVER_BUY_MIN_EVALS = 4


# ─── tushare CSV helper (与 sector_picks._ts 同实现) ──────────────────────


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


# ─── 数据加载 ──────────────────────────────────────────────────────────────


def _load_watchlist() -> dict[str, Any]:
    with _WATCHLIST.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_history_by_code() -> dict[str, list[dict]]:
    """Read sector_picks_history.jsonl, group by code, sort asc by ts."""
    by_code: dict[str, list[dict]] = {}
    if not _HISTORY.exists():
        return by_code
    with _HISTORY.open("r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            code = r.get("code")
            if code:
                by_code.setdefault(code, []).append(r)
    for code in by_code:
        by_code[code].sort(key=lambda r: r.get("ts", ""))
    return by_code


def _match_theme_to_concept(theme: str) -> str | None:
    """Fuzzy-map a theme string (free-form) to a CONCEPTS key."""
    try:
        from concepts_data import CONCEPTS
    except Exception:
        return None
    keys = list(CONCEPTS.keys())
    if theme in keys:
        return theme
    # 子串匹配 (双向): "AI芯片" 命中 "AI芯片 (算力核心)"
    for k in keys:
        if theme in k or k.startswith(theme):
            return k
    return None


def _coerce_date(v) -> date | None:
    """yaml 把 2026-05-08 解析成 datetime.date; 但 ISO 字符串和 None 也要兼容."""
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v)
        except ValueError:
            return None
    return None


def _days_held(added_at) -> int | None:
    d = _coerce_date(added_at)
    if d is None:
        return None
    return (date.today() - d).days


# ─── 信号评估器 ────────────────────────────────────────────────────────────


def signal_invalidate_hit(code: str, entry: dict, manual_hits: set[str]) -> dict:
    """v1: 仅支持 manual override (--invalidate-hits CODES).

    auto-eval `entry.trigger.invalidate` 文本是 free-form semantic, 留给 sunday-preview
    cron 的 Claude agent 来判断, 命中后通过 manual_hits 传入。
    """
    has_clause = bool((entry.get("trigger") or {}).get("invalidate"))
    fired = code in manual_hits
    return {
        "fired": fired,
        "evidence": {
            "has_clause": has_clause,
            "clause": (entry.get("trigger") or {}).get("invalidate"),
            "manual_override": fired,
        },
    }


def signal_theme_cooled(themes: list[str], sector_cache: dict) -> dict:
    """所有可映射 themes 的板块 nav_1m≤0 且 flow_20d≤0 → fire.

    映射不到 CONCEPTS 的 themes 跳过 (不参与计数, 也不否决 fire)。
    需要 ≥1 个可映射 theme 才能 fire (避免 0/0 退化为"全 cooled")。
    """
    evidence: dict[str, Any] = {"themes_evaluated": {}, "unmapped": []}
    cooled_count = 0
    mapped_count = 0
    try:
        from sector_score import score_sector
    except Exception:
        return {"fired": False, "evidence": {"error": "sector_score 不可导入"}}

    for theme in themes:
        concept = _match_theme_to_concept(theme)
        if not concept:
            evidence["unmapped"].append(theme)
            continue
        mapped_count += 1
        if concept not in sector_cache:
            try:
                sector_cache[concept] = score_sector(concept)
            except Exception as ex:
                sector_cache[concept] = None
                evidence["themes_evaluated"][theme] = {"concept": concept, "error": str(ex)}
                continue
        ss = sector_cache[concept]
        if ss is None or not getattr(ss, "raw_signals", None):
            evidence["themes_evaluated"][theme] = {"concept": concept, "data": "missing"}
            continue
        rs = ss.raw_signals
        nav_1m = rs.get("nav_1m", 0) or 0
        flow_20d = rs.get("flow_20d_cny", 0) or 0
        cooled = nav_1m <= 0 and flow_20d <= 0
        if cooled:
            cooled_count += 1
        evidence["themes_evaluated"][theme] = {
            "concept": concept,
            "nav_1m_pct": round(nav_1m, 2),
            "flow_20d_yi": round(flow_20d / 1e8, 2),
            "tier": ss.tier,
            "score": ss.total_score,
            "cooled": cooled,
        }
    fired = mapped_count >= 1 and cooled_count == mapped_count
    evidence["mapped_themes"] = mapped_count
    evidence["cooled_themes"] = cooled_count
    return {"fired": fired, "evidence": evidence}


def signal_never_buy(code: str, history_by_code: dict) -> dict:
    """≥4 次历史 sector_picks 评估全部 ∉ {BUY, TREND_BUY} → fire."""
    recs = history_by_code.get(code, [])
    if len(recs) < DEFAULT_NEVER_BUY_MIN_EVALS:
        return {
            "fired": False,
            "evidence": {
                "n_evaluations": len(recs),
                "note": f"insufficient (<{DEFAULT_NEVER_BUY_MIN_EVALS}), 不 fire",
            },
        }
    verdicts = [r.get("verdict") for r in recs]
    has_buy = any(v in ("BUY", "TREND_BUY") for v in verdicts)
    return {
        "fired": not has_buy,
        "evidence": {
            "n_evaluations": len(recs),
            "verdicts_seen": verdicts[-8:],
            "has_any_buy": has_buy,
        },
    }


def signal_fundamentals_broken(code: str) -> dict:
    """最新 fina_indicator: ROE ≤ 0 OR q_profit_yoy ≤ -20%."""
    rows = _ts("fina_indicator", ts_code=code,
               fields="end_date,roe,q_profit_yoy,or_yoy")
    if not rows:
        return {"fired": False, "evidence": {"note": "no fina_indicator data"}}
    latest = rows[0]
    try:
        roe = float(latest.get("roe") or 0)
    except (ValueError, TypeError):
        roe = 0.0
    try:
        q_yoy = float(latest.get("q_profit_yoy") or 0)
    except (ValueError, TypeError):
        q_yoy = 0.0
    fired = roe <= 0 or q_yoy <= -20
    return {
        "fired": fired,
        "evidence": {
            "end_date": latest.get("end_date"),
            "roe": roe,
            "q_profit_yoy": q_yoy,
        },
    }


def signal_underperform_csi300(code: str, added_at,
                                csi300_cache: dict) -> dict:
    """A股 only. 自加入日, 股票累涨 - 沪深300 累涨 ≤ -25 pct → fire."""
    if not (code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".BJ")):
        return {"fired": False, "evidence": {"note": "non-A-share, skip"}}
    d_added = _coerce_date(added_at)
    if d_added is None:
        return {"fired": False, "evidence": {"note": "added_at unparseable"}}
    days = (date.today() - d_added).days
    if days < DEFAULT_AGE_FLOOR_DAYS:
        return {"fired": False, "evidence": {"note": f"only {days}d, <floor"}}

    start_yyyymmdd = d_added.strftime("%Y%m%d")
    today_yyyymmdd = date.today().strftime("%Y%m%d")

    # CSI 300 cache (session-wide)
    if "data" not in csi300_cache or csi300_cache.get("start") != start_yyyymmdd:
        idx_rows = _ts("index_daily", ts_code="000300.SH",
                       start_date=start_yyyymmdd, end_date=today_yyyymmdd,
                       fields="trade_date,close")
        csi300_cache["data"] = idx_rows
        csi300_cache["start"] = start_yyyymmdd
    idx_rows = csi300_cache["data"]
    if not idx_rows:
        return {"fired": False, "evidence": {"note": "csi300 fetch failed"}}

    sk_rows = _ts("daily", ts_code=code,
                  start_date=start_yyyymmdd, end_date=today_yyyymmdd,
                  fields="trade_date,close")
    if not sk_rows or len(sk_rows) < 2:
        return {"fired": False, "evidence": {"note": "stock daily insufficient"}}

    sk_sorted = sorted(sk_rows, key=lambda r: r["trade_date"])
    idx_sorted = sorted(idx_rows, key=lambda r: r["trade_date"])
    try:
        sk_first = float(sk_sorted[0]["close"])
        sk_last = float(sk_sorted[-1]["close"])
        idx_first = float(idx_sorted[0]["close"])
        idx_last = float(idx_sorted[-1]["close"])
    except (ValueError, KeyError) as ex:
        return {"fired": False, "evidence": {"note": f"parse fail: {ex}"}}

    sk_ret = (sk_last / sk_first - 1) * 100
    idx_ret = (idx_last / idx_first - 1) * 100
    excess = sk_ret - idx_ret
    fired = excess <= -25
    return {
        "fired": fired,
        "evidence": {
            "since": str(added_at) if added_at else None,
            "stock_ret_pct": round(sk_ret, 1),
            "csi300_ret_pct": round(idx_ret, 1),
            "excess_pct": round(excess, 1),
        },
    }


def signal_vol_dead(code: str) -> dict:
    """A股 only. 60d 主力净流入 ≤ -2亿 且 60d nav ≤ -10% → fire.

    moneyflow 数据缺失 (例: 部分新股或 BJ 板) 时, 退化为 nav-only check。
    """
    if not (code.endswith(".SH") or code.endswith(".SZ") or code.endswith(".BJ")):
        return {"fired": False, "evidence": {"note": "non-A-share, skip"}}

    today = date.today()
    start = (today - timedelta(days=120)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    daily = _ts("daily", ts_code=code, start_date=start, end_date=end,
                fields="trade_date,close")
    if not daily or len(daily) < 30:
        return {"fired": False, "evidence": {"note": "daily insufficient"}}
    daily_sorted = sorted(daily, key=lambda r: r["trade_date"])[-60:]
    try:
        closes = [float(r["close"]) for r in daily_sorted]
    except (ValueError, KeyError):
        return {"fired": False, "evidence": {"note": "daily parse fail"}}
    if len(closes) < 30:
        return {"fired": False, "evidence": {"note": "closes insufficient"}}
    nav_60d_pct = (closes[-1] / closes[0] - 1) * 100

    mf = _ts("moneyflow", ts_code=code, start_date=start, end_date=end,
             fields="trade_date,buy_lg_amount,buy_elg_amount,sell_lg_amount,sell_elg_amount")
    if not mf:
        fired = nav_60d_pct <= -10
        return {
            "fired": fired,
            "evidence": {
                "nav_60d_pct": round(nav_60d_pct, 1),
                "moneyflow_data": "unavailable",
                "fallback": "nav-only check",
            },
        }
    mf_sorted = sorted(mf, key=lambda r: r["trade_date"])[-60:]
    main_net_wan = 0.0
    for r in mf_sorted:
        try:
            buy_lg = float(r.get("buy_lg_amount") or 0)
            buy_elg = float(r.get("buy_elg_amount") or 0)
            sell_lg = float(r.get("sell_lg_amount") or 0)
            sell_elg = float(r.get("sell_elg_amount") or 0)
            main_net_wan += (buy_lg + buy_elg - sell_lg - sell_elg)
        except (ValueError, TypeError):
            pass
    main_net_yi = main_net_wan / 1e4   # 万元 → 亿元
    fired = (main_net_yi <= -2) and (nav_60d_pct <= -10)
    return {
        "fired": fired,
        "evidence": {
            "main_net_60d_yi": round(main_net_yi, 2),
            "nav_60d_pct": round(nav_60d_pct, 1),
        },
    }


# ─── 主流程 ────────────────────────────────────────────────────────────────


def evaluate_entry(entry: dict, *, history: dict, sector_cache: dict,
                   csi300_cache: dict, manual_hits: set) -> dict:
    code = entry["code"]
    name = entry.get("name", "")
    themes = entry.get("themes") or []
    added_at = entry.get("added_at", "")

    signals = {
        "invalidate_hit":      signal_invalidate_hit(code, entry, manual_hits),
        "theme_cooled":        signal_theme_cooled(themes, sector_cache),
        "never_buy":           signal_never_buy(code, history),
        "fundamentals_broken": signal_fundamentals_broken(code),
        "underperform_csi300": signal_underperform_csi300(code, added_at, csi300_cache),
        "vol_dead":            signal_vol_dead(code),
    }

    score = sum(W[k] for k, sig in signals.items() if sig["fired"])
    fired_ids = [k for k, sig in signals.items() if sig["fired"]]

    return {
        "code": code,
        "name": name,
        "tier": entry.get("tier"),
        "themes": themes,
        "added_at": added_at,
        "days_held": _days_held(added_at),
        "score": score,
        "signals_fired": fired_ids,
        "signals": signals,
    }


def run_decay(*, threshold: int = DEFAULT_THRESHOLD,
              age_floor: int = DEFAULT_AGE_FLOOR_DAYS,
              manual_hits: set | None = None,
              limit: int | None = None,
              progress_to_stderr: bool = False) -> dict:
    """对 watchlist 跑一遍 decay, 返回完整 report dict."""
    manual_hits = manual_hits or set()
    wl = _load_watchlist()
    entries = wl.get("entries", []) or []

    # 作用域: 仅 观察池, age floor (但 manual_hits 例外 → 即使新加入也评)
    candidates = []
    skipped_age = []
    for e in entries:
        if e.get("tier") != "观察池":
            continue
        d = _days_held(e.get("added_at", ""))
        if d is None:
            continue
        if d < age_floor and e["code"] not in manual_hits:
            skipped_age.append({
                "code": e["code"], "name": e.get("name"), "days_held": d,
            })
            continue
        candidates.append(e)

    if limit:
        candidates = candidates[:limit]

    history = _load_history_by_code()
    sector_cache: dict = {}
    csi300_cache: dict = {}

    results = []
    for i, e in enumerate(candidates, 1):
        if progress_to_stderr:
            print(f"  [{i}/{len(candidates)}] {e['code']} {e.get('name','')}",
                  file=sys.stderr, flush=True)
        r = evaluate_entry(e, history=history, sector_cache=sector_cache,
                           csi300_cache=csi300_cache, manual_hits=manual_hits)
        results.append(r)

    results.sort(key=lambda r: -r["score"])

    stale = [r for r in results
             if r["score"] >= threshold or "invalidate_hit" in r["signals_fired"]]
    near_stale = [r for r in results
                  if (r["score"] >= DEFAULT_NEAR_STALE_LO
                      and r["score"] < threshold
                      and "invalidate_hit" not in r["signals_fired"])]
    fresh = [r for r in results
             if r["score"] < DEFAULT_NEAR_STALE_LO
             and "invalidate_hit" not in r["signals_fired"]]

    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "threshold": threshold,
        "age_floor_days": age_floor,
        "total_evaluated": len(candidates),
        "skipped_under_age": skipped_age,
        "stale": stale,
        "near_stale": near_stale,
        "fresh": fresh,
    }


# ─── proposals 输出 (供 sunday-preview cron 合并) ──────────────────────────


def emit_proposals(report: dict, base_id: int = 1000) -> dict:
    proposals = []
    for i, item in enumerate(report["stale"]):
        signals_label = "+".join(item["signals_fired"])
        evid_parts = []
        for sig_id in item["signals_fired"]:
            ev = item["signals"][sig_id]["evidence"]
            if sig_id == "invalidate_hit":
                evid_parts.append("invalidate命中")
            elif sig_id == "theme_cooled":
                evid_parts.append(f"theme_cooled({ev.get('cooled_themes')}/{ev.get('mapped_themes')})")
            elif sig_id == "never_buy":
                evid_parts.append(f"never_buy(n={ev.get('n_evaluations')})")
            elif sig_id == "fundamentals_broken":
                evid_parts.append(f"基本面破裂(ROE={ev.get('roe')},YoY={ev.get('q_profit_yoy')}%)")
            elif sig_id == "underperform_csi300":
                evid_parts.append(f"跑输沪深300({ev.get('excess_pct')}pp)")
            elif sig_id == "vol_dead":
                evid_parts.append(f"无量(主力{ev.get('main_net_60d_yi')}亿/nav{ev.get('nav_60d_pct')}%)")
        reason = (
            f"[decay score={item['score']}] {signals_label}: "
            f"{', '.join(evid_parts)}. 加入 {item['days_held']} 天."
        )
        proposals.append({
            "id": base_id + i,
            "op": "remove",
            "code": item["code"],
            "name": item["name"],
            "reason": reason,
            "evidence": {
                "score": item["score"],
                "signals_fired": item["signals_fired"],
                "details": {sid: item["signals"][sid]["evidence"]
                            for sid in item["signals_fired"]},
                "added_at": item["added_at"],
                "days_held": item["days_held"],
                "themes": item["themes"],
            },
        })

    return {
        "schema_version": 1,
        "generated_at": report["generated_at"],
        "generated_by": "watchlist_decay.py",
        "decay_meta": {
            "threshold": report["threshold"],
            "age_floor_days": report["age_floor_days"],
            "total_evaluated": report["total_evaluated"],
            "n_stale": len(report["stale"]),
            "n_near_stale": len(report["near_stale"]),
        },
        "proposals": proposals,
        "near_stale_monitor": [
            {
                "code": r["code"], "name": r["name"],
                "score": r["score"], "signals_fired": r["signals_fired"],
                "themes": r["themes"], "days_held": r["days_held"],
            }
            for r in report["near_stale"]
        ],
    }


# ─── 文本报告 ──────────────────────────────────────────────────────────────


def _print_report(report: dict, verbose: bool = False) -> None:
    print("\n━━━ watchlist_decay (v1) ━━━")
    print(f"  生成: {report['generated_at']}")
    print(f"  阈值: score ≥ {report['threshold']} → stale  /  3-{report['threshold']-1} → near-stale")
    print(f"  Age floor: 加入 ≥ {report['age_floor_days']} 天才评估 (manual_hits 例外)")
    print(f"  评估范围: {report['total_evaluated']} 只 (跳过 {len(report['skipped_under_age'])} 只新加入)")

    n_stale = len(report["stale"])
    n_near = len(report["near_stale"])
    n_fresh = len(report["fresh"])
    print(f"\n  📊 结果: stale={n_stale}  near-stale={n_near}  fresh={n_fresh}")

    if n_stale:
        print(f"\n  🪦 STALE (建议 op:remove):")
        for r in report["stale"]:
            sigs = "+".join(r["signals_fired"]) or "(none)"
            themes_s = ",".join(r["themes"][:3])
            print(f"    [{r['score']:>2}] {r['code']:<10} {r['name'][:8]:<8} "
                  f"{r['days_held']}d  themes={themes_s}  {sigs}")
            if verbose:
                for sig_id in r["signals_fired"]:
                    ev = r["signals"][sig_id]["evidence"]
                    print(f"         - {sig_id}: {json.dumps(ev, ensure_ascii=False)}")

    if n_near:
        print(f"\n  ⚠️  NEAR-STALE (监控, 不变成 proposal):")
        for r in report["near_stale"]:
            sigs = "+".join(r["signals_fired"]) or "(none)"
            themes_s = ",".join(r["themes"][:3])
            print(f"    [{r['score']:>2}] {r['code']:<10} {r['name'][:8]:<8} "
                  f"{r['days_held']}d  themes={themes_s}  {sigs}")

    if n_fresh and verbose:
        print(f"\n  ✓ FRESH ({n_fresh}, top 10):")
        for r in report["fresh"][:10]:
            print(f"    [{r['score']:>2}] {r['code']:<10} {r['name'][:8]:<8} "
                  f"{r['days_held']}d")


# ─── CLI ───────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description="Watchlist 观察池 staleness 评分器 (v1).")
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                    help=f"stale 分数阈值 (default {DEFAULT_THRESHOLD})")
    ap.add_argument("--age-floor", type=int, default=DEFAULT_AGE_FLOOR_DAYS,
                    help=f"age floor 天数 (default {DEFAULT_AGE_FLOOR_DAYS})")
    ap.add_argument("--invalidate-hits", type=str, default="",
                    help="逗号分隔 codes, 标记 invalidate-hit (强制 +10 分)")
    ap.add_argument("--limit", type=int, help="只评前 N 只 (debug)")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--emit-proposals", action="store_true",
                    help="输出 YAML proposals 片段 (供 sunday-preview cron 合并)")
    ap.add_argument("--proposals-base-id", type=int, default=1000,
                    help="proposals id 起点 (避开 add 段, default 1000)")
    ap.add_argument("--progress", action="store_true",
                    help="评估过程进度打印到 stderr")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    manual_hits = {x.strip() for x in args.invalidate_hits.split(",") if x.strip()}
    report = run_decay(
        threshold=args.threshold, age_floor=args.age_floor,
        manual_hits=manual_hits, limit=args.limit,
        progress_to_stderr=args.progress,
    )

    if args.emit_proposals:
        out = emit_proposals(report, base_id=args.proposals_base_id)
        print(yaml.safe_dump(out, allow_unicode=True, sort_keys=False, width=120))
    elif args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        _print_report(report, verbose=args.verbose)


if __name__ == "__main__":
    main()
