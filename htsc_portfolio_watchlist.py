#!/usr/bin/env python3
"""htsc_portfolio_watchlist.py — 华泰持仓/自选池快照 + market-tools watch 池差异.

Purpose
-------
This script gives cron agents a deterministic, one-command data layer for:

1. Huatai OpenClaw paper-trading account/positions (read-only)
2. Huatai OpenClaw watchlist (currently first 20 items exposed by the skill)
3. market-tools `watchlist.yaml` comparison
4. Optional one-way sync: market-tools watchlist -> Huatai watchlist

Why one-way sync only?
----------------------
The OpenClaw watchlist skill currently exposes:

- getWatchlist(query): returns only the first 20 watchlist items
- addWatchlist(query, group): add items

It does NOT expose delete or full pagination. Therefore the only safe automated
sync is additive from our canonical local watch pool to Huatai watchlist. The
reverse direction (Huatai-only -> local watchlist) requires human/framework
review and should remain a report item, not an automatic mutation.

Usage
-----
  python3 htsc_portfolio_watchlist.py --json
  python3 htsc_portfolio_watchlist.py --markdown
  python3 htsc_portfolio_watchlist.py --json --sync-missing-to-htsc --sync-limit 20

The script intentionally never prints HT_APIKEY.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
WATCHLIST_YAML = HERE / "watchlist.yaml"
STATE_DIR = HERE / ".cron_state"
SYNC_STATE_FILE = STATE_DIR / "htsc_watchlist_sync.json"
HTSC_SKILLS_DIR = Path.home() / ".homespace" / "skills"
PAPER_SCRIPT = HTSC_SKILLS_DIR / "a-share-paper-trading" / "a_share_paper_trading.py"
WATCHLIST_SCRIPT = HTSC_SKILLS_DIR / "watchlist-management" / "watchlist_management.py"


@dataclass(frozen=True)
class StockRef:
    code: str          # canonical: 000725.SZ / 601208.SH / 00992.HK
    name: str = ""
    raw: str = ""


def _run_json(cmd: list[str], *, cwd: Path, timeout: int = 60) -> dict[str, Any]:
    """Run a skill command and parse JSON. Returns a structured failure on errors."""
    try:
        cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "data": None, "error": {"category": "network", "message": f"timeout after {timeout}s"}}
    if cp.returncode != 0:
        return {
            "ok": False,
            "data": None,
            "error": {
                "category": "process",
                "message": f"exit {cp.returncode}",
                "stderr_tail": (cp.stderr or "")[-500:],
            },
        }
    try:
        return json.loads(cp.stdout)
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "data": None,
            "error": {
                "category": "decode",
                "message": str(e),
                "stdout_head": (cp.stdout or "")[:500],
            },
        }


def _canonical_a_code(code6: str) -> str:
    c = code6.strip()
    if "." in c:
        return c.upper()
    if c.startswith(("60", "68", "69", "90", "51", "58", "56")):
        return f"{c}.SH"
    if c.startswith(("00", "30", "15", "16", "18")):
        return f"{c}.SZ"
    if c.startswith(("43", "83", "87", "88")):
        return f"{c}.BJ"
    return c


def canonical_code(code: str, exchange: str | None = None) -> str:
    c = str(code or "").strip().upper()
    ex = (exchange or "").strip().upper()
    if not c:
        return ""
    if "." in c:
        return c
    if c.startswith("H") and len(c) >= 2 and c[1:].isdigit():
        return f"{c[1:].zfill(5)}.HK"
    if ex in {"SH", "SZ", "BJ", "HK"}:
        if ex == "HK":
            return f"{c.replace('HK', '').replace('H', '').zfill(5)}.HK"
        return f"{c.zfill(6)}.{ex}"
    if c.isdigit():
        if len(c) <= 5:
            # Huatai HK examples use H00992/H02513, while local uses 00992.HK.
            # Bare <=5 digit codes are ambiguous; keep HK because A-share codes are 6 digits.
            return f"{c.zfill(5)}.HK"
        return _canonical_a_code(c.zfill(6))
    return c


def display_code_for_htsc(code: str) -> str:
    c = canonical_code(code)
    if c.endswith(".HK"):
        return "H" + c.split(".")[0].zfill(5)
    if "." in c:
        return c.split(".")[0]
    return c


def parse_watch_item(s: str) -> StockRef | None:
    # Examples: 京东方Ａ(000725), 智谱(H02513), 联想集团(H00992)
    m = re.match(r"\s*(.*?)\s*[（(]([^()（）]+)[）)]\s*$", s or "")
    if not m:
        return None
    name, code = m.group(1).strip(), m.group(2).strip()
    return StockRef(code=canonical_code(code), name=name, raw=s)


def load_local_watchlist() -> list[StockRef]:
    data = yaml.safe_load(WATCHLIST_YAML.read_text(encoding="utf-8")) or {}
    out: list[StockRef] = []
    seen: set[str] = set()
    for e in data.get("entries") or []:
        code = canonical_code(e.get("code", ""))
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(StockRef(code=code, name=str(e.get("name") or ""), raw=json.dumps(e, ensure_ascii=False, default=str)))
    return out


def fetch_htsc_account() -> dict[str, Any]:
    return _run_json(["python3", str(PAPER_SCRIPT), "getAccountBalance"], cwd=PAPER_SCRIPT.parent, timeout=60)


def fetch_htsc_positions() -> dict[str, Any]:
    return _run_json(["python3", str(PAPER_SCRIPT), "getPositions"], cwd=PAPER_SCRIPT.parent, timeout=60)


def fetch_htsc_watchlist() -> dict[str, Any]:
    return _run_json(
        ["python3", str(WATCHLIST_SCRIPT), "getWatchlist", "--query", "查看我的全部自选股"],
        cwd=WATCHLIST_SCRIPT.parent,
        timeout=60,
    )


def normalize_positions(j: dict[str, Any]) -> list[dict[str, Any]]:
    if not j.get("ok"):
        return []
    positions = ((j.get("data") or {}).get("positions") or [])
    out = []
    for p in positions:
        q = dict(p)
        q["canonicalCode"] = canonical_code(q.get("stockCode", ""), q.get("exchange"))
        out.append(q)
    return out


def normalize_htsc_watch(j: dict[str, Any]) -> list[StockRef]:
    if not j.get("ok"):
        return []
    items = (((j.get("data") or {}).get("answer") or {}).get("watchStockList") or [])
    out = []
    seen = set()
    for item in items:
        ref = parse_watch_item(str(item))
        if ref and ref.code and ref.code not in seen:
            seen.add(ref.code)
            out.append(ref)
    return out


def _ref_map(refs: list[StockRef]) -> dict[str, StockRef]:
    return {r.code: r for r in refs if r.code}


def load_sync_state() -> dict[str, Any]:
    if not SYNC_STATE_FILE.exists():
        return {"synced_codes": {}, "attempts": []}
    try:
        return json.loads(SYNC_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"synced_codes": {}, "attempts": []}


def save_sync_state(state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SYNC_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_missing_to_htsc(missing: list[StockRef], *, limit: int, group: str) -> list[dict[str, Any]]:
    state = load_sync_state()
    synced_codes: dict[str, Any] = state.setdefault("synced_codes", {})
    attempts: list[dict[str, Any]] = state.setdefault("attempts", [])
    now = datetime.now().isoformat(timespec="seconds")
    results = []
    n = 0
    for ref in missing:
        if n >= limit:
            break
        if synced_codes.get(ref.code):
            results.append({"code": ref.code, "name": ref.name, "skipped": True, "reason": "already recorded synced"})
            continue
        hcode = display_code_for_htsc(ref.code)
        query = f"将{ref.name or ref.code}({hcode})加入自选"
        j = _run_json(
            ["python3", str(WATCHLIST_SCRIPT), "addWatchlist", "--query", query, "--group", group],
            cwd=WATCHLIST_SCRIPT.parent,
            timeout=60,
        )
        ok = bool(j.get("ok"))
        item = {
            "ts": now,
            "code": ref.code,
            "name": ref.name,
            "query": query,
            "group": group,
            "ok": ok,
            "error": j.get("error"),
            "result": (j.get("data") or {}).get("result") if ok else None,
        }
        attempts.append(item)
        if ok:
            synced_codes[ref.code] = {"ts": now, "name": ref.name, "group": group}
        results.append(item)
        n += 1
    save_sync_state(state)
    return results


def build_snapshot(*, do_sync: bool = False, sync_limit: int = 20, sync_group: str = "market-tools-watch") -> dict[str, Any]:
    account = fetch_htsc_account()
    positions_j = fetch_htsc_positions()
    htsc_watch_j = fetch_htsc_watchlist()

    local = load_local_watchlist()
    htsc_watch = normalize_htsc_watch(htsc_watch_j)
    positions = normalize_positions(positions_j)

    local_map = _ref_map(local)
    htsc_map = _ref_map(htsc_watch)
    position_codes = {p.get("canonicalCode") for p in positions if p.get("canonicalCode")}

    missing_from_htsc_top20 = [r for r in local if r.code not in htsc_map]
    htsc_only_top20 = [r for r in htsc_watch if r.code not in local_map]
    held_not_in_local = [p for p in positions if p.get("canonicalCode") not in local_map]
    held_not_in_htsc_top20 = [p for p in positions if p.get("canonicalCode") not in htsc_map]

    sync_results: list[dict[str, Any]] = []
    if do_sync:
        # Additive only: canonical market-tools watch pool -> Huatai watchlist.
        sync_results = sync_missing_to_htsc(missing_from_htsc_top20, limit=sync_limit, group=sync_group)

    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "htsc_watchlist_limit": 20,
            "htsc_watchlist_full_visibility": False,
            "comparison_note": "Huatai getWatchlist exposes only first 20 items; missing_from_htsc_top20 is conservative and may include items already present after rank 20.",
            "sync_mode": "one_way_local_watchlist_to_htsc_additive" if do_sync else "read_only",
            "sync_group": sync_group if do_sync else None,
            "sync_limit": sync_limit if do_sync else None,
        },
        "account": account,
        "positions_raw": positions_j,
        "positions": positions,
        "htsc_watchlist_raw": htsc_watch_j,
        "htsc_watchlist_top20": [r.__dict__ for r in htsc_watch],
        "local_watchlist": [r.__dict__ for r in local],
        "diff": {
            "local_count": len(local),
            "htsc_visible_count": len(htsc_watch),
            "positions_count": len(positions),
            "missing_from_htsc_top20": [r.__dict__ for r in missing_from_htsc_top20],
            "htsc_only_top20": [r.__dict__ for r in htsc_only_top20],
            "held_not_in_local_watchlist": held_not_in_local,
            "held_not_in_htsc_top20": held_not_in_htsc_top20,
        },
        "sync_results": sync_results,
    }


def money(v: Any) -> str:
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return str(v)


def pct(v: Any) -> str:
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return str(v)


def to_markdown(s: dict[str, Any]) -> str:
    lines: list[str] = []
    meta = s["meta"]
    acc = s.get("account") or {}
    acc_data = acc.get("data") or {}
    diff = s["diff"]
    positions = s.get("positions") or []

    lines.append("## 华泰持仓 / 自选池快照")
    lines.append("")
    lines.append(f"生成时间: `{meta['generated_at']}`")
    lines.append("")

    if acc.get("ok"):
        lines.append("### 1. 持仓池盈亏")
        lines.append(
            f"- 总资产: **{money(acc_data.get('totalAssets'))} 元**；可用资金: **{money(acc_data.get('availableBalance'))} 元**；"
            f"持仓市值: **{money(acc_data.get('totalPositionValue'))} 元**；仓位: **{pct(acc_data.get('positionRatio'))}**"
        )
        lines.append(
            f"- 当日盈亏: **{money(acc_data.get('dayProfit'))} 元** ({pct(acc_data.get('dayProfitPct'))})；"
            f"累计盈亏: **{money(acc_data.get('totalProfit'))} 元** ({pct(acc_data.get('totalProfitPct'))})"
        )
        if positions:
            lines.append("")
            lines.append("| 股票 | 代码 | 数量 | 当前价 | 市值 | 当日盈亏 | 累计盈亏 | 盈亏率 | 仓位 |")
            lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
            for p in positions:
                lines.append(
                    f"| {p.get('stockName','')} | {p.get('canonicalCode','')} | {p.get('quantity','')} | "
                    f"{money(p.get('currentPrice'))} | {money(p.get('marketValue'))} | {money(p.get('dayProfit'))} | "
                    f"{money(p.get('profit'))} | {pct(p.get('profitPct'))} | {pct(p.get('positionPct'))} |"
                )
        else:
            lines.append("- 当前无持仓明细。")
    else:
        lines.append("### 1. 持仓池盈亏")
        lines.append(f"- ⚠️ 华泰账户/持仓读取失败: `{acc.get('error')}`")

    lines.append("")
    lines.append("### 2. 自选池 vs market-tools watch 池")
    lines.append(f"- market-tools watch 池: **{diff['local_count']}** 只")
    lines.append(f"- 华泰自选池可见项: **{diff['htsc_visible_count']}** 只（接口当前仅返回前 20 条）")
    lines.append(f"- 华泰持仓数: **{diff['positions_count']}** 只")
    lines.append("- ⚠️ 差异基于华泰自选前 20 条；`missing_from_htsc_top20` 可能包含华泰第 21 条以后已存在的股票。同步采用幂等添加，不做删除。")

    def _short(items: list[dict[str, Any]], n: int = 20) -> str:
        if not items:
            return "无"
        xs = [f"{x.get('name') or x.get('stockName') or ''}({x.get('code') or x.get('canonicalCode')})" for x in items[:n]]
        tail = "" if len(items) <= n else f"，另 {len(items)-n} 只未展开"
        return "、".join(xs) + tail

    lines.append("")
    lines.append(f"- 本地 watch 池中、华泰前20未见: **{len(diff['missing_from_htsc_top20'])}** 只：{_short(diff['missing_from_htsc_top20'])}")
    lines.append(f"- 华泰前20中、本地 watch 池未见: **{len(diff['htsc_only_top20'])}** 只：{_short(diff['htsc_only_top20'])}")
    lines.append(f"- 持仓中、本地 watch 池未见: **{len(diff['held_not_in_local_watchlist'])}** 只：{_short(diff['held_not_in_local_watchlist'])}")
    lines.append(f"- 持仓中、华泰前20未见: **{len(diff['held_not_in_htsc_top20'])}** 只：{_short(diff['held_not_in_htsc_top20'])}")

    if s.get("sync_results"):
        ok = [r for r in s["sync_results"] if r.get("ok")]
        skipped = [r for r in s["sync_results"] if r.get("skipped")]
        fail = [r for r in s["sync_results"] if (not r.get("ok") and not r.get("skipped"))]
        lines.append("")
        lines.append("### 3. 本轮同步结果")
        lines.append(f"- 同步模式: `{meta['sync_mode']}`；分组: `{meta['sync_group']}`；上限: {meta['sync_limit']}")
        lines.append(f"- 成功: **{len(ok)}**；跳过: **{len(skipped)}**；失败: **{len(fail)}**")
        if ok:
            lines.append(f"- 成功添加/确认: {_short(ok)}")
        if fail:
            lines.append(f"- 失败: {_short(fail)}")
    else:
        lines.append("")
        lines.append("### 3. 同步建议")
        lines.append("- 日常报告默认只读；下一轮如需消除差异，调用：")
        lines.append("```bash")
        lines.append("cd /home/emox/work/projects/market-tools")
        lines.append("python3 htsc_portfolio_watchlist.py --markdown --sync-missing-to-htsc --sync-limit 20")
        lines.append("```")

    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Huatai portfolio/watchlist snapshot and diff")
    out = ap.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true", help="print JSON (default)")
    out.add_argument("--markdown", action="store_true", help="print markdown summary")
    ap.add_argument("--out", help="write output to file as well")
    ap.add_argument("--sync-missing-to-htsc", action="store_true", help="add local watchlist items missing from Huatai visible top20")
    ap.add_argument("--sync-limit", type=int, default=20, help="max addWatchlist calls per run")
    ap.add_argument("--sync-group", default="market-tools-watch", help="Huatai watchlist group for additive sync")
    args = ap.parse_args()

    snap = build_snapshot(do_sync=args.sync_missing_to_htsc, sync_limit=args.sync_limit, sync_group=args.sync_group)
    if args.markdown:
        text = to_markdown(snap)
    else:
        text = json.dumps(snap, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
