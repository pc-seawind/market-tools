"""rec_watchdog.py — 推荐有效性看门狗 (framework v2.3 补丁).

核心逻辑: 每天检查所有 open recs 的板块状态是否发生了降级变化.
如果推荐时板块是 HOT (≥60), 当前变成 NEUTRAL/COLD, 触发告警.

告警级别:
  🚨 CRITICAL  — 板块从 HOT 跌至 COLD (<45), BUY rec 建议 EXIT
  ⚠️  WARNING   — 板块从 HOT 跌至 NEUTRAL (45-59), BUY rec 建议降级 WATCH
  ℹ️  INFO      — 板块仍 HOT 但 flow 反转 (20d 正→负), 注意仓位

CLI:
  python3 rec_watchdog.py              # 全量检查, 输出告警表
  python3 rec_watchdog.py --json       # JSON 输出 (cron 消费)
  python3 rec_watchdog.py --summary    # 一句话摘要 (给 cron prompt 嵌入)
"""
from __future__ import annotations
import argparse
import json
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from rec_log import _read_jsonl, _parse_ts_date, MAX_VERIFY_HORIZON_DAYS
from sector_score import score_sector, SectorScore, _fmt_cny

_REC_FILE = _HERE / "recommendations.jsonl"


# ─── Alert levels ────────────────────────────────────────────────────────

@dataclass
class RecAlert:
    rec_id: str
    code: str
    name: str
    action: str  # original action (BUY/WATCH)
    sector: str
    # At recommendation time
    score_at_rec: float
    tier_at_rec: str  # "HOT" / "HOT_STRONG" etc
    # Current
    score_now: float
    tier_now: str
    flow_20d_at_rec: float | None
    flow_20d_now: float | None
    # Alert
    alert_level: str  # CRITICAL / WARNING / INFO / OK
    alert_reason: str
    suggestion: str  # EXIT / DOWNGRADE_TO_WATCH / REDUCE / HOLD / MONITOR


def _tier_from_score(score: float) -> str:
    if score >= 80: return "HOT_STRONG"
    if score >= 60: return "HOT"
    if score >= 45: return "NEUTRAL"
    if score >= 30: return "COLD"
    return "AVOID"


def check_rec(rec: dict[str, Any]) -> RecAlert | None:
    """Check one recommendation against current sector state."""
    code = rec.get("code", "")
    name = rec.get("name", "?")
    action = rec.get("action", "?")
    sector = rec.get("sector", "")
    score_at_rec = rec.get("sector_tier1_score") or 0.0

    # Skip expired recs
    rec_date = _parse_ts_date(rec.get("ts", ""))
    days_since = (date.today() - rec_date).days
    if days_since > MAX_VERIFY_HORIZON_DAYS:
        return None

    # Get current sector score
    current = score_sector(sector)
    if current is None or current.data_quality == "no_etf":
        # Can't score — skip
        return None

    score_now = current.total_score
    tier_at_rec = _tier_from_score(score_at_rec)
    tier_now = _tier_from_score(score_now)

    flow_20d_now = current.raw_signals.get("flow_20d_cny")

    # Determine alert level
    alert_level = "OK"
    alert_reason = ""
    suggestion = "HOLD"

    # Case 1: Sector dropped from HOT/HOT_STRONG to COLD/AVOID
    if score_at_rec >= 60 and score_now < 45:
        alert_level = "CRITICAL"
        alert_reason = f"板块从 {tier_at_rec}({score_at_rec:.0f}) 暴跌至 {tier_now}({score_now:.0f})"
        if action == "BUY":
            suggestion = "EXIT"
        else:
            suggestion = "REMOVE_FROM_WATCH"

    # Case 2: Sector dropped from HOT to NEUTRAL
    elif score_at_rec >= 60 and score_now < 60:
        alert_level = "WARNING"
        alert_reason = f"板块从 {tier_at_rec}({score_at_rec:.0f}) 降至 {tier_now}({score_now:.0f})"
        if action == "BUY":
            suggestion = "DOWNGRADE_TO_WATCH"
        else:
            suggestion = "MONITOR"

    # Case 3: Still HOT but flow reversed (20d was positive, now negative)
    elif score_at_rec >= 60 and score_now >= 60 and flow_20d_now is not None and flow_20d_now < 0:
        alert_level = "INFO"
        alert_reason = f"板块仍{tier_now}({score_now:.0f}) 但 20 日资金转为净流出({_fmt_cny(flow_20d_now)})"
        suggestion = "REDUCE_POSITION" if action == "BUY" else "MONITOR"

    # Case 4: Score dropped significantly (>15 points) but still HOT
    elif score_at_rec - score_now >= 15 and score_now >= 60:
        alert_level = "INFO"
        alert_reason = f"板块仍{tier_now}但评分下降{score_at_rec - score_now:.0f}pt ({score_at_rec:.0f}→{score_now:.0f})"
        suggestion = "MONITOR"

    return RecAlert(
        rec_id=rec.get("id", ""),
        code=code,
        name=name,
        action=action,
        sector=sector,
        score_at_rec=score_at_rec,
        tier_at_rec=tier_at_rec,
        score_now=score_now,
        tier_now=tier_now,
        flow_20d_at_rec=None,  # not stored in rec, can't compare
        flow_20d_now=flow_20d_now,
        alert_level=alert_level,
        alert_reason=alert_reason,
        suggestion=suggestion,
    )


def check_all() -> list[RecAlert]:
    """Check all open recs. Return list of alerts (including OK ones)."""
    recs = _read_jsonl(_REC_FILE)
    alerts = []
    for rec in recs:
        alert = check_rec(rec)
        if alert:
            alerts.append(alert)
    return alerts


# ─── Output formatters ───────────────────────────────────────────────────

_LEVEL_ICON = {
    "CRITICAL": "🚨",
    "WARNING": "⚠️",
    "INFO": "ℹ️",
    "OK": "✅",
}

_SUGGESTION_CN = {
    "EXIT": "建议清仓退出",
    "DOWNGRADE_TO_WATCH": "建议降级为 WATCH (不再加仓)",
    "REMOVE_FROM_WATCH": "建议移出观察池",
    "REDUCE_POSITION": "建议缩减仓位至下限",
    "MONITOR": "密切关注, 暂不行动",
    "HOLD": "维持当前状态",
}


def print_alerts_table(alerts: list[RecAlert]):
    """Print human-readable table."""
    # Sort: CRITICAL first, then WARNING, then INFO, then OK
    order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2, "OK": 3}
    alerts.sort(key=lambda a: (order.get(a.alert_level, 9), -a.score_at_rec))

    actionable = [a for a in alerts if a.alert_level != "OK"]
    ok_count = len(alerts) - len(actionable)

    print(f"\n{'='*100}")
    print(f"推荐有效性看门狗  ({date.today().isoformat()})  "
          f"总计={len(alerts)} 条  告警={len(actionable)} 条  正常={ok_count} 条")
    print(f"{'='*100}")

    if actionable:
        print(f"\n{'─'*100}")
        print(f"{'级别':<4} {'代码':<12} {'名称':<8} {'原动作':<6} "
              f"{'板块':<18} {'推荐时分':<8} {'当前分':<8} {'建议':<20} {'原因'}")
        print(f"{'─'*100}")
        for a in actionable:
            icon = _LEVEL_ICON.get(a.alert_level, "?")
            sug = _SUGGESTION_CN.get(a.suggestion, a.suggestion)
            print(f" {icon}  {a.code:<12} {a.name:<8} {a.action:<6} "
                  f"{a.sector[:16]:<18} {a.score_at_rec:>6.1f}  {a.score_now:>6.1f}  "
                  f"{sug:<20} {a.alert_reason}")
    else:
        print("\n  ✅ 所有推荐的板块逻辑均未发生降级变化")

    if ok_count > 0:
        print(f"\n  ✅ {ok_count} 条推荐板块状态正常 (仍 HOT, flow 未反转)")
        for a in alerts:
            if a.alert_level == "OK":
                print(f"     {a.code} {a.name} [{a.action}] — "
                      f"{a.sector[:14]} {a.score_at_rec:.0f}→{a.score_now:.0f} OK")


def print_summary(alerts: list[RecAlert]) -> str:
    """One-line summary for cron embedding."""
    critical = [a for a in alerts if a.alert_level == "CRITICAL"]
    warning = [a for a in alerts if a.alert_level == "WARNING"]
    info = [a for a in alerts if a.alert_level == "INFO"]
    ok = [a for a in alerts if a.alert_level == "OK"]

    parts = []
    if critical:
        parts.append(f"🚨{len(critical)}条严重降级(需EXIT)")
    if warning:
        parts.append(f"⚠️{len(warning)}条板块降档(建议WATCH)")
    if info:
        parts.append(f"ℹ️{len(info)}条需关注")
    if ok:
        parts.append(f"✅{len(ok)}条正常")

    summary = " | ".join(parts) if parts else "无 open recs"
    print(summary)
    return summary


# ─── CLI ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Rec watchdog — 推荐有效性检查.")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--summary", action="store_true", help="One-line summary")
    args = ap.parse_args()

    alerts = check_all()

    if args.json:
        print(json.dumps([asdict(a) for a in alerts], ensure_ascii=False, indent=2))
    elif args.summary:
        print_summary(alerts)
    else:
        print_alerts_table(alerts)


if __name__ == "__main__":
    main()
