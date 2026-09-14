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
    "trade_date":  "20260523",                    # radar 决策日
    "published_at":"2026-05-23T17:30:00+08:00",   # 原文发布时间 (新事件必填)
    "pub_date":    "20260523",                    # 从 published_at 派生
    "session":     "pre" / "intraday" / "post", # 基准价口径
    "pool":        "research" / "trade",          # 研究库与 alpha 验证池隔离
    "track":       "AI" / "家居" / "AI×家居",
    "subdomain":   "ai__compute_chip" / ...,
    "score":       0..3,    # 0=噪音 1=常规 2=叙事级 3=罕见重磅
    "title":       原文标题,
    "url":         原 URL,
    "source":      "baidu" / "tavily" / 网址名,
    "rationale":   agent 给的 1 行打分理由,
    "thesis_seed": agent 写的初步推演 (单位经济变化 / 受益方草图),
    "tickers":     [{"code", "name", "side": "+"/"-"}, ...],  # 研究映射
    "alpha_tickers":[...],                           # 最多 2 个直接受益/受损标的
    "eligibility_reasons": [...]                    # 未进 trade pool 的原因
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
import re
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
EVENTS_PATH = os.path.join(HERE, "narrative_events.jsonl")
UNIVERSE_PATH = os.path.join(HERE, "narrative_universe.yaml")
TICKER_LAYER_PATH = os.path.join(HERE, "ticker_layer.yaml")

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
# 去重指纹 — 防止 cron 把同一条产业叙事每天重复入库 (2026-06 加固)
# 经 _dedup_events.py 验证: 数字指纹比字符 ngram 抗"同义改写"
# ============================================================================
_DEDUP_WINDOW_DAYS = 30
_DEDUP_SIM_THRESHOLD = 0.55

def _norm_title(t: str) -> str:
    t = t.lower()
    t = re.sub(r"[0-9]+(\.[0-9]+)?", "#", t)
    t = re.sub(r"[^一-鿿a-z#]", "", t)
    return t

def _num_fingerprint(t: str) -> frozenset:
    """抽取标题里的关键数字 token (范围/百分比/倍数), 同义改写下保持稳定。"""
    t = t.replace("~", "-").replace("％", "%")
    nums = set()
    for m in re.findall(r"(\d+(?:\.\d+)?)\s*[-—]\s*(\d+(?:\.\d+)?)", t):
        nums.add(f"{m[0]}-{m[1]}")
    for m in re.findall(r"(?<![\d.-])(\d{2,4}(?:\.\d+)?)(?![\d.-])", t):
        nums.add(m)
    return frozenset(nums)

def _ngrams(s: str, n=3) -> frozenset:
    if len(s) < n:
        return frozenset({s}) if s else frozenset()
    return frozenset(s[i:i+n] for i in range(len(s)-n+1))

def _jaccard(a, b) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

def find_duplicate(new_event: dict, events: list, window_days=_DEDUP_WINDOW_DAYS):
    """在已有 events 里找与 new_event 高度相似的事件 (同 subdomain + 时间窗口内 +
    数字指纹强重叠 或 字符相似度高 + ticker 重叠)。返回首个命中事件或 None。"""
    sub = new_event.get("subdomain")
    new_ng = _ngrams(_norm_title(new_event.get("title", "")))
    new_nums = _num_fingerprint(new_event.get("title", ""))
    new_tk = frozenset(t.get("code", "") for t in new_event.get("tickers", []))
    try:
        new_dt = dt.datetime.fromisoformat(new_event["ts"].split("#")[0])
    except Exception:
        new_dt = dt.datetime.now(CN_TZ)
    for e in events:
        if e.get("subdomain") != sub:
            continue
        # 时间窗口
        try:
            e_dt = dt.datetime.fromisoformat(e["ts"].split("#")[0])
            if abs((new_dt - e_dt).days) > window_days:
                continue
        except Exception:
            pass
        e_tk = frozenset(t.get("code", "") for t in e.get("tickers", []))
        if not (new_tk & e_tk):
            continue
        e_ng = _ngrams(_norm_title(e.get("title", "")))
        e_nums = _num_fingerprint(e.get("title", ""))
        char_sim = _jaccard(new_ng, e_ng)
        num_sim = _jaccard(new_nums, e_nums)
        shared = new_nums & e_nums
        num_match = (num_sim >= 0.5 and len(shared) >= 2)
        if max(char_sim, num_sim) >= _DEDUP_SIM_THRESHOLD or num_match:
            return e
    return None


def load_ticker_layer() -> dict:
    """读 ticker_layer.yaml — {subdomain: {ticker_code: layer}}.
    文件不存在返回空字典 (允许新装/迁移时不爆)."""
    if not os.path.exists(TICKER_LAYER_PATH):
        return {}
    import yaml
    with open(TICKER_LAYER_PATH) as f:
        return yaml.safe_load(f) or {}


def check_ticker_layer_coverage(subdomain: str, tickers: list[dict]) -> list[dict]:
    """对 tickers 数组里每个 ticker 检查是否在 ticker_layer.yaml 里有标签.
    返回缺失列表 (没标签的 tickers), 调用方决定是 warning 还是 block.
    """
    layer_map = load_ticker_layer()
    sd_map = layer_map.get(subdomain, {}) or {}
    missing = []
    for t in tickers:
        code = t.get("code", "")
        if code and code not in sd_map:
            missing.append(t)
    return missing


# ============================================================================
# event 分类器 — late_stage / event_type / score_penalty
# (实现 W21 v3 §6 #1 + #2 改进: 末期抱团降权 + lagging 数据降权)
# ============================================================================

def is_late_stage(subdomain: str, universe: dict) -> bool:
    """subdomain 在 universe.late_stage_subdomains 里?"""
    return subdomain in (universe.get("late_stage_subdomains") or [])


def classify_event_type(title: str, rationale: str = "", thesis_seed: str = "",
                        universe: dict | None = None) -> tuple[str, int]:
    """按原始标题分类事件，避免 agent 生成的 rationale/thesis_seed 污染标签。

    P0 规则把旧 ``capex_lock`` 拆成已确认订单、客户资本开支、公司已执行
    capex、融资扩产、供给扩张和仅规划六类。配置顺序决定优先级。
    ``rationale`` / ``thesis_seed`` 参数仅为兼容旧调用，不参与匹配。
    """
    if universe is None:
        universe = load_universe()
    text = (title or "").lower()
    patterns = universe.get("event_type_patterns") or {}
    for et, spec in patterns.items():
        for kw in spec.get("keywords") or []:
            if kw and kw.lower() in text:
                return et, int(spec.get("score_penalty", 0))
    return "other", 0


def compute_event_quality(event: dict, universe: dict | None = None) -> dict:
    """计算类型、末期状态、penalty 与 effective_score。"""
    if universe is None:
        universe = load_universe()
    et, et_penalty = classify_event_type(event.get("title", ""), universe=universe)
    late = is_late_stage(event.get("subdomain", ""), universe)
    late_penalty = 1 if late else 0
    total_penalty = et_penalty + late_penalty
    raw_score = int(event.get("score", 0))
    return {
        "event_type": et,
        "late_stage": late,
        "score_penalty": total_penalty,
        "effective_score": max(0, raw_score - total_penalty),
    }


_WEAK_TRADE_EVENT_TYPES = {
    "capacity_plan", "financing_capex", "industry_supply_expansion",
    "recap_news", "trailing_data",
}
_ALPHA_LAYERS = {"core_pure", "core_partial"}


def _parse_tickers(raw: str) -> list[dict]:
    """Parse ``code:name[:side]`` CSV and validate side."""
    out = []
    for chunk in (raw or "").split(","):
        if not chunk.strip():
            continue
        parts = [x.strip() for x in chunk.split(":")]
        if len(parts) not in (2, 3):
            raise ValueError(f"ticker 格式错误: {chunk!r}")
        side = parts[2] if len(parts) == 3 else "+"
        if side not in {"+", "-"}:
            raise ValueError(f"ticker side 必须为 +/-: {chunk!r}")
        out.append({"code": parts[0], "name": parts[1], "side": side})
    return out


def _parse_published_at(value: str) -> tuple[Optional[dt.datetime], str]:
    """Return timezone-aware published datetime and YYYYMMDD pub_date."""
    if not value:
        return None, ""
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("--published-at 必须是 ISO-8601，例如 2026-08-01T15:30:00+08:00") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    parsed = parsed.astimezone(CN_TZ)
    return parsed, parsed.strftime("%Y%m%d")


def _attach_layers(subdomain: str, tickers: list[dict]) -> list[dict]:
    sd_layers = load_ticker_layer().get(subdomain, {}) or {}
    return [{**t, "ticker_layer": sd_layers.get(t.get("code"))} for t in tickers]


def _parse_optional_float(value: str, flag: str) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{flag} 必须是数字") from exc


def _cooldown_hits(event: dict, events: list[dict], days: int = 14) -> list[str]:
    """Trade-pool-only cooldown; legacy/research rows do not block new P0 events."""
    codes = {t.get("code") for t in event.get("alpha_tickers", [])}
    if not codes:
        return []
    now = dt.datetime.fromisoformat(event["ts"])
    hit = set()
    for old in events:
        if old.get("pool") != "trade" or old.get("subdomain") != event.get("subdomain"):
            continue
        try:
            old_dt = dt.datetime.fromisoformat(old.get("ts", ""))
        except ValueError:
            continue
        if 0 <= (now - old_dt).days < days:
            hit |= codes & {t.get("code") for t in old.get("alpha_tickers", [])}
    return sorted(x for x in hit if x)


def assess_trade_eligibility(event: dict, existing_events: list[dict]) -> list[str]:
    """Return blocking reasons. Empty means eligible for forward alpha tracking."""
    reasons = []
    if not event.get("published_at") or not event.get("pub_date"):
        reasons.append("missing_published_at")
    if event.get("session") not in {"pre", "intraday", "post"}:
        reasons.append("missing_session")
    if event.get("effective_score", 0) < 2:
        reasons.append("effective_score_below_2")
    if event.get("late_stage"):
        reasons.append("late_stage_blocked")
    if event.get("event_type") in _WEAK_TRADE_EVENT_TYPES:
        reasons.append(f"weak_event_type:{event.get('event_type')}")
    if event.get("surprise") not in {"medium", "high"}:
        reasons.append("surprise_not_verified")
    if event.get("source_tier") not in {"primary", "industry"}:
        reasons.append("source_not_primary_or_industry")
    alpha = event.get("alpha_tickers") or []
    if not alpha:
        reasons.append("no_direct_alpha_ticker")
    if len(alpha) > 2:
        reasons.append("too_many_alpha_tickers")
    if not event.get("mapping_evidence"):
        reasons.append("missing_mapping_evidence")
    tf = event.get("tradeability_features") or {}
    if any(tf.get(k) is None for k in ("position_120d", "pre_ret_20", "volume_ratio_5_20")):
        reasons.append("missing_price_crowding_features")
    else:
        if tf["position_120d"] > 80:
            reasons.append("position_120d_above_80")
        if tf["pre_ret_20"] > 25:
            reasons.append("pre_ret_20_above_25")
        if tf["volume_ratio_5_20"] > 1.8:
            reasons.append("volume_ratio_5_20_above_1.8")
    for t in alpha:
        if t.get("ticker_layer") not in _ALPHA_LAYERS:
            reasons.append(f"ineligible_ticker_layer:{t.get('code')}:{t.get('ticker_layer') or 'missing'}")
    cooling = _cooldown_hits(event, existing_events)
    if cooling:
        reasons.append("cooldown_14d:" + ",".join(cooling))
    return reasons


# ============================================================================
# add — append 一条事件到 jsonl
# ============================================================================
def cmd_add(args):
    universe = load_universe()
    if args.subdomain not in universe:
        print(f"❌ subdomain '{args.subdomain}' 不在 narrative_universe.yaml", file=sys.stderr)
        sys.exit(1)
    track = universe[args.subdomain].get("track", "?")
    try:
        tickers = _parse_tickers(args.tickers)
        alpha_raw = _parse_tickers(args.trade_tickers)
        published_dt, pub_date = _parse_published_at(args.published_at)
        position_120d = _parse_optional_float(args.position_120d, "--position-120d")
        pre_ret_20 = _parse_optional_float(args.pre_ret_20, "--pre-ret-20")
        volume_ratio_5_20 = _parse_optional_float(args.volume_ratio_5_20, "--volume-ratio-5-20")
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)

    mapped_codes = {t["code"] for t in tickers}
    if any(t["code"] not in mapped_codes for t in alpha_raw):
        print("❌ --trade-tickers 必须是 --tickers 的子集", file=sys.stderr)
        sys.exit(1)

    now = dt.datetime.now(CN_TZ)
    event = {
        "ts": now.isoformat(),
        "discovered_at": now.isoformat(),
        "trade_date": now.strftime("%Y%m%d"),
        "published_at": published_dt.isoformat() if published_dt else "",
        "pub_date": pub_date,
        "session": args.session or "",
        "track": track,
        "subdomain": args.subdomain,
        "score": int(args.score),             # narrative importance, not tradeability
        "title": args.title,
        "url": args.url or "",
        "source": args.source or "",
        "source_tier": args.source_tier,
        "surprise": args.surprise,
        "rationale": args.rationale or "",
        "thesis_seed": args.thesis_seed or "",
        "mapping_evidence": args.mapping_evidence or "",
        "tradeability_features": {
            "position_120d": position_120d,
            "pre_ret_20": pre_ret_20,
            "volume_ratio_5_20": volume_ratio_5_20,
        },
        "tickers": _attach_layers(args.subdomain, tickers),
        "alpha_tickers": _attach_layers(args.subdomain, alpha_raw),
        "policy_version": "p0-2026-08-01",
    }
    existing = load_events()
    if not args.force:
        dup = find_duplicate(event, existing)
        if dup is not None:
            print(f"⚠️ 疑似重复事件, 已拒绝入库 (同一叙事 {_DEDUP_WINDOW_DAYS} 天内已存在):", file=sys.stderr)
            print(f"   新: {event['title'][:60]}", file=sys.stderr)
            print(f"   旧: {dup['title'][:60]}  (ts={dup['ts'][:19]}, score={dup.get('score')})", file=sys.stderr)
            sys.exit(2)

    event.update(compute_event_quality(event, universe=universe))
    reasons = assess_trade_eligibility(event, existing)
    requested = args.pool
    if requested == "research":
        event["pool"] = "research"
        reasons = reasons or ["explicit_research_pool"]
    elif requested == "trade" and reasons:
        print("❌ 请求 trade pool 但未通过准入: " + "; ".join(reasons), file=sys.stderr)
        sys.exit(3)
    else:
        event["pool"] = "trade" if not reasons else "research"
    event["eligibility_reasons"] = reasons

    with open(EVENTS_PATH, "a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    badge = f"pool={event['pool']} / effective={event['effective_score']}"
    print(f"✅ event appended ({event['trade_date']} {track}/{args.subdomain} score={args.score} "
          f"type={event['event_type']} / {badge})")
    print(f"   {args.title[:80]}")
    print(f"   mapped={len(tickers)} / alpha={len(event['alpha_tickers'])}")
    if reasons:
        print("   research-only: " + "; ".join(reasons))


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
                tags.append(f"`pool={e.get('pool', 'legacy')}`")
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
                display_tickers = e.get("alpha_tickers") if e.get("pool") == "trade" else e.get("tickers")
                if display_tickers:
                    label = "Alpha 跟踪标的" if e.get("pool") == "trade" else "研究映射标的"
                    md.append(f"- **{label}**:")
                    for t in display_tickers:
                        marker = "📈" if t.get("side", "+") == "+" else "📉"
                        md.append(f"  - {marker} {t['code']} {t['name']}")
                if e.get("eligibility_reasons"):
                    md.append(f"- **未进交易池**: {'; '.join(e['eligibility_reasons'])}")
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
              if e["trade_date"] >= cutoff_str and _eff(e) >= 2
              and (e.get("policy_version") != "p0-2026-08-01" or e.get("pool") == "trade")]
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
        print("\n".join(md))
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
        pick_tickers = e.get("alpha_tickers") or e.get("tickers") or []
        if pick_tickers:
            tk_str = ", ".join(f"{t['code']}({t['name']})" for t in pick_tickers[:8])
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
    p_add.add_argument("--tickers", default="", help='研究映射: "code:name:+,code:name:-"')
    p_add.add_argument("--trade-tickers", default="", help="直接受益/受损 alpha 标的, 必须是 --tickers 子集, 最多2只")
    p_add.add_argument("--published-at", default="", help="原文 ISO-8601 发布时间")
    p_add.add_argument("--session", choices=["pre", "intraday", "post"], default=None)
    p_add.add_argument("--source-tier", choices=["primary", "industry", "major_media", "aggregator", "unknown"], default="unknown")
    p_add.add_argument("--surprise", choices=["high", "medium", "low", "unknown"], default="unknown")
    p_add.add_argument("--mapping-evidence", default="", help="ticker 收益传导证据")
    p_add.add_argument("--position-120d", default="", help="事件基准日前一交易日的120日价格百分位")
    p_add.add_argument("--pre-ret-20", default="", help="事件基准日前20交易日收益率(%)")
    p_add.add_argument("--volume-ratio-5-20", default="", help="事件前5日/20日平均成交量比")
    p_add.add_argument("--pool", choices=["auto", "research", "trade"], default="auto")
    p_add.add_argument("--force", action="store_true",
                       help="跳过去重检查强制入库 (确为新进展时用)")
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
