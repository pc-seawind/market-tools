#!/usr/bin/env python3
"""htsc_raw_data.py — Huatai OpenClaw raw/semi-structured data adapter.

Purpose
-------
This is the *data* counterpart of `htsc_skill_bridge.py`.

- `htsc_skill_bridge.py` is for HTSC second-opinion / narrative prose.
- `htsc_raw_data.py` is for fields that can be consumed by cron as backup data.

It wraps the installed Huatai OpenClaw skills and normalizes the parts that are
actually structured enough to be useful:

1. a-share-paper-trading.searchStock  → stock identity resolver
2. a-share-paper-trading.getQuote     → A-share quote snapshot (structured JSON)
3. watchlist-management.getWatchlist  → Huatai watchlist first 20 items
4. select-stock.selectStock           → markdown tables parsed into records
5. query-indicator.queryIndicator     → text + any markdown tables parsed

Important boundaries
--------------------
- This is NOT a full Tushare replacement. Tushare/akshare/local parquet remain
  the primary reproducible data layer.
- HTSC quote is useful as a small-scope backup for latest A-share quote fields.
- HTSC select-stock is useful as a candidate/fund-flow backup, but its backend
  may rewrite natural-language conditions; keep `query` and `text` for audit.
- HTSC query-indicator is semi-structured; use parsed tables when available,
  otherwise treat as readable backup evidence.
- `a-share-paper-trading` account/positions are paper-trading only; this script
  intentionally does not expose positions as real holdings.

Usage
-----
  python3 htsc_raw_data.py search --query 京东方A
  python3 htsc_raw_data.py quote --code 000725 --exchange SZ
  python3 htsc_raw_data.py quote --query 京东方A
  python3 htsc_raw_data.py quotes --codes 000725.SZ,601208.SH,688012.SH
  python3 htsc_raw_data.py watchlist
  python3 htsc_raw_data.py watchlist-quotes --limit 20
  python3 htsc_raw_data.py select --query "最近5日半导体主力净流入前10"
  python3 htsc_raw_data.py indicator --query "对比京东方A、TCL科技涨跌幅、PE、PB"

Output is JSON only. It never prints HT_APIKEY.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / ".cron_state"
CACHE_DIR = STATE_DIR / "htsc_raw_cache"
HTSC_SKILLS_DIR = Path(os.environ.get("HTSC_SKILLS_DIR", Path.home() / ".homespace" / "skills"))

PAPER_SCRIPT = HTSC_SKILLS_DIR / "a-share-paper-trading" / "a_share_paper_trading.py"
WATCHLIST_SCRIPT = HTSC_SKILLS_DIR / "watchlist-management" / "watchlist_management.py"
SELECT_SCRIPT = HTSC_SKILLS_DIR / "select-stock" / "select_stock.py"
QUERY_SCRIPT = HTSC_SKILLS_DIR / "query-indicator" / "query_indicator.py"

CN_TZ = dt.timezone(dt.timedelta(hours=8))


def now_iso() -> str:
    return dt.datetime.now(CN_TZ).isoformat(timespec="seconds")


def cache_key(payload: dict[str, Any]) -> str:
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def load_cache(key: str, ttl_sec: int) -> dict[str, Any] | None:
    if ttl_sec <= 0:
        return None
    p = CACHE_DIR / f"{key}.json"
    if not p.exists():
        return None
    try:
        age = time.time() - p.stat().st_mtime
        if age > ttl_sec:
            return None
        j = json.loads(p.read_text(encoding="utf-8"))
        j["cached"] = True
        j["cache_age_sec"] = round(age, 1)
        return j
    except Exception:
        return None


def save_cache(key: str, result: dict[str, Any]) -> None:
    if not result.get("ok"):
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / f"{key}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def run_json(cmd: list[str], *, cwd: Path, timeout: int, retries: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    last: dict[str, Any] = {"ok": False, "data": None, "error": {"category": "not_run", "message": "not run"}}
    for i in range(max(0, retries) + 1):
        started = time.time()
        att: dict[str, Any] = {"n": i + 1, "started_at": now_iso(), "timeout": timeout}
        try:
            cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
            att.update({"rc": cp.returncode, "elapsed_sec": round(time.time() - started, 2)})
            if cp.returncode != 0:
                last = {
                    "ok": False,
                    "data": None,
                    "error": {
                        "category": "process",
                        "message": f"exit {cp.returncode}",
                        "stderr_tail": (cp.stderr or "")[-800:],
                        "stdout_head": (cp.stdout or "")[:500],
                    },
                }
            else:
                try:
                    last = json.loads(cp.stdout)
                except json.JSONDecodeError as e:
                    last = {
                        "ok": False,
                        "data": None,
                        "error": {
                            "category": "decode",
                            "message": str(e),
                            "stdout_head": (cp.stdout or "")[:800],
                            "stderr_tail": (cp.stderr or "")[-500:],
                        },
                    }
        except subprocess.TimeoutExpired:
            att.update({"rc": None, "elapsed_sec": round(time.time() - started, 2)})
            last = {"ok": False, "data": None, "error": {"category": "network", "message": f"timeout after {timeout}s"}}
        err = last.get("error") or {}
        att.update({"ok": bool(last.get("ok")), "error_category": err.get("category")})
        attempts.append(att)
        if last.get("ok"):
            break
        cat = str(err.get("category") or "").lower()
        msg = str(err.get("message") or "").lower()
        retriable = cat in {"network", "process", "decode"} or "超时" in msg or "timeout" in msg
        if not retriable or i >= retries:
            break
        time.sleep(min(2 + i * 2, 8))
    return last, attempts


def canonical_code(code: str, exchange: str | None = None) -> str:
    c = str(code or "").strip().upper()
    ex = str(exchange or "").strip().upper()
    if not c:
        return ""
    if "." in c:
        return c
    if c.startswith("H") and c[1:].isdigit():
        return f"{c[1:].zfill(5)}.HK"
    if ex in {"SH", "SZ", "BJ", "HK"}:
        return f"{c.zfill(5 if ex == 'HK' else 6)}.{ex}"
    if c.isdigit():
        if len(c) <= 5:
            return f"{c.zfill(5)}.HK"
        c = c.zfill(6)
        if c.startswith(("60", "68", "69", "90", "51", "58", "56")):
            return f"{c}.SH"
        if c.startswith(("00", "30", "15", "16", "18")):
            return f"{c}.SZ"
        if c.startswith(("43", "83", "87", "88")):
            return f"{c}.BJ"
    return c


def split_canonical(code: str) -> tuple[str, str | None]:
    c = canonical_code(code)
    if "." not in c:
        return c, None
    base, ex = c.split(".", 1)
    if ex == "HK":
        return base.zfill(5), "HK"
    return base.zfill(6), ex


def parse_watch_item(s: str) -> dict[str, Any] | None:
    m = re.match(r"\s*(.*?)\s*[（(]([^()（）]+)[）)]\s*$", s or "")
    if not m:
        return None
    name, code = m.group(1).strip(), m.group(2).strip()
    return {"name": name, "rawCode": code, "canonicalCode": canonical_code(code), "raw": s}


def parse_cn_number(s: Any) -> float | None:
    """Best-effort parser for Chinese market strings: 28.54亿, 3.04%, 6840.76亿."""
    if s is None:
        return None
    text = str(s).strip().replace(",", "")
    if not text or text in {"-", "--", "null", "None"}:
        return None
    mult = 1.0
    if text.endswith("%"):
        text = text[:-1]
    # Handle RMB/market units. Do this before stripping non-number chars.
    if "万亿" in text:
        mult = 1e12
    elif "亿" in text:
        mult = 1e8
    elif "万" in text:
        mult = 1e4
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0)) * mult
    except Exception:
        return None


def normalize_cell(value: str, key: str = "") -> dict[str, Any]:
    text = str(value or "")
    out: dict[str, Any] = {"raw": value}
    # Do not treat names like "北方华创(002371)" as numeric values.
    name_like_keys = {"股票名称", "公司名称", "标的", "名称", "股票", "证券名称"}
    if key not in name_like_keys:
        num = parse_cn_number(value)
        if num is not None:
            out["num"] = num
    # Common HTSC markdown cell shape: 海光信息(688041), 智谱(H02513).
    m = re.match(r"\s*(.*?)\s*[（(]([^()（）]+)[）)]\s*$", text)
    if m:
        name, code = m.group(1).strip(), m.group(2).strip()
        out["name"] = name
        out["code"] = code
        out["canonicalCode"] = canonical_code(code)
    return out


def parse_markdown_tables(text: str) -> list[dict[str, Any]]:
    """Parse simple GitHub-style markdown tables from HTSC answers."""
    lines = (text or "").splitlines()
    tables: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not (line.startswith("|") and line.endswith("|")):
            i += 1
            continue
        if i + 1 >= len(lines):
            i += 1
            continue
        sep = lines[i + 1].strip()
        if not (sep.startswith("|") and re.fullmatch(r"[|:\-\s]+", sep)):
            i += 1
            continue
        headers = [c.strip() for c in line.strip("|").split("|")]
        rows = []
        i += 2
        while i < len(lines):
            rowline = lines[i].strip()
            if not (rowline.startswith("|") and rowline.endswith("|")):
                break
            cells = [c.strip() for c in rowline.strip("|").split("|")]
            # Pad/truncate so dict construction is stable.
            if len(cells) < len(headers):
                cells += [""] * (len(headers) - len(cells))
            if len(cells) > len(headers):
                cells = cells[: len(headers)]
            raw = dict(zip(headers, cells))
            norm = {k: normalize_cell(v, k) for k, v in raw.items()}
            enriched: dict[str, Any] = {}
            # Promote stock identity when a table has a 股票名称/名称 style column.
            for k, cell in norm.items():
                if isinstance(cell, dict) and cell.get("canonicalCode") and k in {"股票名称", "公司名称", "标的", "名称", "股票", "证券名称"}:
                    enriched.update({
                        "stockName": cell.get("name"),
                        "stockCodeRaw": cell.get("code"),
                        "canonicalCode": cell.get("canonicalCode"),
                    })
                    break
            rows.append({"raw": raw, "normalized": norm, "enriched": enriched})
            i += 1
        tables.append({"columns": headers, "rows": rows, "rowCount": len(rows)})
    return tables


def wrap(kind: str, *, ok: bool, data: Any = None, raw: Any = None, error: Any = None,
         attempts: list[dict[str, Any]] | None = None, cached: bool = False,
         params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": ok,
        "source": "htsc_openclaw_raw",
        "kind": kind,
        "generated_at": now_iso(),
        "cached": cached,
        "params": params or {},
        "data": data,
        "raw": raw,
        "error": error,
        "attempts": attempts or [],
    }


def maybe_cached(kind: str, params: dict[str, Any], ttl: int) -> tuple[str, dict[str, Any] | None]:
    key = cache_key({"v": 1, "kind": kind, "params": params})
    return key, load_cache(key, ttl)


def cmd_search(args) -> dict[str, Any]:
    params = {"query": args.query}
    key, cached = maybe_cached("search", params, args.cache_ttl)
    if cached and not args.no_cache:
        return cached
    raw, attempts = run_json(["python3", str(PAPER_SCRIPT), "searchStock", "--query", args.query], cwd=PAPER_SCRIPT.parent, timeout=args.timeout, retries=args.retries)
    data = raw.get("data") if raw.get("ok") else None
    result = wrap("search", ok=bool(raw.get("ok")), data=data, raw=raw, error=raw.get("error"), attempts=attempts, params=params)
    save_cache(key, result)
    return result


def resolve_query(query: str, *, timeout: int, retries: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    raw, attempts = run_json(["python3", str(PAPER_SCRIPT), "searchStock", "--query", query], cwd=PAPER_SCRIPT.parent, timeout=timeout, retries=retries)
    if not raw.get("ok"):
        return None, {"raw": raw, "attempts": attempts}
    results = (raw.get("data") or {}).get("results") or []
    if not results:
        return None, {"raw": raw, "attempts": attempts, "error": {"category": "not_found", "message": "no search result"}}
    # Prefer exact non-B share result when available; otherwise first result.
    q = query.strip().lower()
    chosen = None
    for r in results:
        name = str(r.get("stockName") or "").lower()
        code = str(r.get("stockCode") or "")
        if (q == name or q == code.lower()) and not name.endswith("ｂ") and not name.endswith("b"):
            chosen = r
            break
    if chosen is None:
        chosen = results[0]
    return chosen, {"raw": raw, "attempts": attempts}


def cmd_quote(args) -> dict[str, Any]:
    params = {"code": args.code, "exchange": args.exchange, "query": args.query}
    key, cached = maybe_cached("quote", params, args.cache_ttl)
    if cached and not args.no_cache:
        return cached
    resolve_meta = None
    code = args.code
    exchange = args.exchange
    if args.query and not code:
        chosen, resolve_meta = resolve_query(args.query, timeout=args.timeout, retries=args.retries)
        if not chosen:
            result = wrap("quote", ok=False, data=None, raw=resolve_meta, error=(resolve_meta or {}).get("error") or {"category": "resolve_failed", "message": "searchStock failed"}, params=params)
            save_cache(key, result)
            return result
        code = chosen.get("stockCode")
        exchange = chosen.get("exchange")
    if not code:
        return wrap("quote", ok=False, error={"category": "validation", "message": "--code or --query required"}, params=params)
    if not exchange:
        base, ex = split_canonical(code)
        code, exchange = base, ex
    if exchange not in {"SH", "SZ", "BJ"}:
        return wrap("quote", ok=False, error={"category": "unsupported_market", "message": f"getQuote supports A-share SH/SZ/BJ only, got {exchange}"}, params=params)
    raw, attempts = run_json(["python3", str(PAPER_SCRIPT), "getQuote", "--stock-code", str(code).zfill(6), "--exchange", exchange], cwd=PAPER_SCRIPT.parent, timeout=args.timeout, retries=args.retries)
    data = None
    if raw.get("ok"):
        q = dict(raw.get("data") or {})
        q["stockCode"] = str(code).zfill(6)
        q["exchange"] = exchange
        q["canonicalCode"] = canonical_code(str(code).zfill(6), exchange)
        # HTSC `change` is pct change, per skill docs/examples.
        if "change" in q:
            q["changePct"] = q.get("change")
        data = q
    result = wrap("quote", ok=bool(raw.get("ok")), data=data, raw={"quote": raw, "resolve": resolve_meta}, error=raw.get("error"), attempts=attempts, params=params)
    save_cache(key, result)
    return result


def cmd_quotes(args) -> dict[str, Any]:
    codes = [x.strip() for x in (args.codes or "").split(",") if x.strip()]
    params = {"codes": codes}
    key, cached = maybe_cached("quotes", params, args.cache_ttl)
    if cached and not args.no_cache:
        return cached
    quotes = []
    errors = []
    for c in codes:
        base, ex = split_canonical(c)
        sub = argparse.Namespace(code=base, exchange=ex, query="", timeout=args.timeout, retries=args.retries, cache_ttl=args.cache_ttl, no_cache=args.no_cache)
        r = cmd_quote(sub)
        if r.get("ok"):
            quotes.append(r.get("data"))
        else:
            errors.append({"code": c, "error": r.get("error")})
    result = wrap("quotes", ok=bool(quotes) and not errors, data={"quotes": quotes, "errors": errors, "count": len(quotes)}, error=None if not errors else {"category": "partial" if quotes else "all_failed", "errors": errors}, params=params)
    save_cache(key, result)
    return result


def cmd_watchlist(args) -> dict[str, Any]:
    params = {"query": args.query}
    key, cached = maybe_cached("watchlist", params, args.cache_ttl)
    if cached and not args.no_cache:
        return cached
    raw, attempts = run_json(["python3", str(WATCHLIST_SCRIPT), "getWatchlist", "--query", args.query], cwd=WATCHLIST_SCRIPT.parent, timeout=args.timeout, retries=args.retries)
    data = None
    if raw.get("ok"):
        items = (((raw.get("data") or {}).get("answer") or {}).get("watchStockList") or [])
        parsed = [x for x in (parse_watch_item(str(i)) for i in items) if x]
        data = {"items": parsed, "rawItems": items, "count": len(parsed), "fullVisibility": False, "visibilityNote": "HTSC getWatchlist currently returns first 20 items only"}
    result = wrap("watchlist", ok=bool(raw.get("ok")), data=data, raw=raw, error=raw.get("error"), attempts=attempts, params=params)
    save_cache(key, result)
    return result


def cmd_watchlist_quotes(args) -> dict[str, Any]:
    wl_args = argparse.Namespace(query="查看我的自选股", timeout=args.timeout, retries=args.retries, cache_ttl=args.cache_ttl, no_cache=args.no_cache)
    wl = cmd_watchlist(wl_args)
    if not wl.get("ok"):
        return wrap("watchlist-quotes", ok=False, raw=wl, error=wl.get("error"), params={"limit": args.limit})
    items = (wl.get("data") or {}).get("items") or []
    quotes = []
    skipped = []
    errors = []
    for item in items[: args.limit]:
        code = item.get("canonicalCode")
        base, ex = split_canonical(code)
        if ex not in {"SH", "SZ", "BJ"}:
            skipped.append({"item": item, "reason": f"unsupported market {ex}"})
            continue
        qargs = argparse.Namespace(code=base, exchange=ex, query="", timeout=args.timeout, retries=args.retries, cache_ttl=args.cache_ttl, no_cache=args.no_cache)
        q = cmd_quote(qargs)
        if q.get("ok"):
            d = dict(q.get("data") or {})
            d["watchName"] = item.get("name")
            quotes.append(d)
        else:
            errors.append({"item": item, "error": q.get("error")})
    ok = bool(quotes)
    return wrap("watchlist-quotes", ok=ok, data={"quotes": quotes, "skipped": skipped, "errors": errors, "count": len(quotes), "watchlist": wl.get("data")}, error=None if ok else {"category": "no_quotes", "message": "no quotes fetched"}, params={"limit": args.limit})


def cmd_select(args) -> dict[str, Any]:
    params = {"query": args.query}
    key, cached = maybe_cached("select", params, args.cache_ttl)
    if cached and not args.no_cache:
        return cached
    raw, attempts = run_json(["python3", str(SELECT_SCRIPT), "selectStock", "--query", args.query], cwd=SELECT_SCRIPT.parent, timeout=args.timeout, retries=args.retries)
    text = ""
    tables = []
    if raw.get("ok"):
        text = ((raw.get("data") or {}).get("result") or "")
        tables = parse_markdown_tables(text)
    data = {"query": args.query, "text": text, "tables": tables, "tableCount": len(tables)} if raw.get("ok") else None
    result = wrap("select", ok=bool(raw.get("ok")), data=data, raw=raw, error=raw.get("error"), attempts=attempts, params=params)
    save_cache(key, result)
    return result


def cmd_indicator(args) -> dict[str, Any]:
    params = {"query": args.query}
    key, cached = maybe_cached("indicator", params, args.cache_ttl)
    if cached and not args.no_cache:
        return cached
    raw, attempts = run_json(["python3", str(QUERY_SCRIPT), "queryIndicator", "--query", args.query], cwd=QUERY_SCRIPT.parent, timeout=args.timeout, retries=args.retries)
    text = ""
    tables = []
    if raw.get("ok"):
        text = ((raw.get("data") or {}).get("answer") or "")
        tables = parse_markdown_tables(text)
    data = {"query": args.query, "text": text, "tables": tables, "tableCount": len(tables)} if raw.get("ok") else None
    result = wrap("indicator", ok=bool(raw.get("ok")), data=data, raw=raw, error=raw.get("error"), attempts=attempts, params=params)
    save_cache(key, result)
    return result


def add_common(p: argparse.ArgumentParser, *, timeout: int, ttl: int) -> None:
    p.add_argument("--timeout", type=int, default=timeout)
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--cache-ttl", type=int, default=ttl)
    p.add_argument("--no-cache", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="HTSC OpenClaw raw/semi-structured data adapter")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="Resolve stock name/code via HTSC searchStock")
    p.add_argument("--query", required=True)
    add_common(p, timeout=30, ttl=86400)

    p = sub.add_parser("quote", help="Get structured A-share quote via HTSC getQuote")
    p.add_argument("--code", default="")
    p.add_argument("--exchange", default="")
    p.add_argument("--query", default="", help="Resolve name first if code not provided")
    add_common(p, timeout=30, ttl=300)

    p = sub.add_parser("quotes", help="Get multiple A-share quotes, comma-separated canonical codes")
    p.add_argument("--codes", required=True, help="e.g. 000725.SZ,601208.SH")
    add_common(p, timeout=30, ttl=300)

    p = sub.add_parser("watchlist", help="Get HTSC watchlist first 20 items")
    p.add_argument("--query", default="查看我的自选股")
    add_common(p, timeout=60, ttl=1800)

    p = sub.add_parser("watchlist-quotes", help="Fetch quotes for visible A-share watchlist items")
    p.add_argument("--limit", type=int, default=20)
    add_common(p, timeout=30, ttl=300)

    p = sub.add_parser("select", help="Run selectStock and parse markdown tables")
    p.add_argument("--query", required=True)
    add_common(p, timeout=300, ttl=6 * 3600)

    p = sub.add_parser("indicator", help="Run queryIndicator and parse markdown tables if present")
    p.add_argument("--query", required=True)
    add_common(p, timeout=90, ttl=1800)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    dispatch = {
        "search": cmd_search,
        "quote": cmd_quote,
        "quotes": cmd_quotes,
        "watchlist": cmd_watchlist,
        "watchlist-quotes": cmd_watchlist_quotes,
        "select": cmd_select,
        "indicator": cmd_indicator,
    }
    result = dispatch[args.cmd](args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
