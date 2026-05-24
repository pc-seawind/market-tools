"""
narrative_backfill_pubdate.py
=============================
回填 narrative_events.jsonl 中每条 event 的 pub_date (新闻原始发布日期).

为啥需要:
  原 schema 用 trade_date = "我收集那天" (cron 跑那天) 做 baseline.
  但很多新闻是 1-2 周前发的, baseline 错了 → T+N 验证失真.
  正确 baseline = 新闻发布当日 close (或下一个交易日 open).

策略:
  1. URL path 带日期 (ebrun/20260522, sina/2026-05-22) → 直接 regex
  2. baidu/sohu 等聚合页面 → 不可靠, 用 LLM 反推 + 手工 review

用法:
  python3 narrative_backfill_pubdate.py extract  # 自动提一遍能解析的
  python3 narrative_backfill_pubdate.py review   # 列出待人工 review 的
  python3 narrative_backfill_pubdate.py apply --code <line_idx> --date YYYYMMDD
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

CN_TZ = dt.timezone(dt.timedelta(hours=8))
ROOT = Path(__file__).resolve().parent
EVENTS_PATH = ROOT / "narrative_events.jsonl"


# ---------- url-based extractors ----------

def extract_from_url(url: str) -> str | None:
    """从 URL path 直接提日期 — 仅适用部分站点."""
    # ebrun: /20260522/669291.shtml
    m = re.search(r"/(\d{4})(\d{2})(\d{2})/", url)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    # sina finance: /2026-05-22/
    m = re.search(r"/(\d{4})-(\d{2})-(\d{2})/", url)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    # sohu: /a/1023888221_121124362 — ID 不是日期编码, 跳过
    # baidu baijiahao: ID 不是简单 snowflake, 跳过 (需走 page fetch)
    # nomadsemi/wccftech/baike: 路径无日期
    return None


# ---------- ops ----------

def load_events() -> list[dict]:
    if not EVENTS_PATH.exists():
        return []
    return [json.loads(l) for l in EVENTS_PATH.read_text().splitlines() if l.strip()]


def save_events(events: list[dict]) -> None:
    """原子写回: tmp → rename."""
    tmp = EVENTS_PATH.with_suffix(".jsonl.tmp")
    with tmp.open("w") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    tmp.rename(EVENTS_PATH)


# ---------- commands ----------

def cmd_extract(args):
    events = load_events()
    auto_filled = 0
    pending = []

    for i, e in enumerate(events):
        if e.get("pub_date"):
            continue
        d = extract_from_url(e["url"])
        if d:
            e["pub_date"] = d
            e["pub_date_source"] = "url_path"
            auto_filled += 1
        else:
            pending.append((i, e))

    save_events(events)
    print(f"✅ url_path: {auto_filled} 个 pub_date 自动填上")
    print(f"⏳ pending review: {len(pending)} 个 (用 review 命令查看)")


def cmd_review(args):
    events = load_events()
    pending = [(i, e) for i, e in enumerate(events) if not e.get("pub_date")]
    if not pending:
        print("✅ 所有 events 已有 pub_date.")
        return

    print(f"# {len(pending)} 个 events 缺 pub_date — 请人工 review")
    print()
    for i, e in pending:
        print(f"## [{i}] td={e['trade_date']} s={e['score']}")
        print(f"   title: {e['title']}")
        print(f"   url:   {e['url']}")
        print(f"   apply: python3 narrative_backfill_pubdate.py apply --idx {i} --date YYYYMMDD")
        print()


def cmd_apply(args):
    events = load_events()
    if args.idx < 0 or args.idx >= len(events):
        print(f"❌ idx {args.idx} 越界 (0..{len(events)-1})")
        return 1
    if not re.fullmatch(r"\d{8}", args.date):
        print(f"❌ date 必须是 YYYYMMDD")
        return 1
    e = events[args.idx]
    e["pub_date"] = args.date
    e["pub_date_source"] = args.source or "manual"
    save_events(events)
    print(f"✅ events[{args.idx}].pub_date = {args.date} ({e.get('pub_date_source')})")
    print(f"   title: {e['title']}")
    return 0


def cmd_apply_batch(args):
    """批量从 stdin 读 idx,date,source 行."""
    events = load_events()
    n = 0
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        idx = int(parts[0])
        date = parts[1]
        source = parts[2] if len(parts) > 2 else "manual"
        if not re.fullmatch(r"\d{8}", date):
            print(f"⚠️ skip bad date {date} for idx={idx}")
            continue
        if idx < 0 or idx >= len(events):
            print(f"⚠️ skip oob idx {idx}")
            continue
        events[idx]["pub_date"] = date
        events[idx]["pub_date_source"] = source
        n += 1
    save_events(events)
    print(f"✅ batch applied: {n} events updated")


def cmd_status(args):
    events = load_events()
    have = sum(1 for e in events if e.get("pub_date"))
    by_source = {}
    pub_date_counts = {}
    for e in events:
        if e.get("pub_date"):
            by_source[e.get("pub_date_source", "unknown")] = by_source.get(e.get("pub_date_source", "unknown"), 0) + 1
            d = e["pub_date"]
            pub_date_counts[d] = pub_date_counts.get(d, 0) + 1
    print(f"events={len(events)} have_pub_date={have} pending={len(events)-have}")
    print(f"by source: {by_source}")
    print(f"pub_dates: {dict(sorted(pub_date_counts.items()))}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("extract").set_defaults(func=cmd_extract)
    sub.add_parser("review").set_defaults(func=cmd_review)
    sub.add_parser("status").set_defaults(func=cmd_status)

    p_apply = sub.add_parser("apply")
    p_apply.add_argument("--idx", type=int, required=True)
    p_apply.add_argument("--date", required=True, help="YYYYMMDD")
    p_apply.add_argument("--source", default="manual")
    p_apply.set_defaults(func=cmd_apply)

    sub.add_parser("apply-batch").set_defaults(func=cmd_apply_batch)

    args = ap.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
