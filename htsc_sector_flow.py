#!/usr/bin/env python3
"""htsc_sector_flow.py — cache HTSC sector main-fund-flow for scoring fallback.

Why
---
Tushare `moneyflow_ind_dc` is the original primary sector-fund-flow source but
may be unavailable/no-permission. The earlier THS/AkShare fallback used
`stock_fund_flow_* 5日排行` total net-flow, which was audited on 2026-06-17 and
found to conflict with HTSC/OpenClaw "主力净流入" for hot semiconductor/CPO/PCB
sectors. Since the model wants *main-money* direction, HTSC is the preferred
fallback; THS should not drive scoring by default.

This script refreshes a JSON cache that `bk_moneyflow.py` can read cheaply. Do
network calls here (cron detached stage), not inside `sector_score --all`.

Usage
-----
  python3 htsc_sector_flow.py refresh --concept "光通信 (光模块/CPO)"
  python3 htsc_sector_flow.py refresh-default --max-concepts 20
  python3 htsc_sector_flow.py get --concept "光通信 (光模块/CPO)"
  python3 htsc_sector_flow.py list

Cache
-----
  .cron_state/htsc_sector_flow.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / ".cron_state"
CACHE_FILE = STATE_DIR / "htsc_sector_flow.json"
HTSC_RAW = HERE / "htsc_raw_data.py"
CN_TZ = dt.timezone(dt.timedelta(hours=8))

# Only labels that HTSC understands well. Multiple labels are averaged per concept
# to avoid double-counting overlapping concepts.
HTSC_FLOW_ALIASES: dict[str, list[str]] = {
    "AI芯片 (算力核心)": ["半导体", "芯片概念"],
    "存储芯片 (HBM/DDR/NAND)": ["存储芯片", "半导体"],
    # Use the closest HTSC label only. Do not average with broad industries here:
    # e.g. CPO + 通信设备 can flip the sign and recreate the THS口径问题.
    "先进封装 (CoWoS/Chiplet)": ["先进封装"],
    "光通信 (光模块/CPO)": ["CPO概念"],
    "英伟达产业链": ["半导体", "通信设备"],
    "PCB (算力基建)": ["PCB"],
    "硅片 (半导体材料)": ["半导体"],
    "半导体设备 (国产替代)": ["半导体"],
    "金融-银行": ["银行"],
    "金融-证券": ["证券"],
    "金融-保险": ["保险"],
    "锂电产业链": ["锂电池", "电池"],
    "新能源车": ["新能源汽车"],
    "固态电池": ["固态电池"],
    "游戏传媒": ["游戏", "传媒"],
    "有色金属": ["有色金属"],
    "化工": ["化工"],
}

DEFAULT_CONCEPTS = [
    "AI芯片 (算力核心)",
    "存储芯片 (HBM/DDR/NAND)",
    "先进封装 (CoWoS/Chiplet)",
    "光通信 (光模块/CPO)",
    "英伟达产业链",
    "PCB (算力基建)",
    "半导体设备 (国产替代)",
    "硅片 (半导体材料)",
    "金融-证券",
    "金融-银行",
    "锂电产业链",
    "固态电池",
    "化工",
]


def now_iso() -> str:
    return dt.datetime.now(CN_TZ).isoformat(timespec="seconds")


def load_cache() -> dict[str, Any]:
    if not CACHE_FILE.exists():
        return {"schema_version": 1, "updated_at": None, "concepts": {}}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": 1, "updated_at": None, "concepts": {}}


def save_cache(cache: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cache["updated_at"] = now_iso()
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE_FILE)


def parse_amount_yi(text: str) -> tuple[float | None, float | None]:
    """Return (flow_yi, pct) from HTSC short answer."""
    t = (text or "").replace("，", ",").replace("％", "%")
    # Prefer patterns near 主力净流入/净流出合计.
    flow = None
    patterns = [
        r"主力净流入(?:合计)?(?:为|是)?\s*([-+]?\d+(?:\.\d+)?)\s*亿元",
        r"主力资金净流入(?:合计)?(?:为|是)?\s*([-+]?\d+(?:\.\d+)?)\s*亿元",
        r"净流入(?:合计)?(?:为|是)?\s*([-+]?\d+(?:\.\d+)?)\s*亿元",
        r"主力净流出(?:合计)?(?:为|是)?\s*([-+]?\d+(?:\.\d+)?)\s*亿元",
        r"净流出(?:合计)?(?:为|是)?\s*([-+]?\d+(?:\.\d+)?)\s*亿元",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if m:
            flow = float(m.group(1))
            if "净流出" in pat and flow > 0:
                flow = -flow
            break
    # If text says 主力净流入合计-970.86亿元, regex above handles negative.
    pct = None
    m = re.search(r"涨跌幅(?:为|是)?\s*([-+]?\d+(?:\.\d+)?)\s*%", t)
    if not m:
        m = re.search(r"区间涨幅(?:为|是)?\s*([-+]?\d+(?:\.\d+)?)\s*%", t)
    if m:
        pct = float(m.group(1))
    return flow, pct


def call_indicator(label: str, days: int, *, timeout: int, retries: int) -> dict[str, Any]:
    q = f"最近{days}个交易日{label}板块主力净流入合计和涨跌幅是多少？只回答金额和涨跌幅"
    cmd = ["python3", str(HTSC_RAW), "indicator", "--query", q, "--timeout", str(timeout), "--retries", str(retries), "--cache-ttl", "21600"]
    started = time.time()
    try:
        cp = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True, timeout=timeout + 20)
    except subprocess.TimeoutExpired:
        return {"ok": False, "query": q, "error": {"category": "timeout", "message": f"timeout after {timeout + 20}s"}}
    elapsed = round(time.time() - started, 2)
    if cp.returncode != 0:
        try:
            raw = json.loads(cp.stdout)
        except Exception:
            raw = {"ok": False, "error": {"category": "process", "message": f"exit {cp.returncode}", "stderr_tail": cp.stderr[-500:]}}
        return {"ok": False, "query": q, "elapsed_sec": elapsed, "raw": raw, "error": raw.get("error")}
    raw = json.loads(cp.stdout)
    text = ((raw.get("data") or {}).get("text") or "")
    flow_yi, pct = parse_amount_yi(text)
    return {
        "ok": bool(raw.get("ok") and flow_yi is not None),
        "query": q,
        "elapsed_sec": elapsed,
        "text": text,
        "flow_yi": flow_yi,
        "pct": pct,
        "raw_ok": raw.get("ok"),
        "error": None if flow_yi is not None else {"category": "parse", "message": "failed to parse flow_yi", "text_head": text[:300]},
    }


def mean(vals: list[float | None]) -> float | None:
    xs = [v for v in vals if v is not None]
    return sum(xs) / len(xs) if xs else None


def refresh_concept(concept: str, *, timeout: int, retries: int, sleep_sec: float) -> dict[str, Any]:
    labels = HTSC_FLOW_ALIASES.get(concept)
    if not labels:
        return {"ok": False, "concept": concept, "error": {"category": "no_mapping", "message": "no HTSC labels"}}
    entries = []
    for label in labels:
        e: dict[str, Any] = {"label": label}
        r5 = call_indicator(label, 5, timeout=timeout, retries=retries)
        if sleep_sec:
            time.sleep(sleep_sec)
        r20 = call_indicator(label, 20, timeout=timeout, retries=retries)
        if sleep_sec:
            time.sleep(sleep_sec)
        e.update({"5d": r5, "20d": r20})
        entries.append(e)
    f5 = mean([e["5d"].get("flow_yi") for e in entries if e.get("5d")])
    f20 = mean([e["20d"].get("flow_yi") for e in entries if e.get("20d")])
    pct5 = mean([e["5d"].get("pct") for e in entries if e.get("5d")])
    pct20 = mean([e["20d"].get("pct") for e in entries if e.get("20d")])
    ok = f5 is not None or f20 is not None
    return {
        "ok": ok,
        "concept": concept,
        "labels": labels,
        "updated_at": now_iso(),
        "source": "htsc_openclaw_query_indicator",
        "flow_5d_cny": f5 * 1e8 if f5 is not None else None,
        "flow_20d_cny": f20 * 1e8 if f20 is not None else None,
        "pct_5d": pct5,
        "pct_20d": pct20,
        "entries": entries,
        "error": None if ok else {"category": "no_data", "message": "all labels failed"},
    }


def is_fresh(rec: dict[str, Any], ttl_hours: float) -> bool:
    try:
        t = dt.datetime.fromisoformat(rec.get("updated_at", ""))
        if t.tzinfo is None:
            t = t.replace(tzinfo=CN_TZ)
        return (dt.datetime.now(CN_TZ) - t).total_seconds() <= ttl_hours * 3600
    except Exception:
        return False


def cmd_refresh(args) -> dict[str, Any]:
    cache = load_cache()
    rec = refresh_concept(args.concept, timeout=args.timeout, retries=args.retries, sleep_sec=args.sleep)
    cache.setdefault("concepts", {})[args.concept] = rec
    save_cache(cache)
    return rec


def cmd_refresh_default(args) -> dict[str, Any]:
    cache = load_cache()
    concepts = DEFAULT_CONCEPTS[: args.max_concepts] if args.max_concepts else list(DEFAULT_CONCEPTS)
    results = []
    for c in concepts:
        existing = (cache.get("concepts") or {}).get(c)
        if existing and not args.force and is_fresh(existing, args.ttl_hours):
            results.append({"concept": c, "skipped": True, "reason": "fresh", "updated_at": existing.get("updated_at"), "ok": existing.get("ok")})
            continue
        rec = refresh_concept(c, timeout=args.timeout, retries=args.retries, sleep_sec=args.sleep)
        cache.setdefault("concepts", {})[c] = rec
        save_cache(cache)
        results.append({"concept": c, "ok": rec.get("ok"), "error": rec.get("error"), "updated_at": rec.get("updated_at")})
    return {"ok": True, "updated_at": now_iso(), "cache_file": str(CACHE_FILE), "results": results}


def cmd_get(args) -> dict[str, Any]:
    cache = load_cache()
    rec = (cache.get("concepts") or {}).get(args.concept)
    if not rec:
        return {"ok": False, "concept": args.concept, "error": {"category": "not_found", "message": "not in cache"}}
    rec = dict(rec)
    rec["fresh"] = is_fresh(rec, args.ttl_hours)
    return rec


def cmd_list(args) -> dict[str, Any]:
    cache = load_cache()
    rows = []
    for c, rec in sorted((cache.get("concepts") or {}).items()):
        rows.append({
            "concept": c,
            "ok": rec.get("ok"),
            "updated_at": rec.get("updated_at"),
            "fresh": is_fresh(rec, args.ttl_hours),
            "flow_5d_cny": rec.get("flow_5d_cny"),
            "flow_20d_cny": rec.get("flow_20d_cny"),
            "pct_5d": rec.get("pct_5d"),
            "pct_20d": rec.get("pct_20d"),
        })
    return {"ok": True, "cache_file": str(CACHE_FILE), "count": len(rows), "rows": rows}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Refresh/read HTSC sector main-fund-flow cache")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("refresh")
    p.add_argument("--concept", required=True)
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--sleep", type=float, default=0.5)
    p = sub.add_parser("refresh-default")
    p.add_argument("--max-concepts", type=int, default=0, help="0 = all defaults")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--sleep", type=float, default=0.5)
    p.add_argument("--ttl-hours", type=float, default=12)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("get")
    p.add_argument("--concept", required=True)
    p.add_argument("--ttl-hours", type=float, default=24)
    p = sub.add_parser("list")
    p.add_argument("--ttl-hours", type=float, default=24)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    fn = {"refresh": cmd_refresh, "refresh-default": cmd_refresh_default, "get": cmd_get, "list": cmd_list}[args.cmd]
    out = fn(args)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(0 if out.get("ok") else 1)


if __name__ == "__main__":
    main()
