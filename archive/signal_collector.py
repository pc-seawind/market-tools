#!/usr/bin/env python3
"""signal_collector.py — 短线 (5-20d) 信号层采集器 v2.

针对 v2.5 框架的核心缺陷"信息维度过窄"补齐.
旧框架只用基本面/估值 (1 维), 命中率 18%.

本模块对 (ts_code, date) 拉 7 个**短线驱动**维度:
  1. 龙虎榜 (top_list)            — 游资介入与机构席位
  2. 北向资金 (hsgt_top10)        — 外资偏好
  3. 主力资金流 (moneyflow)       — 大单/特大单净流入
  4. 公告 (anns_d)                 — 业绩/订单/减持/立案
  5. 研报 (report_rc)              — 30d 评级 + 目标价
  6. 融资余额 (margin_detail)     — 杠杆资金动向
  7. 突破结构 (daily kline)       — 60d 横盘 + 温和放量突破 (v2 新增)

加上 macro_guard() (沪深300 vs MA60), 在大盘下行期把所有 GO 降级 NEUTRAL.

每维度返回 (score ∈ {-1, 0, +1}, reason: str). 总分 ∈ [-7, +7].

阈值 (v2):
  ≥ +3   → GO       (短线信号正向)
  0..+2  → NEUTRAL  (信息混合, 不足以下注)
  ≤ -1   → NO_GO    (短线无支撑, 避开)
  + macro_guard 触发: 任何 GO → NEUTRAL_BY_MACRO_GUARD

设计上**不依赖 sector_picks 的 metrics**, 完全独立 — backtest 就是要验证
新维度本身能不能区分胜负, 不掺老变量进来污染.

支持回测: 所有 lookback 都用 trade_date 之前 N 天 (含当日), 严格 backward only,
不会偷看未来.

CLI 用法:
    python3 signal_collector.py 300502.SZ 20260513
    python3 signal_collector.py 300502.SZ 20260513 --json

依赖: tushare.py (REST wrapper), python3 stdlib.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
TUSHARE_CLI = os.path.join(HERE, "tushare.py")


def _ts(api: str, **params) -> dict:
    """调 tushare wrapper, 返回 dict 形式 {fields:[...], items:[[...]]}."""
    cmd = ["python3", TUSHARE_CLI, api]
    for k, v in params.items():
        cmd.append(f"{k}={v}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"fields": [], "items": [], "error": "timeout"}
    if out.returncode != 0:
        return {"fields": [], "items": [], "error": out.stderr.strip()[:200]}
    try:
        body = json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"fields": [], "items": [], "error": "bad json"}
    if body.get("code") != 0:
        return {"fields": [], "items": [], "error": body.get("msg", "unknown")}
    data = body.get("data") or {}
    return {
        "fields": data.get("fields", []),
        "items": data.get("items", []),
        "error": None,
    }


def _to_dicts(resp: dict) -> list[dict]:
    f = resp.get("fields", [])
    return [dict(zip(f, r)) for r in resp.get("items", [])]


def _date_range(end_date: str, lookback_days: int) -> tuple[str, str]:
    """end_date='YYYYMMDD' → (start_date, end_date) tushare style."""
    end = dt.datetime.strptime(end_date, "%Y%m%d").date()
    start = end - dt.timedelta(days=lookback_days)
    return start.strftime("%Y%m%d"), end_date


# -----------------------------------------------------------------------------
# 维度 1: 龙虎榜
# -----------------------------------------------------------------------------
def check_lhb(ts_code: str, date: str, lookback_days: int = 7) -> tuple[int, str]:
    """5 个交易日 ≈ 7 个自然日.

    评分:
      +1: 上榜 ≥ 2 次 + 累计净买入 > 0  (游资 / 机构介入做多)
      -1: 上榜 ≥ 1 次 + 累计净卖出      (出货)
       0: 未上榜 / 中性
    """
    start, end = _date_range(date, lookback_days)
    resp = _ts("top_list", ts_code=ts_code, start_date=start, end_date=end)
    rows = _to_dicts(resp)
    if not rows:
        return 0, "未上榜"

    # net_amount 单位: 元 (tushare 文档)
    nets = [r.get("net_amount") or 0 for r in rows]
    total_net = sum(nets) / 1e8  # 亿元
    n = len(rows)

    if n >= 2 and total_net > 0:
        return +1, f"5d 上榜 {n} 次 / 净买入 {total_net:+.2f}亿"
    if n >= 1 and total_net < 0:
        return -1, f"5d 上榜 {n} 次 / 净卖出 {total_net:+.2f}亿"
    return 0, f"5d 上榜 {n} 次 / 净 {total_net:+.2f}亿 (中性)"


# -----------------------------------------------------------------------------
# 维度 2: 北向资金 (hsgt_top10)
# -----------------------------------------------------------------------------
def check_northbound(ts_code: str, date: str, lookback_days: int = 7) -> tuple[int, str]:
    """北向 top10 持股 (沪深通).

    评分:
      +1: 5d 内进入 top10 ≥ 2 次 + amount 末日 > 首日
      -1: 5d 内 amount 末日 < 首日 * 0.7  (大幅减持)
       0: 中性 / 不在 top10
    """
    start, end = _date_range(date, lookback_days)
    resp = _ts("hsgt_top10", ts_code=ts_code, start_date=start, end_date=end)
    rows = _to_dicts(resp)
    if not rows:
        return 0, "5d 未进北向 top10"

    # 按日期升序
    rows.sort(key=lambda r: r.get("trade_date", ""))
    n = len(rows)
    first = rows[0].get("amount") or 0
    last = rows[-1].get("amount") or 0
    delta_pct = (last - first) / first * 100 if first else 0

    if n >= 2 and last > first:
        return +1, f"5d top10 {n} 次 / amount {first/1e8:.1f}亿→{last/1e8:.1f}亿 ({delta_pct:+.0f}%)"
    if n >= 1 and last < first * 0.7:
        return -1, f"5d top10 {n} 次 / amount {first/1e8:.1f}亿→{last/1e8:.1f}亿 ({delta_pct:+.0f}%, 大幅减仓)"
    return 0, f"5d top10 {n} 次 / amount {delta_pct:+.0f}% (中性)"


# -----------------------------------------------------------------------------
# 维度 3: 主力资金流 (moneyflow)
# -----------------------------------------------------------------------------
def check_main_flow(ts_code: str, date: str, lookback_days: int = 7) -> tuple[int, str]:
    """5d 累计 (大单+特大单) 净流入.

    tushare moneyflow 字段单位: 手 (vol) / 千元 (amount).
    净流入 = (buy_elg_amount - sell_elg_amount) + (buy_lg_amount - sell_lg_amount)

    评分:
      +1: 5d 累计净流入 > +1亿
      -1: 5d 累计净流出 > -1亿
       0: 中间区间
    """
    start, end = _date_range(date, lookback_days)
    resp = _ts("moneyflow", ts_code=ts_code, start_date=start, end_date=end)
    rows = _to_dicts(resp)
    if not rows:
        return 0, "无数据"

    total = 0
    for r in rows:
        elg_net = (r.get("buy_elg_amount") or 0) - (r.get("sell_elg_amount") or 0)
        lg_net = (r.get("buy_lg_amount") or 0) - (r.get("sell_lg_amount") or 0)
        total += elg_net + lg_net

    yi = total / 1e5  # 千元 → 亿元 (1亿 = 10万千元)
    if yi > 1:
        return +1, f"5d 主力净流入 {yi:+.2f}亿"
    if yi < -1:
        return -1, f"5d 主力净流出 {yi:+.2f}亿"
    return 0, f"5d 主力 {yi:+.2f}亿 (中性)"


# -----------------------------------------------------------------------------
# 维度 4: 公告 (anns_d)
# -----------------------------------------------------------------------------
LH_KW_POS = (
    "业绩预增", "业绩快报", "净利润同比增", "中标", "签订", "增持", "回购",
    "重大合同", "战略合作", "新产品", "高管增持", "员工持股",
)
LH_KW_NEG = (
    "业绩预减", "业绩预亏", "亏损", "减持", "拟减持", "立案", "处罚",
    "终止", "暂停", "退市风险", "诉讼", "失信", "高管辞职",
)


def check_announcements(ts_code: str, date: str, lookback_days: int = 14) -> tuple[int, str]:
    """14d 公告关键词扫描.

    评分:
      +1: 利好关键词 ≥ 1 + 无利空
      -1: 利空关键词 ≥ 1
       0: 无明显信号
    """
    start, end = _date_range(date, lookback_days)
    resp = _ts("anns_d", ts_code=ts_code, start_date=start, end_date=end)
    rows = _to_dicts(resp)
    if not rows:
        return 0, "14d 无公告"

    pos_hits, neg_hits = [], []
    for r in rows:
        title = r.get("title") or ""
        for kw in LH_KW_POS:
            if kw in title:
                pos_hits.append((r.get("ann_date"), kw, title[:30]))
                break
        for kw in LH_KW_NEG:
            if kw in title:
                neg_hits.append((r.get("ann_date"), kw, title[:30]))
                break

    if neg_hits:
        sample = neg_hits[0]
        return -1, f"利空 {len(neg_hits)} 条 (e.g. {sample[1]}: {sample[2]})"
    if pos_hits:
        sample = pos_hits[0]
        return +1, f"利好 {len(pos_hits)} 条 (e.g. {sample[1]}: {sample[2]})"
    return 0, f"14d {len(rows)} 条公告 / 无关键词"


# -----------------------------------------------------------------------------
# 维度 5: 研报 (report_rc)
# -----------------------------------------------------------------------------
def check_research(ts_code: str, date: str, lookback_days: int = 30) -> tuple[int, str]:
    """30d 内研报评级 + 目标价变化.

    tushare report_rc 字段: rating (评级 e.g. '买入'/'增持'/'中性'/'减持'),
    quarter, op_pred (营业利润预测), report_date, max_price/min_price (目标价区间).

    评分:
      +1: 30d 内研报数 ≥ 3 + '买入' 或 '增持' 占比 ≥ 70%
      -1: 30d 内出现 '中性'/'减持' 或目标价被下调
       0: 中性
    """
    start, end = _date_range(date, lookback_days)
    resp = _ts("report_rc", ts_code=ts_code, start_date=start, end_date=end)
    rows = _to_dicts(resp)
    if not rows:
        return 0, "30d 无研报"

    n = len(rows)
    bullish = sum(1 for r in rows if (r.get("rating") or "") in ("买入", "增持"))
    bearish = sum(1 for r in rows if (r.get("rating") or "") in ("中性", "减持", "卖出"))

    if n >= 3 and bullish / n >= 0.7:
        return +1, f"30d {n} 篇 / 买入+增持 {bullish}/{n}"
    if bearish > 0:
        return -1, f"30d {n} 篇 / 中性+减持 {bearish}/{n}"
    return 0, f"30d {n} 篇 / 买入 {bullish} / 中性偏空 {bearish} (中性)"


# -----------------------------------------------------------------------------
# 维度 6: 融资余额 (margin_detail)
# -----------------------------------------------------------------------------
def check_margin(ts_code: str, date: str, lookback_days: int = 7) -> tuple[int, str]:
    """5d 融资余额变化 (rzye = 融资余额).

    评分:
      +1: 5d 融资余额 +5% 以上 (杠杆资金加仓)
      -1: 5d 融资余额 -5% 以上 (杠杆资金离场)
       0: 中性
    """
    start, end = _date_range(date, lookback_days)
    resp = _ts("margin_detail", ts_code=ts_code, start_date=start, end_date=end)
    rows = _to_dicts(resp)
    if not rows:
        return 0, "5d 无融资数据"

    rows.sort(key=lambda r: r.get("trade_date", ""))
    first = rows[0].get("rzye") or 0
    last = rows[-1].get("rzye") or 0
    if not first:
        return 0, "首日融资余额=0 / 数据异常"
    delta = (last - first) / first * 100

    if delta > 5:
        return +1, f"5d 融资余额 {first/1e8:.2f}亿→{last/1e8:.2f}亿 ({delta:+.1f}%)"
    if delta < -5:
        return -1, f"5d 融资余额 {first/1e8:.2f}亿→{last/1e8:.2f}亿 ({delta:+.1f}%)"
    return 0, f"5d 融资余额 {delta:+.1f}% (中性)"


# -----------------------------------------------------------------------------
# 维度 7 (v2 新增): 60d 横盘 + 温和放量突破
# -----------------------------------------------------------------------------
def check_breakout(ts_code: str, date: str) -> tuple[int, str]:
    """检测吸筹型启动:
      - 近 60 日箱体振幅 (high_max - low_min) / low_min < 25%
      - 近 5 日成交量 / 前 60 日均量 ∈ [1.5, 3.0]  (温和放量, 不是爆量)
      - 当日收盘 > 箱体上沿 high_max

    评分:
      +1: 三条件全满足
       0: 任一条件不满足
      -1: 已破箱体下沿 (跌破支撑)

    HK 股暂不支持 (字段差异), 直接 0.
    """
    if ts_code.endswith(".HK"):
        return 0, "HK 不支持"

    # 取 decision_date 当日 + 前 110 日 (覆盖 60 个交易日, 节假日 buffer)
    start, end = _date_range(date, 110)
    resp = _ts("daily", ts_code=ts_code, start_date=start, end_date=end)
    rows = _to_dicts(resp)
    if len(rows) < 60:
        return 0, f"daily 数据不足 ({len(rows)}<60)"

    # tushare 默认降序, 排升序
    rows.sort(key=lambda r: r.get("trade_date", ""))

    # 当日 row: 必须 == date, 否则没数据
    today_row = rows[-1]
    if today_row.get("trade_date") != date:
        return 0, f"无当日 {date} 数据 (latest={today_row.get('trade_date')})"

    # 历史窗口 = 倒数 [60..1] 天 (排除当日)
    prior = rows[-61:-1]
    if len(prior) < 55:
        return 0, f"60d 窗口数据不足 ({len(prior)})"

    highs = [r.get("high") or 0 for r in prior]
    lows  = [r.get("low") or 0 for r in prior]
    vols  = [r.get("vol") or 0 for r in prior]
    box_high = max(highs)
    box_low  = min(lows)
    if box_low <= 0:
        return 0, "数据异常 (box_low<=0)"
    box_width = (box_high - box_low) / box_low * 100

    # 近 5 日 vol vs 前 60 日均量 (前 60 日不含近5)
    if len(prior) >= 60:
        recent_vols = vols[-5:]
        baseline_vols = vols[:-5]
    else:
        recent_vols = vols[-5:]
        baseline_vols = vols
    avg_recent = sum(recent_vols) / max(1, len(recent_vols))
    avg_base   = sum(baseline_vols) / max(1, len(baseline_vols))
    vol_ratio = avg_recent / avg_base if avg_base > 0 else 0

    today_close = today_row.get("close") or 0

    # 评分
    cond_box   = box_width < 25
    cond_vol   = 1.5 <= vol_ratio <= 3.0
    cond_break = today_close > box_high

    if today_close < box_low * 0.97:
        return -1, f"跌破箱体下沿 ({box_low:.2f}) close={today_close:.2f}"

    if cond_box and cond_vol and cond_break:
        return +1, (f"60d 箱体 [{box_low:.2f}, {box_high:.2f}] 振幅 {box_width:.1f}% / "
                    f"vol_ratio {vol_ratio:.2f}x / 突破收盘 {today_close:.2f}")

    # 详细解释为什么不命中
    miss = []
    if not cond_box:
        miss.append(f"box 振幅 {box_width:.1f}% ≥25%")
    if not cond_vol:
        miss.append(f"vol_ratio {vol_ratio:.2f}x ∉ [1.5,3.0]")
    if not cond_break:
        miss.append(f"close {today_close:.2f} ≤ box_high {box_high:.2f}")
    return 0, " / ".join(miss)


# -----------------------------------------------------------------------------
# 宏观风控 (v2 新增): 沪深300 vs MA60
# -----------------------------------------------------------------------------
_MACRO_CACHE: dict[str, tuple[float, float]] = {}  # date → (close, ma60)


def macro_guard(date: str) -> tuple[bool, str]:
    """returns (guard_active, reason).

    guard_active=True ↔ 沪深300 当日收盘 < MA60, 大盘趋势走弱, 任何 GO 应降级.
    """
    if date in _MACRO_CACHE:
        c, m = _MACRO_CACHE[date]
        active = c < m
        return active, f"沪深300 close={c:.2f} {'<' if active else '≥'} MA60={m:.2f}"

    # 拉前 90 日 + 当日 = 60 trading days for MA
    start, end = _date_range(date, 100)
    resp = _ts("index_daily", ts_code="000300.SH", start_date=start, end_date=end)
    rows = _to_dicts(resp)
    if len(rows) < 60:
        return False, f"沪深300 数据不足 ({len(rows)}<60), 跳过 macro_guard"

    rows.sort(key=lambda r: r.get("trade_date", ""))
    if rows[-1].get("trade_date") != date:
        return False, f"沪深300 当日 {date} 无数据 (latest={rows[-1].get('trade_date')})"

    today_close = rows[-1].get("close") or 0
    last60 = rows[-60:]
    ma60 = sum((r.get("close") or 0) for r in last60) / 60
    _MACRO_CACHE[date] = (today_close, ma60)
    active = today_close < ma60
    return active, f"沪深300 close={today_close:.2f} {'<' if active else '≥'} MA60={ma60:.2f}"


# -----------------------------------------------------------------------------
# 主入口: 7 维聚合 + macro guard
# -----------------------------------------------------------------------------
@dataclass
class SignalReport:
    ts_code: str
    date: str
    lhb: tuple[int, str]
    northbound: tuple[int, str]
    main_flow: tuple[int, str]
    announcements: tuple[int, str]
    research: tuple[int, str]
    margin: tuple[int, str]
    breakout: tuple[int, str] = (0, "")
    total_score: int = 0
    verdict: str = ""
    macro_guard_active: bool = False
    macro_guard_reason: str = ""

    def __post_init__(self):
        self.total_score = (
            self.lhb[0] + self.northbound[0] + self.main_flow[0]
            + self.announcements[0] + self.research[0] + self.margin[0]
            + self.breakout[0]
        )
        if self.total_score >= 3:
            self.verdict = "GO"
        elif self.total_score <= -1:
            self.verdict = "NO_GO"
        else:
            self.verdict = "NEUTRAL"
        # macro guard: GO → NEUTRAL_BY_MACRO_GUARD
        if self.macro_guard_active and self.verdict == "GO":
            self.verdict = "NEUTRAL_BY_MACRO_GUARD"


def collect(ts_code: str, date: str, apply_macro_guard: bool = True) -> SignalReport:
    if apply_macro_guard:
        guard_on, guard_reason = macro_guard(date)
    else:
        guard_on, guard_reason = False, "macro_guard disabled"

    r = SignalReport(
        ts_code=ts_code,
        date=date,
        lhb=check_lhb(ts_code, date),
        northbound=check_northbound(ts_code, date),
        main_flow=check_main_flow(ts_code, date),
        announcements=check_announcements(ts_code, date),
        research=check_research(ts_code, date),
        margin=check_margin(ts_code, date),
        breakout=check_breakout(ts_code, date),
        macro_guard_active=guard_on,
        macro_guard_reason=guard_reason,
    )
    return r


def _print_report(r: SignalReport, indent: str = ""):
    icon = {
        "GO": "✅",
        "NEUTRAL": "🟡",
        "NO_GO": "⛔",
        "NEUTRAL_BY_MACRO_GUARD": "🛑",
    }[r.verdict]
    print(f"{indent}━━━ {r.ts_code} @ {r.date} {icon} {r.verdict} ({r.total_score:+d}/7) ━━━")
    if r.macro_guard_active:
        print(f"{indent}  ⚠️ macro_guard 触发: {r.macro_guard_reason}")
    rows = [
        ("龙虎榜", r.lhb),
        ("北向资金", r.northbound),
        ("主力流", r.main_flow),
        ("公告", r.announcements),
        ("研报", r.research),
        ("融资余额", r.margin),
        ("突破结构", r.breakout),
    ]
    for name, (score, reason) in rows:
        sym = "+1" if score > 0 else ("-1" if score < 0 else " 0")
        print(f"{indent}  [{sym}] {name:8s}  {reason}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ts_code", help="e.g. 300502.SZ")
    ap.add_argument("date", help="trade_date YYYYMMDD")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    r = collect(args.ts_code, args.date)
    if args.json:
        print(json.dumps(asdict(r), ensure_ascii=False, indent=2))
    else:
        _print_report(r)


if __name__ == "__main__":
    main()
