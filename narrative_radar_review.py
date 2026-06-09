"""narrative_radar_review.py — 自动复盘脚本 (cross-period attribution review)

用法:
    python3 narrative_radar_review.py                # 默认 D14-28 主窗口
    python3 narrative_radar_review.py --window 7-14  # 短窗口复盘 (W21 看 W22)
    python3 narrative_radar_review.py --window 28-42 # 长窗口 (滞后效应)
    python3 narrative_radar_review.py --since 2026-04-01  # 限定 events 起点
    python3 narrative_radar_review.py --hypothesis-check  # 跑 v3 三大 hypothesis 验算

读 narrative_events.jsonl (含 features._human_labeled) + narrative_perf.jsonl,
join 后跑 cross-tab + hypothesis 1/2/3 验算, 输出 markdown 报告片段。

设计原则:
- 不要依赖 _human_labels.py / features_v2.jsonl 这种一次性脚本; 直接读入库文件
- 跳过 features._human_labeled != True 的 event (heuristic 准确率仅 74%, 不可信)
- 输出可直接 paste 到 weekly review 报告里
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
EVENTS_PATH = _HERE / "narrative_events.jsonl"
PERF_PATH = _HERE / "narrative_perf.jsonl"


def parse_window(s: str) -> tuple[int, int]:
    """e.g. '14-28' → (14, 28)."""
    a, b = s.split("-")
    return int(a), int(b)


def load_events(since: str | None) -> dict:
    """Return {event_ts: event_dict}. Only events with _human_labeled=True."""
    out = {}
    skipped_unlabeled = 0
    skipped_before = 0
    with open(EVENTS_PATH, encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            ts = e.get("ts", "")
            if since and ts < since:
                skipped_before += 1
                continue
            feat = e.get("features", {})
            if not feat.get("_human_labeled"):
                skipped_unlabeled += 1
                continue
            out[ts] = e
    if skipped_unlabeled:
        print(f"⚠️  跳过 {skipped_unlabeled} 个未人工打标的 events (heuristic 准确率 74% 不可信)", file=sys.stderr)
    if skipped_before:
        print(f"   跳过 {skipped_before} 个 since 之前的 events", file=sys.stderr)
    return out


def load_perf_window(d_lo: int, d_hi: int) -> list:
    """Return perf rows where d_lo <= days_since_event <= d_hi."""
    out = []
    with open(PERF_PATH, encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            d = p.get("days_since_event", 0)
            if d_lo <= d <= d_hi:
                out.append(p)
    return out


def to_unique_pairs(perf_rows: list) -> dict:
    """Latest verify_date per (event_ts, code)."""
    pairs = {}
    for p in perf_rows:
        key = (p.get("event_ts"), p.get("code"))
        vd = p.get("verify_date", "")
        if key not in pairs or vd > pairs[key].get("verify_date", ""):
            pairs[key] = p
    return pairs


def join_events_perf(events: dict, pairs: dict) -> list:
    """Join: each pair gets event features attached. Drop pairs with no feature."""
    joined = []
    dropped = 0
    for (et, code), p in pairs.items():
        if et not in events:
            dropped += 1
            continue
        feat = events[et].get("features", {})
        joined.append({
            "event_ts": et,
            "event_title": (p.get("event_title") or events[et].get("title", ""))[:60],
            "code": code,
            "name": p.get("name"),
            "subdomain": p.get("event_subdomain"),
            "score": p.get("event_score"),
            "hit_strict": p.get("hit_strict"),
            "hit": p.get("hit"),
            "hit_vs_sector": p.get("hit_vs_sector"),
            "excess_vs_sector": p.get("excess_vs_sector"),
            "ticker_layer": p.get("ticker_layer"),
            **{k: v for k, v in feat.items() if not k.startswith("_")},
        })
    if dropped:
        print(f"   join 时丢弃 {dropped} pairs (event 不在打标集)", file=sys.stderr)
    return joined


def crosstab(joined: list, field: str, baseline: float, label: str = "") -> list:
    """Return list of (value, n, strict_n, rate, lift) tuples sorted by lift desc."""
    label = label or field
    bucket = defaultdict(list)
    for j in joined:
        v = j.get(field)
        bucket[v].append(j)
    rows = []
    for v, items in bucket.items():
        n = len(items)
        sn = sum(1 for it in items if it["hit_strict"])
        rate = sn / n if n else 0
        lift = rate - baseline
        rows.append((v, n, sn, rate, lift))
    rows.sort(key=lambda r: -r[3])
    return rows


def print_crosstab_table(field: str, rows: list):
    print(f"\n=== {field} ===")
    print(f"  {'value':<22} {'n':>4} {'strict_n':>10} {'strict_rate':>14} {'lift':>10}")
    for v, n, sn, rate, lift in rows:
        sign = "+" if lift >= 0 else ""
        print(f"  {str(v):<22} {n:>4} {sn:>10} {rate:>13.0%} {sign}{lift*100:>8.1f}pp")


def emit_markdown_table(field: str, rows: list, baseline: float) -> str:
    """Generate markdown table for one cross-tab dimension."""
    lines = [f"### {field}", ""]
    lines.append(f"| value | n | strict_n | strict_rate | lift vs baseline ({baseline:.0%}) |")
    lines.append("|---|---:|---:|---:|---:|")
    for v, n, sn, rate, lift in rows:
        sign = "+" if lift >= 0 else ""
        marker = "✅" if rate >= baseline + 0.20 else ("❌" if rate <= baseline - 0.20 else "")
        lines.append(f"| `{v}` | {n} | {sn} | {rate:.0%} | {sign}{lift*100:.1f}pp {marker} |")
    return "\n".join(lines) + "\n"


def hypothesis_check(joined: list, baseline: float):
    """Validate v3 report 6 hypotheses against current data."""
    print("\n\n## Hypothesis 验算 (v3 报告六条)\n")

    h_results = []

    # H1: upstream/narrow/m_a_capex 100% strict
    for field, target_value, label in [
        ("supply_chain_position", "upstream", "H1a: upstream → 高 strict_rate"),
        ("specificity", "narrow", "H1b: narrow → 高 strict_rate"),
        ("catalyst_type", "m_a_capex", "H1c: m_a_capex → 高 strict_rate"),
    ]:
        items = [j for j in joined if j.get(field) == target_value]
        n = len(items)
        sn = sum(1 for j in items if j["hit_strict"])
        rate = sn / n if n else 0
        verdict = "✅ 持续" if (n >= 3 and rate >= baseline + 0.20) else (
            "⚠️ 样本不足" if n < 3 else "❌ 失效"
        )
        h_results.append((label, n, sn, rate, verdict))
        print(f"  {label}: n={n}, strict={sn}, rate={rate:.0%} → {verdict}")

    # H2: broad/cross_domain/policy/concept/domestic_demand 25% strict (反向)
    for field, target_value, label in [
        ("specificity", "broad", "H2a: broad → 低 strict_rate"),
        ("cross_domain", True, "H2b: cross_domain=True → 低 strict_rate"),
        ("catalyst_type", "policy", "H2c: policy → 低 strict_rate"),
        ("catalyst_type", "concept", "H2d: concept → 低 strict_rate"),
        ("driver_origin", "domestic_demand", "H2e: domestic_demand → 低 strict_rate"),
    ]:
        items = [j for j in joined if j.get(field) == target_value]
        n = len(items)
        sn = sum(1 for j in items if j["hit_strict"])
        rate = sn / n if n else 0
        verdict = "✅ 持续" if (n >= 3 and rate <= baseline - 0.20) else (
            "⚠️ 样本不足" if n < 3 else "❌ 失效"
        )
        h_results.append((label, n, sn, rate, verdict))
        print(f"  {label}: n={n}, strict={sn}, rate={rate:.0%} → {verdict}")

    # H4: ticker_layer 单调性 (core_pure/core_partial > satellite > concept_only)
    print("\n## H4: ticker_layer 单调性验算\n")
    for layer, label in [
        ("core_pure", "H4a: core_pure → 高 strict_rate"),
        ("core_partial", "H4b: core_partial → 中 strict_rate"),
        ("satellite", "H4c: satellite → 低 strict_rate"),
        ("concept_only", "H4d: concept_only → 极低 strict_rate"),
    ]:
        items = [j for j in joined if j.get("ticker_layer") == layer]
        n = len(items)
        sn = sum(1 for j in items if j["hit_strict"])
        rate = sn / n if n else 0
        if layer in ("core_pure", "core_partial"):
            verdict = "✅ 持续" if (n >= 3 and rate >= baseline) else (
                "⚠️ 样本不足" if n < 3 else "❌ 失效"
            )
        else:  # satellite/concept_only 应反向
            verdict = "✅ 持续" if (n >= 3 and rate <= baseline - 0.20) else (
                "⚠️ 样本不足" if n < 3 else "❌ 失效"
            )
        h_results.append((label, n, sn, rate, verdict))
        print(f"  {label}: n={n}, strict={sn}, rate={rate:.0%} → {verdict}")

    # H3: 打分模板 — score = +1 (upstream) +1 (narrow) +1 (m_a_capex) +1 (core_pure) -1 (broad) -1 (cross_domain) -1 (policy/concept) -1 (domestic_demand) -1 (satellite/concept_only)
    print("\n## H3: 综合打分模板验算 (v4 加入 ticker_layer)\n")
    print("  规则: +1 each (upstream/narrow/m_a_capex/core_pure),")
    print("        -1 each (broad/cross_domain/policy/concept/domestic_demand/satellite/concept_only)")
    bucket = defaultdict(list)
    for j in joined:
        s = 0
        if j.get("supply_chain_position") == "upstream": s += 1
        if j.get("specificity") == "narrow": s += 1
        if j.get("catalyst_type") == "m_a_capex": s += 1
        if j.get("ticker_layer") == "core_pure": s += 1
        if j.get("specificity") == "broad": s -= 1
        if j.get("cross_domain") is True: s -= 1
        if j.get("catalyst_type") in ("policy", "concept"): s -= 1
        if j.get("driver_origin") == "domestic_demand": s -= 1
        if j.get("ticker_layer") in ("satellite", "concept_only"): s -= 1
        bucket[s].append(j)
    print(f"\n  {'score':>6} {'n':>4} {'strict_n':>10} {'strict_rate':>12}")
    for s in sorted(bucket.keys(), reverse=True):
        items = bucket[s]
        n = len(items)
        sn = sum(1 for j in items if j["hit_strict"])
        rate = sn / n if n else 0
        print(f"  {s:>6} {n:>4} {sn:>10} {rate:>11.0%}")

    return h_results


def show_winners_losers(joined: list, top_n: int = 10):
    """Show top winners (highest excess_vs_sector) and top losers."""
    valid = [j for j in joined if j.get("excess_vs_sector") is not None]
    valid.sort(key=lambda j: j["excess_vs_sector"], reverse=True)
    print(f"\n\n## TOP {top_n} WINNERS (excess_vs_sector desc)\n")
    print(f"  {'excess':>8} {'code':<14} {'name':<10} | {'cat':<14} {'pos':<10} {'spec':<8} | title")
    for j in valid[:top_n]:
        ex = j["excess_vs_sector"]
        print(f"  vs板  +{ex:>5.1f}pp |  {j['code']:<14} {(j['name'] or ''):<10} | "
              f"{(j.get('catalyst_type') or '?'):<14} {(j.get('supply_chain_position') or '?'):<10} "
              f"{(j.get('specificity') or '?'):<8} | {j['event_title']}")
    print(f"\n## TOP {top_n} LOSERS (excess_vs_sector asc)\n")
    print(f"  {'excess':>8} {'code':<14} {'name':<10} | {'cat':<14} {'pos':<10} {'spec':<8} | title")
    for j in valid[-top_n:][::-1]:
        ex = j["excess_vs_sector"]
        sign = "+" if ex >= 0 else ""
        print(f"  vs板 {sign}{ex:>5.1f}pp |  {j['code']:<14} {(j['name'] or ''):<10} | "
              f"{(j.get('catalyst_type') or '?'):<14} {(j.get('supply_chain_position') or '?'):<10} "
              f"{(j.get('specificity') or '?'):<8} | {j['event_title']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="14-28", help="days_since_event 窗口, 默认 14-28")
    ap.add_argument("--since", default=None, help="event_ts 起点 ISO date, e.g. 2026-04-01")
    ap.add_argument("--hypothesis-check", action="store_true", help="只跑 H1/H2/H3 验算")
    ap.add_argument("--markdown", action="store_true", help="输出 markdown 表格 (可粘贴到 weekly review)")
    ap.add_argument("--top", type=int, default=10, help="top winners/losers 数量, 默认 10")
    args = ap.parse_args()

    d_lo, d_hi = parse_window(args.window)
    print(f"# Narrative Radar Review")
    print(f"窗口: D{d_lo}-{d_hi}; events since: {args.since or '不限'}")

    events = load_events(args.since)
    perf_rows = load_perf_window(d_lo, d_hi)
    pairs = to_unique_pairs(perf_rows)
    joined = join_events_perf(events, pairs)
    joined_a = [j for j in joined if j["hit_strict"] is not None]

    n_total = len(joined_a)
    if n_total == 0:
        print("\n⚠️  无可用数据 (joined_a is empty). 检查 perf 是否已 verify, events 是否已打标。")
        return

    strict_n = sum(1 for j in joined_a if j["hit_strict"])
    baseline = strict_n / n_total

    print(f"\nevents (labeled): {len(events)}")
    print(f"perf rows in window: {len(perf_rows)}")
    print(f"unique pairs: {len(pairs)}")
    print(f"joined valid pairs: {n_total}, baseline strict_rate: {strict_n}/{n_total} = {baseline:.0%}")

    fields = [
        "catalyst_type",
        "has_quantified_data",
        "has_explicit_catalyst_date",
        "supply_chain_position",
        "driver_origin",
        "specificity",
        "is_lagging_indicator",
        "cross_domain",
        "ticker_layer",  # 第 9 维 (ticker-level)
    ]

    if args.markdown:
        print("\n\n# Markdown 输出 (可粘贴 weekly review)\n")
        print(f"## Cross-tab (D{d_lo}-{d_hi}, n={n_total}, baseline={baseline:.0%})\n")
        for f in fields:
            rows = crosstab(joined_a, f, baseline)
            print(emit_markdown_table(f, rows, baseline))
    else:
        for f in fields:
            rows = crosstab(joined_a, f, baseline)
            print_crosstab_table(f, rows)

    if args.hypothesis_check or not args.markdown:
        hypothesis_check(joined_a, baseline)

    show_winners_losers(joined_a, args.top)

    # 落盘 joined data 给 case study 用
    out = _HERE / f"_review_joined_d{d_lo}-{d_hi}.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for j in joined_a:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")
    print(f"\n\n✅ 写 {out.name} ({n_total} 行) for case study")


if __name__ == "__main__":
    main()
