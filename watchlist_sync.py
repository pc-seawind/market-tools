#!/usr/bin/env python3
"""watchlist_sync.py — push BUY-tagged stocks to HTSC 自选 (default group).

Why
---
After each evening recap / morning brief, the framework decides a small set of
"常规 BUY" candidates (Tier1 板块 HOT + 个股通过). Manually copying these
into HTSC 自选 is fragile (forget / typo). This helper:

  1. Reads a list of (code, name, source_meta) tuples,
  2. De-dupes against a local idempotency ledger (.cron_state/htsc_watchlist_added.jsonl)
     so we never spam the same stock to HTSC twice,
  3. Calls the HTSC `addWatchlist` skill with one batched natural-language query,
  4. Appends the successfully-added items back to the ledger with timestamp +
     reason (which report decided BUY).

Why a local ledger and not just trust HTSC?
  HTSC `getWatchlist` only returns the first 20 items, so once the watchlist
  grows past 20 we cannot reliably check membership remotely. The ledger is a
  cheap, append-only safety net: if a code appears in the ledger within the
  last `--cooldown-days` (default 30), we skip re-adding (HTSC is supposedly
  idempotent, but no need to spam network calls or risk a quota hit).

Usage
-----
  # one-shot from BUY list on stdin (JSON: [{"code":"601688.SH","name":"华泰证券","reason":"..."}])
  python3 watchlist_sync.py from-stdin --group "默认组"

  # one-shot CLI:
  python3 watchlist_sync.py add --code 601688.SH --name 华泰证券 --reason "evening 2026-06-17"

  # auto-pull from an evening recap json (the orchestration shell output):
  python3 watchlist_sync.py from-recap --recap-json /tmp/evening_recap_2026-06-17.json

Exit code: 0 always (failure of one symbol must NOT break a cron pipeline).
JSON to stdout summarising what we did.
"""
from __future__ import annotations

import argparse
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


def sync_buys(buys: list[dict[str, Any]], *, group: str, cooldown_days: int, dry_run: bool, source: str) -> dict[str, Any]:
    """buys: [{'code':'601688.SH','name':'华泰证券','reason':'...'}]"""
    if not buys:
        return {"ok": True, "added": [], "skipped": [], "errors": [], "source": source, "note": "empty BUY list"}

    skipped = []
    pending = []
    for b in buys:
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
        append_ledger(entry)
        added_now.append(entry)

    return {"ok": True, "added": added_now, "skipped": skipped, "errors": [], "source": source, "htsc_response_data": res.get("data")}


def parse_recap_buys(path: Path) -> list[dict[str, Any]]:
    """Pull 常规 BUY items out of an evening_recap_*.json (the data file)."""
    d = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for concept, pk in (d.get("picks") or {}).items():
        if not isinstance(pk, dict) or pk.get("error"):
            continue
        ss = pk.get("sector_score") or {}
        tier = ss.get("tier", "")
        score = ss.get("total_score") or 0
        is_hot = ("HOT" in tier) or (score >= 60)
        if not is_hot:
            continue  # framework v2: only HOT 板块的 BUY 才入自选
        for e in pk.get("evaluations") or []:
            if e.get("verdict") != "BUY":
                continue
            st = e.get("stock") or {}
            code = st.get("code") or st.get("ts_code") or ""
            name = st.get("name") or ""
            if not code or not name:
                continue
            reason = f"{concept} {tier}({score:.1f}) | {e.get('reason','')}"
            out.append({"code": code, "name": name, "reason": reason})
    # de-dup within a single recap (some stocks could appear in 2 sectors)
    seen, dedup = set(), []
    for b in out:
        c = normalise_code(b["code"])
        if c in seen:
            continue
        seen.add(c)
        b["code"] = c
        dedup.append(b)
    return dedup


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
