#!/usr/bin/env python3
"""watchlist_sync.py — MT-1.0 final-plan-only HTSC 自选同步。

Raw from-recap / from-stdin / add payloads without a qualified plan are rejected.
Use mt1.py watchlist (dry-run default). Execution uses per-symbol failure isolation,
serialized cooldown check/write, and an append-only acknowledgement ledger.
No brokerage orders, no position sizing, no watchlist deletions.
"""
from __future__ import annotations

import argparse
import fcntl
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / ".cron_state"
LEDGER_FILE = STATE_DIR / "htsc_watchlist_added.jsonl"
SKILL_PY = Path(os.path.expanduser("~/.homespace/skills/watchlist-management/watchlist_management.py"))
CN_TZ = dt.timezone(dt.timedelta(hours=8))


def now_iso() -> str:
    return dt.datetime.now(CN_TZ).isoformat(timespec="seconds")


def load_ledger() -> list[dict[str, Any]]:
    if not LEDGER_FILE.exists():
        return []
    out = []
    for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def append_ledger(entry: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def recently_added(code: str, cooldown_days: int) -> dict[str, Any] | None:
    if cooldown_days <= 0:
        return None
    cutoff = dt.datetime.now(CN_TZ) - dt.timedelta(days=cooldown_days)
    for entry in reversed(load_ledger()):
        if entry.get("code") != code:
            continue
        try:
            t = dt.datetime.fromisoformat(entry.get("added_at", ""))
            if t.tzinfo is None:
                t = t.replace(tzinfo=CN_TZ)
            if t >= cutoff:
                return entry
        except Exception:
            continue
        # first matching code beat cutoff → stop walking back
        break
    return None


def normalise_code(code: str) -> str:
    """'601688' / '601688.SH' / 'sh601688' → '601688.SH'"""
    c = (code or "").strip().upper()
    if not c:
        return c
    if c.startswith("SH") or c.startswith("SZ") or c.startswith("BJ"):
        return c[2:] + "." + c[:2]
    if "." in c:
        return c
    # bare 6-digit
    if c[:1] in {"6", "9"}:
        return c + ".SH"
    if c[:1] in {"0", "3"}:
        return c + ".SZ"
    if c[:1] in {"4", "8"}:
        return c + ".BJ"
    return c


def call_addwatchlist(query: str, group: str, *, timeout: int = 90) -> dict[str, Any]:
    if not SKILL_PY.exists():
        return {"ok": False, "error": {"category": "missing_skill", "message": f"{SKILL_PY} not found"}}
    cmd = ["python3", str(SKILL_PY), "addWatchlist", "--query", query, "--group", group]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": {"category": "timeout", "message": f"addWatchlist timed out after {timeout}s"}}
    if cp.returncode != 0:
        return {"ok": False, "error": {"category": "process", "message": f"exit {cp.returncode}", "stderr_tail": cp.stderr[-500:]}}
    try:
        return json.loads(cp.stdout)
    except Exception as e:
        return {"ok": False, "error": {"category": "decode", "message": str(e), "stdout_tail": cp.stdout[-500:]}}


def _sync_buys_locked(buys: list[dict[str, Any]], *, group: str, cooldown_days: int, dry_run: bool, source: str, method_lookup=None) -> dict[str, Any]:
    """buys: [{'code':'601688.SH','name':'华泰证券','reason':'...'}]"""
    if not buys:
        return {"ok": True, "added": [], "skipped": [], "errors": [], "source": source, "note": "empty BUY list"}

    skipped = []
    pending = []
    for b in buys:
        from mt1.plans import eligible
        plan = b.get('_mt1_final_plan') or {}
        if not eligible(plan, dt.datetime.now(CN_TZ).date(), method_lookup) or plan.get('code') != b.get('code'):
            skipped.append({"code": b.get("code"), "skip_reason": "MT-1.0 requires final qualified research plan"})
            continue
        code = normalise_code(b.get("code") or "")
        name = (b.get("name") or "").strip()
        reason = (b.get("reason") or "").strip() or source
        if not code or not name:
            skipped.append({**b, "skip_reason": "missing code or name"}); continue
        prev = recently_added(code, cooldown_days)
        if prev:
            skipped.append({"code": code, "name": name, "skip_reason": f"already added at {prev.get('added_at')} via {prev.get('source')}"})
            continue
        pending.append({"code": code, "name": name, "reason": reason})

    if not pending:
        return {"ok": True, "added": [], "skipped": skipped, "errors": [], "source": source, "note": "nothing new to add"}

    parts = [f"{p['name']}({p['code']})" for p in pending]
    query = "把 " + "、".join(parts) + " 加到自选股"

    if dry_run:
        return {"ok": True, "added": [], "skipped": skipped, "errors": [], "source": source, "dry_run_query": query, "pending": pending}

    res = call_addwatchlist(query, group=group)
    if not res.get("ok"):
        return {"ok": False, "added": [], "skipped": skipped, "errors": [{"items": pending, "error": res.get("error")}], "source": source}

    # ledger append for everything we asked for; HTSC backend may de-dup silently
    confirmed = (res.get("data") or {}).get("stocks") or {}
    confirmed_list = confirmed.get("stocks") or []
    confirmed_codes = {normalise_code(c.get("stockCode", "")) for c in confirmed_list if isinstance(c, dict)}

    added_now = []
    for p in pending:
        # consider added if returned in stocks list OR if backend ack'd as ok at all (defensive)
        ok_back = (p["code"] in confirmed_codes) or (any(p["name"] == c.get("stockName") for c in confirmed_list)) or not confirmed_list
        entry = {
            "code": p["code"],
            "name": p["name"],
            "reason": p["reason"],
            "source": source,
            "group": group,
            "added_at": now_iso(),
            "htsc_ack_in_response": ok_back,
        }
        if ok_back and confirmed_list:
            append_ledger(entry)
            added_now.append(entry)
        else:
            skipped.append({"code": p["code"], "skip_reason": "provider did not explicitly confirm symbol; no ledger write"})

    return {"ok": True, "added": added_now, "skipped": skipped, "errors": [], "source": source, "htsc_response_data": res.get("data")}


def sync_buys(buys: list[dict[str, Any]], *, group: str, cooldown_days: int, dry_run: bool, source: str, method_lookup=None) -> dict[str, Any]:
    """Serialize cooldown check + provider acknowledgement + ledger append."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "htsc_watchlist.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        results = []
        # Per-symbol isolation, including same-invocation duplicate suppression.
        seen = set()
        for item in buys:
            code = normalise_code(item.get("code", ""))
            if code in seen:
                continue
            seen.add(code)
            results.append(_sync_buys_locked([item], group=group, cooldown_days=cooldown_days,
                                            dry_run=dry_run, source=source, method_lookup=method_lookup))
        return {"ok": all(r.get("ok") for r in results), "source": source,
                "added": [x for r in results for x in r.get("added", [])],
                "skipped": [x for r in results for x in r.get("skipped", [])],
                "errors": [x for r in results for x in r.get("errors", [])],
                "pending": [x for r in results for x in r.get("pending", [])]}


def parse_recap_buys(path: Path) -> list[dict[str, Any]]:
    """Raw machine candidates never qualify as final medium-term conclusions."""
    return []


def main() -> None:
    p = argparse.ArgumentParser(prog="watchlist_sync")
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--group", default="默认组")
    common.add_argument("--cooldown-days", type=int, default=30, help="skip a code if added within N days (0=disable)")
    common.add_argument("--dry-run", action="store_true")
    common.add_argument("--source", default="manual", help="free-text label, e.g. 'evening 2026-06-17'")

    s1 = sub.add_parser("from-recap", parents=[common])
    s1.add_argument("--recap-json", required=True)

    s2 = sub.add_parser("from-stdin", parents=[common], help="read JSON list from stdin")

    s3 = sub.add_parser("add", parents=[common], help="add a single stock by --code/--name/--reason")
    s3.add_argument("--code", required=True)
    s3.add_argument("--name", required=True)
    s3.add_argument("--reason", default="")

    s4 = sub.add_parser("show-ledger", help="print local ledger (recently-added)")
    s4.add_argument("--days", type=int, default=30)

    args = p.parse_args()

    if args.cmd == "show-ledger":
        cutoff = dt.datetime.now(CN_TZ) - dt.timedelta(days=args.days)
        rows = []
        for e in load_ledger():
            try:
                t = dt.datetime.fromisoformat(e.get("added_at", ""))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=CN_TZ)
                if t >= cutoff:
                    rows.append(e)
            except Exception:
                continue
        print(json.dumps({"ok": True, "ledger_path": str(LEDGER_FILE), "rows": rows, "count": len(rows)}, ensure_ascii=False, indent=2))
        return

    if args.cmd == "from-recap":
        buys = parse_recap_buys(Path(args.recap_json))
        # default source if user left it 'manual'
        src = args.source if args.source != "manual" else f"evening recap {Path(args.recap_json).stem}"
        res = sync_buys(buys, group=args.group, cooldown_days=args.cooldown_days, dry_run=args.dry_run, source=src)
        res["picked_buys"] = buys
        res["note"] = "MT-1.0: 原始候选自动同步已禁用；使用 mt1.py watchlist 消费最终计划"
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    if args.cmd == "from-stdin":
        buys = json.loads(sys.stdin.read())
        if not isinstance(buys, list):
            raise SystemExit("from-stdin expects a JSON array")
        res = sync_buys(buys, group=args.group, cooldown_days=args.cooldown_days, dry_run=args.dry_run, source=args.source)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    if args.cmd == "add":
        res = sync_buys([{"code": args.code, "name": args.name, "reason": args.reason}], group=args.group, cooldown_days=args.cooldown_days, dry_run=args.dry_run, source=args.source)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    main()
