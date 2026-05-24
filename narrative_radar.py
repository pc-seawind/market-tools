#!/usr/bin/env python3
"""narrative_radar.py — 叙事雷达事件库 + 助手 (CLI for the cron-fired agent).

设计取舍:
  MCP tools (search_baidu/tavily/fetch_url) 只能 agent 进程内调用,
  Python 脚本本身没有这层调用能力. 所以这个脚本不是 end-to-end 雷达,
  而是 cron-fired agent 的"事件库管理"工具:
    * agent 用 MCP 搜新闻 → 自己读判断分数 → 调本工具 append 一条事件
    * agent 跑完所有 query → 调本工具 summarize 拿 markdown 给 create_doc
    * picker (周一 cron) 用本工具 since 拉过去 7 天事件做 TOP3 投票

事件 schema:
  {
    "ts":          "2026-05-23T18:30:00+08:00",  # ISO 时间
    "trade_date":  "20260523",                    # 决策日 (用作过滤 / 回测对齐)
    "track":       "AI" / "家居" / "AI×家居",
    "subdomain":   "ai__compute_chip" / ...,
    "score":       0..3,    # 0=噪音 1=常规 2=叙事级 3=罕见重磅
    "title":       原文标题,
    "url":         原 URL,
    "source":      "baidu" / "tavily" / 网址名,
    "rationale":   agent 给的 1 行打分理由,
    "thesis_seed": agent 写的初步推演 (单位经济变化 / 受益方草图),
    "tickers":     [{"code", "name", "side": "+"/"-"}, ...]   # 受益/受损
  }

CLI:
  python3 narrative_radar.py add --score 2 --track AI --subdomain ai__compute_chip ...
  python3 narrative_radar.py since --days 7
  python3 narrative_radar.py today_doc           # 输出今日 radar 飞书 markdown
  python3 narrative_radar.py picker_doc          # 输出本周 TOP3 推演候选 markdown
  python3 narrative_radar.py validate            # 检查 universe.yaml 一致性
"""

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS_PATH = os.path.join(HERE, "narrative_events.jsonl")
UNIVERSE_PATH = os.path.join(HERE, "narrative_universe.yaml")

CN_TZ = dt.timezone(dt.timedelta(hours=8))


def load_universe():
    import yaml
    with open(UNIVERSE_PATH) as f:
        return yaml.safe_load(f)


def load_events():
    if not os.path.exists(EVENTS_PATH):
        return []
    return [json.loads(l) for l in open(EVENTS_PATH)]


# ============================================================================
# event 分类器 — late_stage / event_type / score_penalty
# (实现 W21 v3 §6 #1 + #2 改进: 末期抱团降权 + lagging 数据降权)
# ============================================================================

def is_late_stage(subdomain: str, universe: dict) -> bool:
    """subdomain 在 universe.late_stage_subdomains 里?"""
    return subdomain in (universe.get("late_stage_subdomains") or [])


def classify_event_type(title: str, rationale: str = "", thesis_seed: str = "",
                        universe: dict | None = None) -> tuple[str, int]:
    """根据关键词把 event 分到 capex_lock / quant_increment / govt_policy /
    recap_news / trailing_data / other 之一. 返回 (event_type, score_penalty).

    匹配顺序: yaml 里 event_type_patterns 的字典插入顺序 (capex_lock 优先).
    第一次命中即返回 — 这样 "capex 上调研报" 会归 capex_lock 不是 recap_news.
    """
    if universe is None:
        universe = load_universe()

    text = " ".join([title or "", rationale or "", thesis_seed or ""]).lower()
    patterns = universe.get("event_type_patterns") or {}

    for et, spec in patterns.items():
        kws = spec.get("keywords") or []
        for kw in kws:
            if kw and kw.lower() in text:
                return et, int(spec.get("score_penalty", 0))

    return "other", 0


def compute_event_quality(event: dict, universe: dict | None = None) -> dict:
    """对一条 event 计算 event_type + late_stage + 综合 score_penalty + effective_score.

    用法:
      cmd_add 写新 event 时调用一次, 把结果合并进 event dict.
      也供老 event backfill 用 (script 遍历 jsonl 调它).
    """
    if universe is None:
        universe = load_universe()

    et, et_penalty = classify_event_type(
        event.get("title", ""),
        event.get("rationale", ""),
        event.get("thesis_seed", ""),
        universe=universe,
    )
    late = is_late_stage(event.get("subdomain", ""), universe)
    late_penalty = 1 if late else 0
    total_penalty = et_penalty + late_penalty

    raw_score = int(event.get("score", 0))
    effective = max(0, raw_score - total_penalty)

    return {
        "event_type":      et,
        "late_stage":      late,
        "score_penalty":   total_penalty,
        "effective_score": effective,
    }


# ============================================================================
# add — append 一条事件到 jsonl
# ============================================================================
def cmd_add(args):
    universe = load_universe()
    if args.subdomain not in universe:
        print(f"❌ subdomain '{args.subdomain}' 不在 narrative_universe.yaml", file=sys.stderr)
        print(f"   合法 subdomain: {[k for k in universe if k.startswith(('ai__','home__','cross__'))]}", file=sys.stderr)
        sys.exit(1)

    sd = universe[args.subdomain]
    track = sd.get("track", "?")

    # 解析 tickers 参数: "688256.SH:寒武纪:+,300308.SZ:中际旭创:-"
    tickers = []
    if args.tickers:
        for chunk in args.tickers.split(","):
            parts = chunk.split(":")
            if len(parts) == 2:
                tickers.append({"code": parts[0].strip(), "name": parts[1].strip(), "side": "+"})
            elif len(parts) == 3:
                tickers.append({"code": parts[0].strip(), "name": parts[1].strip(), "side": parts[2].strip()})

    now = dt.datetime.now(CN_TZ)
    event = {
        "ts":          now.isoformat(),
        "trade_date":  now.strftime("%Y%m%d"),
        "track":       track,
        "subdomain":   args.subdomain,
        "score":       int(args.score),
        "title":       args.title,
        "url":         args.url or "",
        "source":      args.source or "",
        "rationale":   args.rationale or "",
        "thesis_seed": args.thesis_seed or "",
        "tickers":     tickers,
    }

    # W21 v3 §6 改进: 自动分类 event_type + late_stage 标记
    quality = compute_event_quality(event, universe=universe)
    event.update(quality)

    with open(EVENTS_PATH, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    badge = []
    if quality["late_stage"]:
        badge.append("⚠️末期抱团")
    if quality["score_penalty"] > 0:
        badge.append(f"score{args.score}→{quality['effective_score']}")
    badge_str = f" [{' / '.join(badge)}]" if badge else ""
    print(f"✅ event appended ({event['trade_date']} {track}/{args.subdomain} "
          f"score={args.score} type={quality['event_type']}){badge_str}")
    print(f"   {args.title[:80]}")
    print(f"   tickers: {len(tickers)}")
    if quality["score_penalty"] > 0:
        print(f"   ⚠️  score_penalty={quality['score_penalty']} → effective_score={quality['effective_score']}")


# ============================================================================
# since — 列出过去 N 天的事件
# ============================================================================
def cmd_since(args):
    days = int(args.days)
    cutoff = dt.datetime.now(CN_TZ) - dt.timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y%m%d")
    events = [e for e in load_events() if e["trade_date"] >= cutoff_str]
    events.sort(key=lambda e: (e["trade_date"], -e["score"]))
    if args.json:
        print(json.dumps(events, ensure_ascii=False, indent=2))
        return

    print(f"过去 {days} 天事件: {len(events)} 条")
    print()
    print(f"{'日期':<10} {'分':<2} {'track':<10} {'subdomain':<28} 标题")
    print("-" * 100)
    for e in events:
        print(f"{e['trade_date']:<10} {e['score']:<2} {e['track']:<10} "
              f"{e['subdomain']:<28} {e['title'][:50]}")


# ============================================================================
# today_doc — 输出今日 radar 飞书 markdown (供 create_doc 用)
# ============================================================================
def cmd_today_doc(args):
    today = dt.datetime.now(CN_TZ).strftime("%Y%m%d")
    events = [e for e in load_events() if e["trade_date"] == today]
    # 按 effective_score 优先, raw score 其次 (老事件没有 effective_score 落到 score)
    events.sort(key=lambda e: -(e.get("effective_score", e.get("score", 0))))

    md = []
    md.append(f"# 叙事雷达 {dt.datetime.now(CN_TZ).strftime('%Y-%m-%d')}")
    md.append("")
    md.append(f"**事件总数**: {len(events)} 条")
    md.append(f"**覆盖 track**: AI / 家居 / AI×家居")
    md.append("")
    md.append("> 评分含 `effective_score` (raw score 减去 late_stage / lagging penalty). "
              "raw=2 effective=1 = 末期抱团赛道里的常规消息.")
    md.append("")

    if not events:
        md.append("> 今日无新增叙事事件。雷达扫了 query 但没有命中 score≥1 的内容。")
        md.append("")
    else:
        # 按 score 分组
        for score_threshold, label in [(3, "🔥 重磅 (score=3)"), (2, "⭐ 叙事级 (score=2)"), (1, "💬 常规 (score=1)")]:
            subset = [e for e in events if e["score"] == score_threshold]
            if not subset:
                continue
            md.append(f"## {label} — {len(subset)} 条")
            md.append("")
            for e in subset:
                # tags: event_type + late_stage + effective_score 降级警告
                tags = []
                et = e.get("event_type")
                if et and et != "other":
                    tags.append(f"`type={et}`")
                if e.get("late_stage"):
                    tags.append("`⚠️末期抱团`")
                eff = e.get("effective_score")
                if eff is not None and eff < e["score"]:
                    tags.append(f"`score:{e['score']}→{eff}`")
                tag_line = (" " + " · ".join(tags)) if tags else ""

                md.append(f"### [{e['track']}/{e['subdomain']}] {e['title']}{tag_line}")
                md.append("")
                md.append(f"- **来源**: {e['source']} {('| ' + e['url']) if e['url'] else ''}")
                if e["rationale"]:
                    md.append(f"- **打分理由**: {e['rationale']}")
                if e["thesis_seed"]:
                    md.append(f"- **初步推演**: {e['thesis_seed']}")
                if e["tickers"]:
                    md.append(f"- **可能受影响标的**:")
                    for t in e["tickers"]:
                        marker = "📈" if t.get("side", "+") == "+" else "📉"
                        md.append(f"  - {marker} {t['code']} {t['name']}")
                md.append("")

    md.append("---")
    md.append(f"*radar 自动生成 / 数据 owner: emox / cron: daily 18:30*")
    print("\n".join(md))


# ============================================================================
# picker_doc — 周一推演候选 TOP3
# ============================================================================
def cmd_picker_doc(args):
    cutoff = dt.datetime.now(CN_TZ) - dt.timedelta(days=7)
    cutoff_str = cutoff.strftime("%Y%m%d")

    def _eff(e):
        return e.get("effective_score", e.get("score", 0))

    # 用 effective_score 筛 (≥2) — 末期抱团赛道里的 score=2 会被降到 1, 不进 picker
    events = [e for e in load_events()
              if e["trade_date"] >= cutoff_str and _eff(e) >= 2]
    # 按 effective_score 倒序, 同分按时间倒序
    events.sort(key=lambda e: (-_eff(e), -int(e["trade_date"])))
    top3 = events[:3]

    week_no = dt.datetime.now(CN_TZ).isocalendar()[1]
    md = []
    md.append(f"# 本周推演候选 TOP3 (W{week_no})")
    md.append("")
    md.append(f"过去 7 天累计 score≥2 的叙事级事件: **{len(events)} 条**, 取 TOP3 让用户勾选深度推演.")
    md.append("")

    if not top3:
        md.append("> 本周无 score≥2 事件. 信号面较弱, 跳过深度推演.")
        return

    for i, e in enumerate(top3, 1):
        tags = []
        et = e.get("event_type")
        if et and et != "other":
            tags.append(f"`type={et}`")
        if e.get("late_stage"):
            tags.append("`⚠️末期抱团`")
        tag_line = (" " + " · ".join(tags)) if tags else ""

        md.append(f"## #{i} [{e['track']}/{e['subdomain']}] {e['title']}{tag_line}")
        md.append("")
        eff = e.get("effective_score")
        if eff is not None and eff < e["score"]:
            md.append(f"- **score**: {e['score']} (effective={eff}, penalty={e.get('score_penalty', 0)})")
        else:
            md.append(f"- **score**: {e['score']}")
        md.append(f"- **来源**: {e['source']} {('| ' + e['url']) if e['url'] else ''}")
        md.append(f"- **触发日**: {e['trade_date']}")
        if e["rationale"]:
            md.append(f"- **打分理由**: {e['rationale']}")
        if e["thesis_seed"]:
            md.append(f"- **种子推演**: {e['thesis_seed']}")
        if e["tickers"]:
            tk_str = ", ".join(f"{t['code']}({t['name']})" for t in e["tickers"][:8])
            md.append(f"- **候选标的**: {tk_str}")
        md.append("")
        md.append(f"**勾选执行**: 回复 `推演 #{i}` 触发深度推演 + 飞书云文档归档")
        md.append("")

    md.append("---")
    md.append(f"*picker 自动生成 / cron: 每周一 09:00*")
    print("\n".join(md))


# ============================================================================
# reclassify — 给老 event 批量回填 event_type / late_stage / effective_score
# 仅在 universe.yaml schema_version 升级 / 关键词调整后跑一次, 幂等.
# ============================================================================
def cmd_reclassify(args):
    universe = load_universe()
    events = load_events()
    if not events:
        print("no events to reclassify")
        return

    counts = {"updated": 0, "unchanged": 0, "type_dist": {}, "late_stage": 0}
    new_events = []
    for e in events:
        q_new = compute_event_quality(e, universe=universe)
        old_type = e.get("event_type")
        old_late = bool(e.get("late_stage"))
        old_pen = int(e.get("score_penalty", 0))

        if (old_type == q_new["event_type"] and old_late == q_new["late_stage"]
                and old_pen == q_new["score_penalty"]):
            counts["unchanged"] += 1
        else:
            counts["updated"] += 1

        e.update(q_new)
        counts["type_dist"][q_new["event_type"]] = counts["type_dist"].get(q_new["event_type"], 0) + 1
        if q_new["late_stage"]:
            counts["late_stage"] += 1
        new_events.append(e)

    if args.dry_run:
        print(f"[DRY] {counts['updated']} updated / {counts['unchanged']} unchanged")
        print(f"[DRY] type 分布: {counts['type_dist']}")
        print(f"[DRY] late_stage 命中: {counts['late_stage']}")
        return

    # 备份再写回
    import shutil
    bak = EVENTS_PATH + ".bak.reclassify"
    shutil.copy(EVENTS_PATH, bak)
    with open(EVENTS_PATH, "w") as f:
        for e in new_events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    print(f"✅ {counts['updated']} updated / {counts['unchanged']} unchanged")
    print(f"   type 分布: {counts['type_dist']}")
    print(f"   late_stage 命中: {counts['late_stage']}")
    print(f"   backup → {bak}")


# ============================================================================
# validate — 检查 universe yaml 一致性
# ============================================================================
def cmd_validate(args):
    u = load_universe()
    sd_keys = [k for k in u if k.startswith(("ai__", "home__", "cross__"))]
    print(f"sub-domains: {len(sd_keys)}")
    seen_codes = {}
    for k in sd_keys:
        sd = u[k]
        for t in sd.get("tickers", []):
            code = t.get("code")
            if code in seen_codes:
                print(f"  ⚠️  ticker {code} 在 {seen_codes[code]} 和 {k} 都出现了 (跨 subdomain 是 OK 的)")
            seen_codes[code] = k
    print(f"unique tickers: {len(seen_codes)}")
    print(f"news sources: {len(u.get('news_sources', []))}")
    print(f"search queries: {len(u.get('search_queries', []))}")


# ============================================================================
# main
# ============================================================================
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("--score", required=True, type=int, choices=[0, 1, 2, 3])
    p_add.add_argument("--track", required=False)  # 从 subdomain 自动推
    p_add.add_argument("--subdomain", required=True)
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--url", default="")
    p_add.add_argument("--source", default="")
    p_add.add_argument("--rationale", default="")
    p_add.add_argument("--thesis-seed", dest="thesis_seed", default="")
    p_add.add_argument("--tickers", default="", help='"code:name:+,code:name:-" 形式')
    p_add.set_defaults(func=cmd_add)

    p_since = sub.add_parser("since")
    p_since.add_argument("--days", type=int, default=7)
    p_since.add_argument("--json", action="store_true")
    p_since.set_defaults(func=cmd_since)

    p_today = sub.add_parser("today_doc")
    p_today.set_defaults(func=cmd_today_doc)

    p_pick = sub.add_parser("picker_doc")
    p_pick.set_defaults(func=cmd_picker_doc)

    p_val = sub.add_parser("validate")
    p_val.set_defaults(func=cmd_validate)

    p_rc = sub.add_parser("reclassify",
                          help="给现有 event jsonl 批量回填 event_type/late_stage/effective_score")
    p_rc.add_argument("--dry-run", action="store_true")
    p_rc.set_defaults(func=cmd_reclassify)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
