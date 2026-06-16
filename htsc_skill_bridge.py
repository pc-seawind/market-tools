#!/usr/bin/env python3
"""htsc_skill_bridge.py — deterministic bridge for Huatai OpenClaw skills.

Purpose
-------
Cron agents need a safe, one-command way to use the installed Huatai OpenClaw
skills as:

1. backup/reference data source (small-scope quote/valuation/fund-flow queries),
2. HTSC second opinion for morning/evening/weekend market reports,
3. narrative/thesis seed generator that still requires source verification.

This bridge intentionally does NOT make HTSC skills the primary structured data
source for the scoring model. Tushare/akshare/local scripts remain the
reproducible machine-data layer. HTSC skills mostly return natural-language or
Markdown answers, so this script wraps them with retry/cache/JSON metadata and
lets downstream cron reports quote them explicitly as "HTSC reference".

Usage
-----
  python3 htsc_skill_bridge.py market-insight --query "今日A股科技成长方向..."
  python3 htsc_skill_bridge.py query-indicator --query "京东方A今天涨跌幅和PE/PB"
  python3 htsc_skill_bridge.py select-stock --query "最近5日主力净流入..." --timeout 300
  python3 htsc_skill_bridge.py watchlist --json

  # common cron-oriented prompts
  python3 htsc_skill_bridge.py evening-reference
  python3 htsc_skill_bridge.py morning-reference
  python3 htsc_skill_bridge.py sunday-preview-reference
  python3 htsc_skill_bridge.py narrative-seeds

Output
------
Default is wrapper JSON:
  {
    "ok": true/false,
    "source": "htsc_openclaw",
    "command": "market-insight",
    "skill": "financial-analysis",
    "tool": "marketInsight",
    "query": "...",
    "cached": false,
    "generated_at": "...",
    "text": "...",          # data.answer / data.result when present
    "raw": {...},            # original skill JSON
    "error": null/{...},
    "attempts": [...]
  }

With --markdown, prints only the answer/result text (or an error block).

Security
--------
Never prints HT_APIKEY. Skill scripts read HT_APIKEY from env or
~/.htsc-skills/config themselves.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / ".cron_state"
CACHE_DIR = STATE_DIR / "htsc_skill_cache"
HTSC_SKILLS_DIR = Path(os.environ.get("HTSC_SKILLS_DIR", Path.home() / ".homespace" / "skills"))

SCRIPT_MAP = {
    "market-insight": {
        "skill": "financial-analysis",
        "script": HTSC_SKILLS_DIR / "financial-analysis" / "financial_analysis.py",
        "tool": "marketInsight",
        "text_fields": ("answer", "result"),
        "timeout": 120,
    },
    "diagnosis-stock": {
        "skill": "financial-analysis",
        "script": HTSC_SKILLS_DIR / "financial-analysis" / "financial_analysis.py",
        "tool": "diagnosisStock",
        "text_fields": ("answer", "result"),
        "timeout": 120,
    },
    "query-indicator": {
        "skill": "query-indicator",
        "script": HTSC_SKILLS_DIR / "query-indicator" / "query_indicator.py",
        "tool": "queryIndicator",
        "text_fields": ("answer", "result"),
        "timeout": 90,
    },
    "select-stock": {
        "skill": "select-stock",
        "script": HTSC_SKILLS_DIR / "select-stock" / "select_stock.py",
        "tool": "selectStock",
        "text_fields": ("result", "answer"),
        # Select-stock can be slow; keep cron callers explicit but safe.
        "timeout": 300,
    },
    "watchlist": {
        "skill": "watchlist-management",
        "script": HTSC_SKILLS_DIR / "watchlist-management" / "watchlist_management.py",
        "tool": "getWatchlist",
        "text_fields": ("result", "answer"),
        "timeout": 60,
        "default_query": "查看我的自选股",
    },
}

COMMON_QUERIES = {
    "evening-reference": (
        "market-insight",
        "总结今日A股主要板块轮动、资金偏好、科技成长/红利/消费/金融方向的利好利空和风险点。"
        "重点标注与AI算力、半导体、消费电子、光模块、银行、红利资产相关的资金变化和风险。"
    ),
    "morning-reference": (
        "market-insight",
        "今日A股盘前需要关注的政策、产业、宏观和海外事件有哪些？"
        "请说明对AI算力、半导体、消费电子、金融、红利方向的潜在影响和主要风险。"
    ),
    "sunday-preview-reference": (
        "market-insight",
        "展望下周A股科技成长、红利、消费、金融、周期方向的关键变量、资金偏好和风险点。"
        "请给出可用于周度操作计划的外部参考观点，但不要直接给交易指令。"
    ),
    "narrative-seeds": (
        "market-insight",
        "最近24小时A股AI算力、半导体、消费电子、机器人、低空经济、红利资产的产业叙事变化、"
        "政策催化、利好利空和可能受益/受损股票。请按叙事线索列出，供后续搜索原文验证。"
    ),
}

CN_TZ = dt.timezone(dt.timedelta(hours=8))


def now_iso() -> str:
    return dt.datetime.now(CN_TZ).isoformat(timespec="seconds")


def cache_key(payload: dict[str, Any]) -> str:
    s = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def load_cache(key: str, ttl_sec: int) -> dict[str, Any] | None:
    p = cache_path(key)
    if ttl_sec <= 0 or not p.exists():
        return None
    try:
        age = time.time() - p.stat().st_mtime
        if age > ttl_sec:
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        data["cached"] = True
        data["cache_age_sec"] = round(age, 1)
        return data
    except Exception:
        return None


def save_cache(key: str, result: dict[str, Any]) -> None:
    # Cache successful skill results only. Errors are usually transient and should
    # not poison tomorrow's cron report.
    if not result.get("ok"):
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = cache_path(key)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def extract_text(raw: dict[str, Any], text_fields: tuple[str, ...]) -> str:
    data = raw.get("data")
    if isinstance(data, dict):
        for f in text_fields:
            v = data.get(f)
            if isinstance(v, str) and v.strip():
                return v
        # Some watchlist calls put nested structures under answer.
        if data:
            return json.dumps(data, ensure_ascii=False, indent=2)
    if isinstance(data, str):
        return data
    return ""


def is_retriable(raw: dict[str, Any], rc: int = 0) -> bool:
    if rc != 0:
        return True
    err = raw.get("error") or {}
    cat = str(err.get("category") or "").lower()
    msg = str(err.get("message") or "").lower()
    return cat in {"network", "timeout", "process", "decode"} or "超时" in msg or "timeout" in msg


def run_skill(command: str, query: str, *, timeout: int, retries: int, cache_ttl: int, no_cache: bool) -> dict[str, Any]:
    spec = SCRIPT_MAP[command]
    script = Path(spec["script"])
    tool = str(spec["tool"])
    payload = {
        "v": 1,
        "command": command,
        "skill": spec["skill"],
        "tool": tool,
        "query": query,
        "script": str(script),
    }
    ckey = cache_key(payload)
    if not no_cache:
        cached = load_cache(ckey, cache_ttl)
        if cached is not None:
            return cached

    attempts: list[dict[str, Any]] = []
    base = {
        "ok": False,
        "source": "htsc_openclaw",
        "command": command,
        "skill": spec["skill"],
        "tool": tool,
        "query": query,
        "cached": False,
        "cache_key": ckey[:12],
        "generated_at": now_iso(),
        "script": str(script),
        "text": "",
        "raw": None,
        "error": None,
        "attempts": attempts,
    }

    if not script.exists():
        base["error"] = {"category": "missing_script", "message": f"skill script not found: {script}"}
        return base

    cmd = ["python3", str(script), tool, "--query", query]
    last: dict[str, Any] = {}
    for i in range(max(0, retries) + 1):
        started = time.time()
        attempt: dict[str, Any] = {"n": i + 1, "started_at": now_iso(), "timeout": timeout}
        try:
            cp = subprocess.run(cmd, cwd=str(script.parent), capture_output=True, text=True, timeout=timeout)
            elapsed = round(time.time() - started, 2)
            attempt.update({"rc": cp.returncode, "elapsed_sec": elapsed})
            if cp.returncode != 0:
                raw = {
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
                    raw = json.loads(cp.stdout)
                except json.JSONDecodeError as e:
                    raw = {
                        "ok": False,
                        "data": None,
                        "error": {
                            "category": "decode",
                            "message": str(e),
                            "stdout_head": (cp.stdout or "")[:800],
                            "stderr_tail": (cp.stderr or "")[-500:],
                        },
                    }
            attempt["ok"] = bool(raw.get("ok"))
            attempt["error_category"] = (raw.get("error") or {}).get("category")
            attempts.append(attempt)
            last = raw
            if raw.get("ok") or not is_retriable(raw, attempt.get("rc", 0)):
                break
        except subprocess.TimeoutExpired:
            elapsed = round(time.time() - started, 2)
            raw = {"ok": False, "data": None, "error": {"category": "network", "message": f"timeout after {timeout}s"}}
            attempt.update({"rc": None, "elapsed_sec": elapsed, "ok": False, "error_category": "network"})
            attempts.append(attempt)
            last = raw
        if i < retries:
            time.sleep(min(2 + i * 2, 8))

    result = dict(base)
    result["ok"] = bool(last.get("ok"))
    result["raw"] = last
    result["error"] = last.get("error")
    result["text"] = extract_text(last, tuple(spec["text_fields"])) if last else ""
    save_cache(ckey, result)
    return result


def print_result(result: dict[str, Any], *, markdown: bool) -> None:
    if markdown:
        if result.get("ok"):
            print(result.get("text") or "")
        else:
            err = result.get("error") or {}
            print(f"⚠️ HTSC skill failed: {err.get('category') or 'unknown'} — {err.get('message') or ''}")
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


def add_common_args(p: argparse.ArgumentParser, *, default_timeout: int, default_ttl: int = 6 * 3600) -> None:
    p.add_argument("--query", default="", help="Query to pass through unchanged to the HTSC skill")
    p.add_argument("--timeout", type=int, default=default_timeout, help="Per-attempt timeout seconds")
    p.add_argument("--retries", type=int, default=1, help="Retries after first failed attempt")
    p.add_argument("--cache-ttl", type=int, default=default_ttl, help="Cache TTL seconds; 0 disables cache")
    p.add_argument("--no-cache", action="store_true", help="Bypass cache and do not read old results")
    p.add_argument("--markdown", action="store_true", help="Print answer/result text only")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Bridge Huatai OpenClaw skills for market-tools cron agents")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, spec in SCRIPT_MAP.items():
        p = sub.add_parser(name, help=f"Call {spec['skill']} / {spec['tool']}")
        add_common_args(p, default_timeout=int(spec["timeout"]))

    for name, (base_cmd, q) in COMMON_QUERIES.items():
        spec = SCRIPT_MAP[base_cmd]
        p = sub.add_parser(name, help=f"Cron preset via {base_cmd}")
        p.add_argument("--query", default=q, help="Override preset query")
        p.add_argument("--timeout", type=int, default=int(spec["timeout"]), help="Per-attempt timeout seconds")
        p.add_argument("--retries", type=int, default=1, help="Retries after first failed attempt")
        p.add_argument("--cache-ttl", type=int, default=6 * 3600, help="Cache TTL seconds")
        p.add_argument("--no-cache", action="store_true", help="Bypass cache")
        p.add_argument("--markdown", action="store_true", help="Print answer/result text only")

    return ap


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    cmd = args.cmd
    if cmd in COMMON_QUERIES:
        base_cmd, default_q = COMMON_QUERIES[cmd]
        command = base_cmd
        query = args.query or default_q
    else:
        command = cmd
        query = args.query or str(SCRIPT_MAP[command].get("default_query") or "")
    if not query:
        ap.error(f"{cmd}: --query is required")
    result = run_skill(
        command,
        query,
        timeout=args.timeout,
        retries=args.retries,
        cache_ttl=args.cache_ttl,
        no_cache=args.no_cache,
    )
    print_result(result, markdown=args.markdown)
    # Exit non-zero only for deterministic process use. Cron agents can still read
    # JSON error payload from stdout.
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
