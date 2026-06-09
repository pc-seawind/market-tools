"""narrative_label_event.py — 给 narrative_events.jsonl 中尚未人工打标的 events 跑 CLI prompt 打标。

背景:
  v3 报告确认 heuristic 整体准确率 74%, driver_origin 仅 43%, 不能用于预测。
  后续新 events 必须 100% 人工打标。本脚本提供轻量 CLI: heuristic 跑初稿 → 用户单字符确认/修改 → 写回 events.jsonl 的 features 字段。

UX:
  每条 event 显示 title/rationale + heuristic 初稿值, 用户对 8 维度单字符确认 (回车=接受 heuristic 默认值)。
  cross_domain 由 track 字段自动推断, 不问。

数据写入位置:
  narrative_events.jsonl 每行新增 features 字段 (dict), 同时打 features._human_labeled=True 标记。
  下次再跑只处理未标过的 events, 已经标过的不重复问。

用法:
  python3 narrative_label_event.py                # 处理所有未标 events
  python3 narrative_label_event.py --since YYYYMMDD  # 只处理这天以后
  python3 narrative_label_event.py --redo TS_PREFIX  # 强制重打某条 (按 ts 前缀匹配)
  python3 narrative_label_event.py --list          # 仅列出未标 events 数量, 不交互
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from narrative_feature_tag import tag_event  # heuristic 初稿源

EVENTS_PATH = _HERE / "narrative_events.jsonl"

# 8 维度 enum + 单字符简写
DIM_SPEC = [
    ("catalyst_type", {
        "b": "business_data",
        "m": "m_a_capex",
        "p": "policy",
        "i": "industry_chain",
        "c": "concept",
        "t": "technical_signal",
        "o": "other",
    }, "[b]usiness_data / [m]a_capex / [p]olicy / [i]ndustry_chain / [c]oncept / [t]ech / [o]ther"),
    ("has_quantified_data", {"y": True, "n": False}, "[y/n]"),
    ("has_explicit_catalyst_date", {"y": True, "n": False}, "[y/n]"),
    ("supply_chain_position", {
        "u": "upstream", "m": "midstream", "d": "downstream", "c": "cross_position",
    }, "[u]pstream / [m]idstream / [d]ownstream / [c]ross_position"),
    ("driver_origin", {
        "g": "global_demand", "s": "domestic_substitution",
        "d": "domestic_demand", "c": "cross",
    }, "[g]lobal_demand / [s]ubstitution / [d]omestic_demand / [c]ross"),
    ("specificity", {"n": "narrow", "m": "medium", "b": "broad"}, "[n]arrow / [m]edium / [b]road"),
    ("is_lagging_indicator", {"y": True, "n": False}, "[y/n]"),
]


def _value_to_short(field: str, value) -> str:
    """把 enum 值反向映射回单字符 (用于显示当前 heuristic 默认)."""
    for f, mapping, _ in DIM_SPEC:
        if f == field:
            for k, v in mapping.items():
                if v == value:
                    return k
    return "?"


def prompt_label(event: dict, draft: dict) -> dict:
    """对单个 event 跑 8 维 prompt, 返回最终 features dict."""
    print("\n" + "─" * 70)
    print(f"ts: {event.get('ts','?')[:19]}  score={event.get('score')}  {event.get('subdomain')}  track={event.get('track')}")
    print(f"title: {event.get('title','')}")
    rat = event.get("rationale", "")
    if len(rat) > 200:
        rat = rat[:200] + "..."
    print(f"rationale: {rat}")
    print("(回车 = 保留 heuristic 初稿值)")
    print()

    final = {}
    for i, (field, mapping, label) in enumerate(DIM_SPEC, 1):
        default_val = draft.get(field)
        default_short = _value_to_short(field, default_val)
        prompt = f"[{i}/{len(DIM_SPEC)}] {field}\n    {label}\n    heuristic: {default_val} → ? "
        while True:
            ans = input(prompt).strip().lower()
            if ans == "":
                final[field] = default_val
                break
            if ans in mapping:
                final[field] = mapping[ans]
                break
            print(f"    ⚠️ 无效输入 '{ans}', 可选: {list(mapping.keys())}")

    # cross_domain 自动从 track 推断 (heuristic 100% 准确)
    final["cross_domain"] = "×" in (event.get("track") or "")
    final["_human_labeled"] = True
    final["_labeled_at"] = datetime.now().isoformat(timespec="seconds")
    return final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="只处理 trade_date >= YYYYMMDD 的 events")
    ap.add_argument("--redo", help="强制重打 ts 前缀匹配的某条 (即使已 _human_labeled)")
    ap.add_argument("--list", action="store_true", help="仅列出未标 events 数量")
    args = ap.parse_args()

    if not EVENTS_PATH.exists():
        print(f"❌ {EVENTS_PATH} 不存在")
        return 1

    events = [json.loads(l) for l in open(EVENTS_PATH, encoding="utf-8") if l.strip()]
    print(f"读入 {len(events)} events")

    # 找出待打标 events
    todo = []
    for e in events:
        already = (e.get("features") or {}).get("_human_labeled", False)
        if args.redo and e.get("ts", "").startswith(args.redo):
            todo.append(e)
            continue
        if already:
            continue
        if args.since and (e.get("trade_date") or "") < args.since:
            continue
        todo.append(e)

    print(f"待打标 events: {len(todo)}")
    if args.list:
        for e in todo:
            print(f"  {e.get('ts','?')[:10]} {e.get('subdomain'):30s} {e.get('title','')[:50]}")
        return 0

    if not todo:
        print("✅ 所有 events 都已人工打标")
        return 0

    # 备份
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = EVENTS_PATH.with_suffix(f".jsonl.bak.label.{ts}")
    shutil.copy2(EVENTS_PATH, bak)
    print(f"✅ 备份到 {bak.name}")

    # 逐条打标
    for i, e in enumerate(todo, 1):
        print(f"\n=== {i}/{len(todo)} ===")
        try:
            draft = tag_event(e)
            label = prompt_label(e, draft)
            e["features"] = label
        except (KeyboardInterrupt, EOFError):
            print(f"\n⏸ 中断, 已完成 {i-1}/{len(todo)}, 部分进度保存中...")
            break

    # 写回 (整文件覆盖, 保留所有 events 顺序)
    with open(EVENTS_PATH, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"\n✅ 写回 {EVENTS_PATH.name} ({len(events)} 行)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
