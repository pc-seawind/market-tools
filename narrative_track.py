"""narrative_track.py — narrative_radar 后期追踪 (T+N 验证).

镜像 rec_log.py 的设计, 但每条 event 可能挂多个 ticker, 所以是
(event_ts, ticker_code) 二元组级别的 perf 记录.

文件:
  narrative_events.jsonl       agent 推 events (radar 写, 这里只读)
  narrative_perf.jsonl         本脚本 append. 每天为每个 (open event, ticker) append 一行

每条 perf record schema:
  {
    "event_ts":          "2026-05-23T19:38:51+08:00",   # 锚定原 event (radar 写入时刻)
    "event_trade_date":  "20260523",                     # radar 收集日 (我加进 events 那天)
    "event_pub_date":    "20260424",                     # 新闻原始发布日 (baseline 锚)
    "baseline_date":     "20260424",                     # 实际用作 baseline 的日期 (=pub_date or trade_date)
    "event_score":       3,
    "event_track":       "AI",
    "event_subdomain":   "ai__compute_chip",
    "event_title":       "...",                          # 80 字截断, 方便人看
    "code":              "688256.SH",
    "name":              "寒武纪",
    "side":              "+" / "-",
    "verify_ts":         "2026-05-24T18:45:00+08:00",
    "verify_date":       "20260524",
    "days_since_event":  30,                             # 自然日, 从 baseline_date 起算 (不是 trade_date)
    "baseline_price":    510.20,                         # baseline_date 当日收盘 (T+0)
    "current_price":     525.30,
    "absolute_pct":      +2.96,                          # vs baseline
    "benchmark":         "000300.SH" / "HSI",
    "benchmark_baseline": 4845.10,
    "benchmark_current":  4870.20,
    "benchmark_pct":     +0.52,
    "excess_pct":        +2.44,                          # absolute - benchmark (大盘), beta-adjusted
    "hit":               True,                           # side=+ → excess>0; side=- → excess<0
    "sector_name":       "元器件",                       # 申万 L1 行业 (HK/US 为 null)
    "sector_pct":        +1.85,                          # 同行业等权平均收益
    "sector_n_members":  47,                             # 实际参与平均的成员数
    "excess_vs_sector":  +1.11,                          # absolute - sector, 行业-adjusted alpha
    "hit_vs_sector":     True,                           # side=+ → excess_vs_sector>0
    "hit_strict":        True,                           # hit AND hit_vs_sector — 真 alpha (剔除 beta + 行业轮动)
  }

baseline 锚定原则:
  baseline_date = event.pub_date (如果 backfill 过) 否则 event.trade_date
  这样真实新闻发布日做 anchor, 不被"我什么时候 cron 抓到"扰动.

T+N 里程碑: 5/10/20/40 自然日. 超过 60 自然日的 event 不再 verify (close).
注: 阈值放宽至 60 (原 45) — backfill 后部分 event pub_date 较老 (如 4/22 / 4/24).

CLI:
  narrative_track.py verify [--all | --event-ts X] [--date YYYYMMDD]
  narrative_track.py report [--weeks N]
  narrative_track.py doc [--weeks N]
  narrative_track.py event_report --event-ts X
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_EVENTS_PATH = _HERE / "narrative_events.jsonl"
_PERF_PATH = _HERE / "narrative_perf.jsonl"
_TUSHARE = _HERE / "tushare.py"

CN_TZ = dt.timezone(dt.timedelta(hours=8))

MAX_HORIZON_DAYS = 60  # 到 T+60 自然日 (~T+40 交易日) 停止验证 (放宽以容纳 backfill 的老 event)

# W21 v3 §4 横截面证据: alpha 在 T+15-T+30 才真正 unlock. T+5/T+10 是噪音区.
# MILESTONE_DAYS 加上 14/21/28 三个 main-signal 窗口, 旧 5/10 保留兼容性但 doc 标 noise.
MILESTONE_DAYS = [5, 10, 14, 21, 28, 40]

# W21 v3 §6 #3: 把窗口分成 noise / early / main / extended 四段, 主信号窗口 = main.
WINDOW_BUCKETS = [
    ("noise",    0,  7,  "D0-7 噪音区 (机构未消化, 不计入主判定)"),
    ("early",    8, 13,  "D8-13 早期消化 (median 仍可能负)"),
    ("main",    14, 28,  "D14-28 主信号窗口 (alpha 在此 unlock — 主判定依据)"),
    ("extended",29, 60,  "D29+ 延伸期 (赢家继续放大)"),
]
MAIN_SIGNAL_BUCKET = "main"  # 周报 hit_rate 头条数据来源


def _baseline_date_of(event: dict) -> str:
    """统一从 event 取 baseline 锚定日期: pub_date > trade_date.

    backfill 过的 event 有 pub_date (新闻原始发布日), 是更准的市场反应起点;
    没 backfill 的回退到 trade_date (radar 收集日, 旧逻辑).
    """
    pd = event.get("pub_date")
    if pd and isinstance(pd, str) and len(pd) == 8 and pd.isdigit():
        return pd
    return event.get("trade_date", "")

BENCHMARK_A = "000300.SH"  # CSI300
BENCHMARK_HK = "HSI"       # 恒指 (走 index_global)


# ─── I/O ─────────────────────────────────────────────────────────────────

def _ts_csv(api: str, **params) -> list[dict[str, str]]:
    args = ["python3", str(_TUSHARE), api]
    for k, v in params.items():
        args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ─── price fetchers ─────────────────────────────────────────────────────

def _fetch_stock_close(ts_code: str, trade_date: Optional[str] = None) -> Optional[float]:
    """单只股票的指定日期 close. trade_date=None 取最新."""
    is_hk = ts_code.upper().endswith(".HK")
    api = "hk_daily" if is_hk else "daily"

    rows = _ts_csv(api, ts_code=ts_code, trade_date=trade_date) if trade_date \
        else _ts_csv(api, ts_code=ts_code)

    if rows:
        rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)
        try:
            return float(rows[0]["close"])
        except (KeyError, ValueError):
            pass

    # fallback: quote_sources daily_bars
    try:
        from quote_sources import daily_bars
        bars = daily_bars(ts_code, days=10)
        if not bars:
            return None
        if trade_date:
            for b in bars:
                if str(b.get("trade_date", "")).replace("-", "") == trade_date:
                    return float(b["close"])
            # 找不到精确日期, 返回 ≤trade_date 最近的
            cands = [b for b in bars if str(b.get("trade_date", "")).replace("-", "") <= trade_date]
            if cands:
                return float(cands[-1]["close"])
            return None
        return float(bars[-1]["close"])
    except Exception:
        return None


def _fetch_benchmark_close(market: str, trade_date: Optional[str] = None) -> tuple[str, Optional[float]]:
    """benchmark close. market='A' → CSI300 via index_daily; 'HK' → HSI via index_global.

    trade_date 找不到 (周末/节假日) 时, fallback 到 ≤trade_date 的最近交易日.
    """
    if market == "HK":
        bench = BENCHMARK_HK
        api = "index_global"
    else:
        bench = BENCHMARK_A
        api = "index_daily"

    # 1) 精确 trade_date
    if trade_date:
        rows = _ts_csv(api, ts_code=bench, trade_date=trade_date)
        if rows:
            try:
                return bench, float(rows[0]["close"])
            except (KeyError, ValueError):
                pass
        # 2) fallback: 拉最近 7 天, 取 ≤trade_date 的最近一条
        start = (dt.datetime.strptime(trade_date, "%Y%m%d").date() - dt.timedelta(days=10)).strftime("%Y%m%d")
        rows = _ts_csv(api, ts_code=bench, start_date=start, end_date=trade_date)
        if rows:
            rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)
            try:
                return bench, float(rows[0]["close"])
            except (KeyError, ValueError):
                pass
        return bench, None

    # 无 trade_date: 取最新
    rows = _ts_csv(api, ts_code=bench)
    if not rows:
        return bench, None
    rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)
    try:
        return bench, float(rows[0]["close"])
    except (KeyError, ValueError):
        return bench, None


def _market_of(ts_code: str) -> str:
    return "HK" if ts_code.upper().endswith(".HK") else "A"


# ─── session-aware base price resolution (authoritative, shared) ──────────
# 两条管道 (realtime verify + backtest ingest) 共享这套 base 口径, 避免双份逻辑。
# session 语义 (用户 2026-05-31 规则):
#   - 'post'(默认): 盘后/收盘后消息 → 市场次一交易日才反应 → base = pub_date 次一
#     交易日的 **开盘价**。捕捉消息引发的跳空, 避免用消息前收盘价低估/高估。
#   - 'intraday': 盘中突发消息 → 消息当时已在交易 → 近似用 pub_date 当日(或之前最近
#     交易日)的 **收盘价** (无分钟数据, 收盘是当日终值的最佳近似)。
#   - 'pre': 盘前消息 → 当日开盘即反应 → base = pub_date 当日(若交易日)否则次一交易日
#     的 **开盘价**。

def _price_on_or_before(ts_code: str, target_date: str, lookback: int = 12,
                        price_field: str = "close") -> Optional[tuple[str, float]]:
    """(actual_trade_date, price) — target_date 当日或之前最近交易日的价格。"""
    is_hk = ts_code.upper().endswith(".HK")
    api = "hk_daily" if is_hk else "daily"
    try:
        d = dt.datetime.strptime(target_date, "%Y%m%d").date()
    except ValueError:
        return None
    start = (d - dt.timedelta(days=lookback)).strftime("%Y%m%d")
    rows = _ts_csv(api, ts_code=ts_code, start_date=start, end_date=target_date,
                   fields=f"trade_date,{price_field}")
    if not rows:
        return None
    rows = [r for r in rows if r.get("trade_date", "") <= target_date]
    if not rows:
        return None
    rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)
    try:
        return rows[0]["trade_date"], float(rows[0][price_field])
    except (KeyError, ValueError):
        return None


def _price_on_or_after(ts_code: str, target_date: str, lookahead: int = 12,
                       price_field: str = "open") -> Optional[tuple[str, float]]:
    """(actual_trade_date, price) — target_date 当日或之后最近交易日的价格。"""
    is_hk = ts_code.upper().endswith(".HK")
    api = "hk_daily" if is_hk else "daily"
    try:
        d = dt.datetime.strptime(target_date, "%Y%m%d").date()
    except ValueError:
        return None
    end = (d + dt.timedelta(days=lookahead)).strftime("%Y%m%d")
    rows = _ts_csv(api, ts_code=ts_code, start_date=target_date, end_date=end,
                   fields=f"trade_date,{price_field}")
    if not rows:
        return None
    rows = [r for r in rows if r.get("trade_date", "") >= target_date]
    if not rows:
        return None
    rows.sort(key=lambda x: x.get("trade_date", ""))
    try:
        return rows[0]["trade_date"], float(rows[0][price_field])
    except (KeyError, ValueError):
        return None


def _index_price_on_or_after(bench: str, api: str, target_date: str,
                             lookahead: int = 12,
                             price_field: str = "open") -> Optional[tuple[str, float]]:
    """指数 target_date 当日或之后最近交易日的 open/close。"""
    try:
        d = dt.datetime.strptime(target_date, "%Y%m%d").date()
    except ValueError:
        return None
    end = (d + dt.timedelta(days=lookahead)).strftime("%Y%m%d")
    rows = _ts_csv(api, ts_code=bench, start_date=target_date, end_date=end)
    if not rows:
        return None
    rows = [r for r in rows if r.get("trade_date", "") >= target_date]
    if not rows:
        return None
    rows.sort(key=lambda x: x.get("trade_date", ""))
    try:
        return rows[0]["trade_date"], float(rows[0].get(price_field, rows[0].get("close")))
    except (KeyError, ValueError, TypeError):
        return None


def _index_close_on_or_before(bench: str, api: str, target_date: str,
                              lookback: int = 12) -> Optional[float]:
    """指数 close on-or-before target_date。"""
    try:
        d = dt.datetime.strptime(target_date, "%Y%m%d").date()
    except ValueError:
        return None
    start = (d - dt.timedelta(days=lookback)).strftime("%Y%m%d")
    rows = _ts_csv(api, ts_code=bench, start_date=start, end_date=target_date)
    if not rows:
        return None
    rows = [r for r in rows if r.get("trade_date", "") <= target_date]
    if not rows:
        return None
    rows.sort(key=lambda x: x.get("trade_date", ""), reverse=True)
    try:
        return float(rows[0]["close"])
    except (KeyError, ValueError):
        return None


def _session_of(event: dict) -> str:
    return (event.get("session") or "post").lower()


def resolve_base(code: str, pub_date: str, session: str
                 ) -> Optional[tuple[str, float]]:
    """根据 session 决定 (actual_base_date, base_price)。权威 base 口径。"""
    try:
        pd_d = dt.datetime.strptime(pub_date, "%Y%m%d").date()
    except ValueError:
        return None
    if session == "intraday":
        return _price_on_or_before(code, pub_date, price_field="close")
    if session == "pre":
        return _price_on_or_after(code, pub_date, price_field="open")
    # default 'post': 次一自然日起的第一个交易日 open
    nxt = (pd_d + dt.timedelta(days=1)).strftime("%Y%m%d")
    return _price_on_or_after(code, nxt, price_field="open")


def resolve_bench_base(bench: str, bapi: str, actual_base_date: str,
                       session: str) -> Optional[float]:
    """benchmark 在 actual_base_date 当天的价格, 取价口径与 stock base 对齐。"""
    field = "close" if session == "intraday" else "open"
    r = _index_price_on_or_after(bench, bapi, actual_base_date, price_field=field)
    if r:
        return r[1]
    return _index_close_on_or_before(bench, bapi, actual_base_date)


# ─── core verify ────────────────────────────────────────────────────────

def _existing_perf_keys() -> set[tuple[str, str, int]]:
    """避免重复 append 同一个 (event_ts, code, days_since_event)."""
    seen = set()
    for p in _read_jsonl(_PERF_PATH):
        seen.add((p.get("event_ts", ""), p.get("code", ""), p.get("days_since_event", -1)))
    return seen


def _baseline_for(event_ts: str, code: str) -> Optional[float]:
    """从 perf 历史里捞已经存的 baseline (avoid 重复拉 tushare)."""
    for p in _read_jsonl(_PERF_PATH):
        if p.get("event_ts") == event_ts and p.get("code") == code:
            v = p.get("baseline_price")
            if v is not None:
                return float(v)
    return None


def _benchmark_baseline_for(event_ts: str, code: str) -> Optional[float]:
    for p in _read_jsonl(_PERF_PATH):
        if p.get("event_ts") == event_ts and p.get("code") == code:
            v = p.get("benchmark_baseline")
            if v is not None:
                return float(v)
    return None


def _truncate(s: str, n: int = 80) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + "…"


def verify_event_ticker(event: dict, ticker: dict, verify_date: Optional[str] = None,
                        cached_baseline: dict | None = None,
                        seen_keys: set | None = None) -> Optional[dict]:
    """计算 (event, ticker) 一对的 T+N perf, append 到 narrative_perf.jsonl.

    幂等: 同 (event_ts, code, days_since_event) 已存在则跳过.
    cached_baseline: dict[(event_ts, code)] -> baseline_price 提前缓存避免重复拉.
    """
    event_ts = event.get("ts", "")
    event_trade_date = event.get("trade_date", "")
    pub_date = _baseline_date_of(event)  # 消息锚定日 (pub_date > trade_date)
    event_pub_date = event.get("pub_date", "")  # 可能为空 (未 backfill)
    session = _session_of(event)  # 'post'(默认)/'intraday'/'pre'
    code = ticker.get("code", "")
    if not event_ts or not pub_date or not code:
        return None

    market = _market_of(code)

    # ── session-aware base: (actual_base_date, base_price) ──
    # baseline 不再是 pub_date 当日收盘, 而是按 session 解析的真实落地交易日 + 取价。
    # post → 次日 open, intraday → 当日 close, pre → 当日 open。
    cache_key = (event_ts, code)
    base_resolved = None
    if cached_baseline and cache_key in cached_baseline:
        base_resolved = cached_baseline[cache_key]
    if base_resolved is None:
        base_resolved = resolve_base(code, pub_date, session)
        if base_resolved is None:
            return None
        if cached_baseline is not None:
            cached_baseline[cache_key] = base_resolved
    baseline_date, baseline = base_resolved  # actual_base_date, base_price

    # days_since 从 actual_base_date 起算 (买入后 N 自然日, 而非消息后)
    try:
        baseline_d = dt.datetime.strptime(baseline_date, "%Y%m%d").date()
    except ValueError:
        return None
    today = dt.date.today() if not verify_date else dt.datetime.strptime(verify_date, "%Y%m%d").date()
    days_since = (today - baseline_d).days
    if days_since < 0 or days_since > MAX_HORIZON_DAYS:
        return None  # 未来 / 已过期

    # 幂等检查
    key = (event_ts, code, days_since)
    if seen_keys is not None and key in seen_keys:
        return None

    # current = verify_date close (None = 最新)
    current = _fetch_stock_close(code, trade_date=verify_date)
    if current is None:
        return None

    # benchmark — base 对齐到 actual_base_date, 取价口径与 stock 一致 (session-aware)
    if market == "HK":
        bench_name, bapi = BENCHMARK_HK, "index_global"
    else:
        bench_name, bapi = BENCHMARK_A, "index_daily"
    bbk = ("bench", bench_name, baseline_date, session)
    bench_baseline = None
    if cached_baseline and bbk in cached_baseline:
        bench_baseline = cached_baseline[bbk]
    if bench_baseline is None:
        bench_baseline = resolve_bench_base(bench_name, bapi, baseline_date, session)
        if cached_baseline is not None and bench_baseline is not None:
            cached_baseline[bbk] = bench_baseline
    _, bench_current = _fetch_benchmark_close(market, trade_date=verify_date)

    abs_pct = (current / baseline - 1) * 100 if baseline else 0.0
    bench_pct = ((bench_current / bench_baseline - 1) * 100) if (bench_baseline and bench_current) else None
    excess_pct = (abs_pct - bench_pct) if bench_pct is not None else None

    # 板块对照 (申万行业等权), 仅 A 股. 失败不阻塞 perf 写入。
    sector_name: Optional[str] = None
    sector_pct: Optional[float] = None
    excess_vs_sector: Optional[float] = None
    sector_n_members: Optional[int] = None
    if market == "A":
        try:
            from narrative_sector_bench import compute_sector_perf
            sp = compute_sector_perf(code, baseline_date, today.strftime("%Y%m%d"))
            if sp:
                sector_name = sp.get("sector_name")
                sector_pct = sp.get("sector_pct")
                sector_n_members = sp.get("n_members")
                if sector_pct is not None:
                    excess_vs_sector = abs_pct - sector_pct
        except Exception:
            pass

    # hit 判定
    side = ticker.get("side", "+")
    if excess_pct is not None:
        hit = (side == "+" and excess_pct > 0) or (side == "-" and excess_pct < 0)
    else:
        hit = (side == "+" and abs_pct > 0) or (side == "-" and abs_pct < 0)

    # hit_vs_sector: 个股是否跑赢板块 (side=+) 或跑输板块 (side=-)
    hit_vs_sector: Optional[bool] = None
    if excess_vs_sector is not None:
        hit_vs_sector = (side == "+" and excess_vs_sector > 0) or \
                        (side == "-" and excess_vs_sector < 0)

    # hit_strict: 同时跑赢大盘 + 板块 = narrative 真有 alpha (剔除 beta + 行业轮动)
    if hit_vs_sector is not None and excess_pct is not None:
        hit_strict = bool(hit) and bool(hit_vs_sector)
    else:
        hit_strict = None  # 数据不全, 不下结论

    perf = {
        "event_ts":          event_ts,
        "event_trade_date":  event_trade_date,
        "event_pub_date":    event_pub_date,
        "baseline_date":     baseline_date,
        "event_score":       event.get("score"),
        "event_track":       event.get("track"),
        "event_subdomain":   event.get("subdomain"),
        "event_title":       _truncate(event.get("title", ""), 80),
        "code":              code,
        "name":              ticker.get("name", ""),
        "side":              side,
        "verify_ts":         dt.datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "verify_date":       today.strftime("%Y%m%d"),
        "days_since_event":  days_since,
        "baseline_price":    round(baseline, 4),
        "current_price":     round(current, 4),
        "absolute_pct":      round(abs_pct, 2),
        "benchmark":         bench_name,
        "benchmark_baseline": round(bench_baseline, 4) if bench_baseline else None,
        "benchmark_current":  round(bench_current, 4) if bench_current else None,
        "benchmark_pct":     round(bench_pct, 2) if bench_pct is not None else None,
        "excess_pct":        round(excess_pct, 2) if excess_pct is not None else None,
        "hit":               hit,
        "sector_name":       sector_name,
        "sector_pct":        round(sector_pct, 2) if sector_pct is not None else None,
        "sector_n_members":  sector_n_members,
        "excess_vs_sector":  round(excess_vs_sector, 2) if excess_vs_sector is not None else None,
        "hit_vs_sector":     hit_vs_sector,
        "hit_strict":        hit_strict,
    }
    _append_jsonl(_PERF_PATH, perf)
    if seen_keys is not None:
        seen_keys.add(key)
    return perf


def verify_all(verify_date: Optional[str] = None) -> dict[str, int]:
    """跑所有 open event × ticker. 返回统计."""
    events = _read_jsonl(_EVENTS_PATH)
    seen = _existing_perf_keys()
    cached_baseline: dict = {}  # stock key→(date,price) tuple; bench key→float
    stats = {
        "events_total": len(events),
        "events_open": 0,
        "events_expired": 0,
        "ticker_pairs_total": 0,
        "verified": 0,
        "skipped_dup": 0,
        "fetch_failed": 0,
    }

    today = dt.date.today() if not verify_date else dt.datetime.strptime(verify_date, "%Y%m%d").date()

    for event in events:
        # 用 pub_date 做粗过滤 (实际 base 由 session 决定, 可能晚 1 交易日;
        # 这里留 +3 天宽限避免边界 event 被误判过期)。精确的 days_since 与幂等
        # 都在 verify_event_ticker 内基于 actual_base_date 计算。
        pub_date = _baseline_date_of(event)
        try:
            pub_d = dt.datetime.strptime(pub_date, "%Y%m%d").date()
        except ValueError:
            continue
        days_from_pub = (today - pub_d).days
        if days_from_pub > MAX_HORIZON_DAYS + 3:
            stats["events_expired"] += 1
            continue
        if days_from_pub < 0:
            continue
        stats["events_open"] += 1

        for ticker in event.get("tickers", []):
            stats["ticker_pairs_total"] += 1
            # 幂等交给 verify_event_ticker (它基于 actual_base_date 算 key);
            # 返回 None 既可能是 dup 也可能是 fetch_failed, 分别计数。
            before = len(seen)
            perf = verify_event_ticker(event, ticker, verify_date=verify_date,
                                       cached_baseline=cached_baseline, seen_keys=seen)
            if perf:
                stats["verified"] += 1
            elif len(seen) == before:
                # seen 没增长 → 要么 dup (key 已在 seen) 要么 fetch_failed。
                code = ticker.get("code", "")
                # 用 actual base 重新推 days 太重, 这里只能合并计 (近似)。
                stats["skipped_dup"] += 1
    return stats


# ─── report aggregation ────────────────────────────────────────────────

def _milestone_perfs_by_ticker(perfs: list[dict]) -> dict:
    """对每个 (event_ts, code) 聚合各 milestone 的最优 perf record."""
    by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in perfs:
        by_pair[(p["event_ts"], p["code"])].append(p)

    out = {}
    for pair, plist in by_pair.items():
        plist.sort(key=lambda x: x["days_since_event"])
        per_milestone = {}
        for ms in MILESTONE_DAYS:
            # 取 days_since_event >= ms 的最早一条 (T+5 = >=5 第一天的快照)
            cands = [p for p in plist if p["days_since_event"] >= ms]
            if cands:
                per_milestone[f"T+{ms}"] = cands[0]
        out[pair] = {"latest": plist[-1], "by_milestone": per_milestone, "all": plist}
    return out


def _enrich_perf_with_event_meta(perfs: list[dict]) -> list[dict]:
    """把 event 上的 event_type / late_stage / effective_score 字段映射到 perf 行.

    perf 文件本身不重写 — 只在内存里 enrich, 报告时用. 这样 narrative_radar
    reclassify 之后立刻生效, 不用回写 perf jsonl.
    """
    events = {e.get("ts"): e for e in _read_jsonl(_EVENTS_PATH)}
    for p in perfs:
        ev = events.get(p.get("event_ts"))
        if not ev:
            continue
        p["event_type"] = ev.get("event_type", "other")
        p["late_stage"] = bool(ev.get("late_stage"))
        p["effective_score"] = ev.get("effective_score", ev.get("score"))
    return perfs


def _bucket_for_days(days: int) -> str:
    for name, lo, hi, _ in WINDOW_BUCKETS:
        if lo <= days <= hi:
            return name
    return "out_of_range"


def report(weeks: int = 4) -> dict[str, Any]:
    """聚合 hit rate / 中位 excess / 失败案例.

    since 过滤用 baseline_date (pub_date 优先), 这样 backfill 后真新闻发布日老
    但 cron 收集日近的 event 也会被正确归类.

    W21 v3 §6 改进:
      * 新增 by_window_bucket 聚合 (noise/early/main/extended), 主判定看 main
      * 新增 by_event_type / by_late_stage 聚合
      * by_subdomain 按 main 窗口 hit_rate 排序 (不是 sample 数)
    """
    perfs = _read_jsonl(_PERF_PATH)
    perfs = _enrich_perf_with_event_meta(perfs)
    since = dt.date.today() - dt.timedelta(weeks=weeks)

    def _filter_date(p):
        # baseline_date 优先, fallback event_trade_date (兼容老数据)
        d = p.get("baseline_date") or p.get("event_trade_date")
        try:
            return dt.datetime.strptime(d, "%Y%m%d").date() >= since
        except (ValueError, TypeError):
            return False

    perfs = [p for p in perfs if _filter_date(p)]

    by_pair = _milestone_perfs_by_ticker(perfs)

    # group by milestone
    out: dict = {
        "since": since.isoformat(),
        "ticker_pairs": len(by_pair),
        "events_covered": len(set(p[0] for p in by_pair)),
        "by_milestone": {},
        "by_window_bucket": {},   # W21 v3 §6 #3: noise/early/main/extended
        "by_score": {},
        "by_track": {},
        "by_subdomain": {},
        "by_event_type": {},      # W21 v3 §6 #2
        "by_late_stage": {},      # W21 v3 §6 #1
        "top_winners": [],
        "top_losers": [],
        "main_signal_summary": None,  # 头条 = main 窗口聚合
    }

    for ms in MILESTONE_DAYS:
        key = f"T+{ms}"
        rows = []
        for pair, agg in by_pair.items():
            if key in agg["by_milestone"]:
                rows.append(agg["by_milestone"][key])
        if not rows:
            continue
        hits = sum(1 for r in rows if r.get("hit"))
        excesses = [r["excess_pct"] for r in rows if r.get("excess_pct") is not None]
        absolutes = [r["absolute_pct"] for r in rows if r.get("absolute_pct") is not None]
        # strict (剔除 beta + 行业): 仅纳入有 sector 数据的样本
        strict_rows = [r for r in rows if r.get("hit_strict") is not None]
        strict_hits = sum(1 for r in strict_rows if r.get("hit_strict"))
        excess_vs_sector = [r["excess_vs_sector"] for r in rows if r.get("excess_vs_sector") is not None]
        out["by_milestone"][key] = {
            "samples": len(rows),
            "hits": hits,
            "hit_rate": round(hits / len(rows) * 100, 1),
            "median_excess_pct": round(sorted(excesses)[len(excesses) // 2], 2) if excesses else None,
            "avg_absolute_pct": round(sum(absolutes) / len(absolutes), 2) if absolutes else None,
            "strict_samples": len(strict_rows),
            "strict_hits": strict_hits,
            "strict_rate": round(strict_hits / len(strict_rows) * 100, 1) if strict_rows else None,
            "median_excess_vs_sector": round(sorted(excess_vs_sector)[len(excess_vs_sector) // 2], 2) if excess_vs_sector else None,
            "is_main_signal": 14 <= ms <= 28,
            "is_noise": ms <= 7,
        }

    # W21 v3 §6 #3: by_window_bucket — 把所有 perf record 按 days 落到 bucket
    for bucket, lo, hi, label in WINDOW_BUCKETS:
        rows = [p for p in perfs if lo <= p["days_since_event"] <= hi]
        # 一个 (event,ticker) 可能在 bucket 内有多条 — 取最新一条 avoid 重复
        latest_in_bucket: dict[tuple[str, str], dict] = {}
        for r in rows:
            k = (r["event_ts"], r["code"])
            old = latest_in_bucket.get(k)
            if old is None or r["days_since_event"] > old["days_since_event"]:
                latest_in_bucket[k] = r
        rows = list(latest_in_bucket.values())
        if not rows:
            out["by_window_bucket"][bucket] = {
                "label": label, "lo": lo, "hi": hi, "samples": 0, "hits": 0,
                "hit_rate": 0, "median_excess_pct": None, "mean_excess_pct": None,
                "strict_samples": 0, "strict_hits": 0, "strict_rate": None,
                "median_excess_vs_sector": None, "mean_excess_vs_sector": None,
            }
            continue
        hits = sum(1 for r in rows if r.get("hit"))
        excesses = [r["excess_pct"] for r in rows if r.get("excess_pct") is not None]
        # strict
        strict_rows = [r for r in rows if r.get("hit_strict") is not None]
        strict_hits = sum(1 for r in strict_rows if r.get("hit_strict"))
        excess_vs_sector = [r["excess_vs_sector"] for r in rows if r.get("excess_vs_sector") is not None]
        out["by_window_bucket"][bucket] = {
            "label": label, "lo": lo, "hi": hi,
            "samples": len(rows),
            "hits": hits,
            "hit_rate": round(hits / len(rows) * 100, 1),
            "median_excess_pct": round(sorted(excesses)[len(excesses) // 2], 2) if excesses else None,
            "mean_excess_pct": round(sum(excesses) / len(excesses), 2) if excesses else None,
            "strict_samples": len(strict_rows),
            "strict_hits": strict_hits,
            "strict_rate": round(strict_hits / len(strict_rows) * 100, 1) if strict_rows else None,
            "median_excess_vs_sector": round(sorted(excess_vs_sector)[len(excess_vs_sector) // 2], 2) if excess_vs_sector else None,
            "mean_excess_vs_sector": round(sum(excess_vs_sector) / len(excess_vs_sector), 2) if excess_vs_sector else None,
        }
    out["main_signal_summary"] = out["by_window_bucket"].get(MAIN_SIGNAL_BUCKET)

    # by_score / track / subdomain / event_type / late_stage:
    # 用 latest (最近 verify) 作为 ticker 当前状态
    def _bump(bucket_dict, key, latest):
        """统一聚合 helper — 同时累加 hit (vs 大盘) 和 hit_strict (vs 大盘+板块)."""
        d = bucket_dict.setdefault(key, {
            "samples": 0, "hits": 0, "excesses": [],
            "strict_samples": 0, "strict_hits": 0, "excess_vs_sector_list": [],
        })
        d["samples"] += 1
        if latest.get("hit"):
            d["hits"] += 1
        if latest.get("excess_pct") is not None:
            d["excesses"].append(latest["excess_pct"])
        # strict 仅在有 sector 数据时计入
        if latest.get("hit_strict") is not None:
            d["strict_samples"] += 1
            if latest.get("hit_strict"):
                d["strict_hits"] += 1
        if latest.get("excess_vs_sector") is not None:
            d["excess_vs_sector_list"].append(latest["excess_vs_sector"])

    for pair, agg in by_pair.items():
        latest = agg["latest"]
        _bump(out["by_score"], f"score={latest.get('event_score')}", latest)
        _bump(out["by_track"], latest.get("event_track", "?"), latest)
        _bump(out["by_subdomain"], latest.get("event_subdomain", "?"), latest)
        # W21 v3 §6 #2: event_type 拆解
        _bump(out["by_event_type"], latest.get("event_type") or "other", latest)
        # W21 v3 §6 #1: late_stage 拆解
        _bump(out["by_late_stage"], "late_stage" if latest.get("late_stage") else "normal", latest)

    for bucket in ["by_score", "by_track", "by_subdomain", "by_event_type", "by_late_stage"]:
        for k, d in out[bucket].items():
            d["hit_rate"] = round(d["hits"] / d["samples"] * 100, 1) if d["samples"] else 0
            ex = d.pop("excesses")
            d["median_excess_pct"] = round(sorted(ex)[len(ex) // 2], 2) if ex else None
            evs = d.pop("excess_vs_sector_list")
            d["median_excess_vs_sector"] = round(sorted(evs)[len(evs) // 2], 2) if evs else None
            d["strict_rate"] = round(d["strict_hits"] / d["strict_samples"] * 100, 1) if d["strict_samples"] else None

    # top winners / losers (latest excess)
    latest_rows = [agg["latest"] for agg in by_pair.values()
                   if agg["latest"].get("excess_pct") is not None]
    latest_rows.sort(key=lambda r: r["excess_pct"], reverse=True)
    out["top_winners"] = latest_rows[:5]
    out["top_losers"] = latest_rows[-5:][::-1]

    return out


# ─── markdown doc ──────────────────────────────────────────────────────

def _fmt_excess(v: Optional[float]) -> str:
    return f"{v:+.2f}%" if v is not None else "—"


def doc_markdown(weeks: int = 4) -> str:
    rep = report(weeks=weeks)
    md = []
    today = dt.datetime.now(CN_TZ).strftime("%Y-%m-%d")
    md.append(f"# 叙事雷达 · 推演验证 ({today})")
    md.append("")
    md.append(f"**回看窗口**: 过去 {weeks} 周 · 自 {rep['since']} 起")
    md.append(f"**覆盖 events**: {rep['events_covered']} 条 · **ticker pair**: {rep['ticker_pairs']} 个")
    md.append("")
    md.append("> **hit 判定 (vs 大盘)**: side=+ → excess_pct > 0; side=- → excess_pct < 0. "
              "excess_pct = ticker 涨跌% - benchmark 涨跌% (CSI300 / HSI). 剔除 beta.")
    md.append(">")
    md.append("> **strict 判定 (vs 大盘 + 板块)**: hit AND ticker 同时跑赢同行业等权均值. "
              "剔除 beta + 行业轮动 = narrative 真有 alpha. **这是周报最该看的数字** — "
              "板块涨时再多 hit 也可能只是搭便车. 仅 A 股有 sector 数据, HK/US 不计入 strict.")
    md.append(">")
    md.append("> baseline 锚定: 新闻**原始发布日 (pub_date)** 收盘价, 不是 radar 收集日 — "
              "确保 T+N 测的是真实市场反应窗口而不是 cron 抓取延迟.")
    md.append(">")
    md.append("> **主判定窗口 = D14-D28** (W21 v3 §4 横截面证据). T+5/T+10 列为 noise 区, 仅供参考, 不当决策依据.")
    md.append("")

    # ─── 头条: 主信号窗口聚合 ─────────────────────────────────────
    main = rep.get("main_signal_summary")
    md.append("## 🎯 主信号 (D14-D28 窗口)")
    md.append("")
    if main and main.get("samples"):
        md.append(f"- **n**: {main['samples']} pair (vs 大盘) · "
                  f"**hit_rate**: **{main['hit_rate']}%** · "
                  f"**median excess**: {_fmt_excess(main.get('median_excess_pct'))}")
        if main.get("strict_samples"):
            sr = main.get("strict_rate")
            sr_str = f"**{sr}%**" if sr is not None else "—"
            md.append(f"- **strict (剔除 beta + 行业)**: n={main['strict_samples']} · "
                      f"strict_rate: {sr_str} · "
                      f"median excess vs sector: {_fmt_excess(main.get('median_excess_vs_sector'))}")
        else:
            md.append("- **strict**: 暂无 A 股 sector 数据 (HK/US 不计 / 老数据未 backfill)")
        md.append("")
        md.append("> 优先看 strict_rate — 板块涨时 hit_rate 容易被 beta 抬高, strict 才是真 alpha.")
    else:
        md.append("> 当前回看窗口内无 D14-D28 数据 — 大部分 event 太新还没穿越主信号窗口.")
        md.append("> 等 cron 累积 2+ 周后会自动填充.")
    md.append("")

    # ─── 各 window bucket 表 ─────────────────────────────────────
    md.append("## 窗口分桶汇总 (alpha 累积曲线)")
    md.append("")
    md.append("| bucket | 范围 | n | hit_rate | excess vs 大盘 | strict_n | strict_rate | excess vs 板块 | 性质 |")
    md.append("|--------|------|---|----------|----------------|----------|-------------|----------------|------|")
    bucket_order = ["noise", "early", "main", "extended"]
    for b in bucket_order:
        d = rep["by_window_bucket"].get(b)
        if not d:
            continue
        flag = "**主判定**" if b == MAIN_SIGNAL_BUCKET else ("⚠️噪音" if b == "noise" else "")
        sn = d.get("strict_samples", 0)
        sr = d.get("strict_rate")
        sr_str = f"{sr}%" if sr is not None else "—"
        md.append(f"| {b} | D{d['lo']}-{d['hi']} | {d['samples']} | "
                  f"{d['hit_rate']}% | {_fmt_excess(d.get('median_excess_pct'))} | "
                  f"{sn} | {sr_str} | {_fmt_excess(d.get('median_excess_vs_sector'))} | {flag} |")
    md.append("")

    # by_milestone 表 (作为细节, 不是头条)
    md.append("## 各 milestone 命中率 (细节)")
    md.append("")
    md.append("| 窗口 | n | hit_rate | 中位 excess% | strict_rate | 中位 excess vs 板块 | 标签 |")
    md.append("|------|---|----------|--------------|-------------|---------------------|------|")
    for ms in MILESTONE_DAYS:
        key = f"T+{ms}"
        if key not in rep["by_milestone"]:
            md.append(f"| {key} | 0 | — | — | — | — | — |")
            continue
        d = rep["by_milestone"][key]
        med = f"{d['median_excess_pct']:+.2f}" if d['median_excess_pct'] is not None else "—"
        sr = d.get("strict_rate")
        sr_str = f"{sr}%" if sr is not None else "—"
        flag = "🎯主信号" if d.get("is_main_signal") else ("⚠️噪音" if d.get("is_noise") else "")
        md.append(f"| {key} | {d['samples']} | {d['hit_rate']}% | {med}% | "
                  f"{sr_str} | {_fmt_excess(d.get('median_excess_vs_sector'))} | {flag} |")
    md.append("")

    def _row(k, d):
        """统一 row 渲染 — 5 列: n / hit_rate / median excess / strict_rate / median excess vs sector."""
        med = f"{d['median_excess_pct']:+.2f}%" if d['median_excess_pct'] is not None else "—"
        sr = d.get("strict_rate")
        sr_str = f"{sr}%" if sr is not None else "—"
        evs = _fmt_excess(d.get("median_excess_vs_sector"))
        return f"| {k} | {d['samples']} | {d['hit_rate']}% | {med} | {sr_str} | {evs} |"

    _COLS = "| {label} | n | hit_rate | 中位 excess% | strict_rate | excess vs 板块 |"
    _SEP = "|------|---|----------|--------------|-------------|----------------|"

    # by_score
    md.append("## 按 event score 拆解 (latest)")
    md.append("")
    md.append(_COLS.format(label="score"))
    md.append(_SEP)
    for k in sorted(rep["by_score"].keys()):
        md.append(_row(k, rep["by_score"][k]))
    md.append("")

    # by_track
    md.append("## 按 track 拆解")
    md.append("")
    md.append(_COLS.format(label="track"))
    md.append(_SEP)
    for k in sorted(rep["by_track"].keys()):
        md.append(_row(k, rep["by_track"][k]))
    md.append("")

    # by_subdomain — 优先按 strict_rate 排 (有数据时), 否则按 hit_rate
    md.append("## 按 subdomain 拆解 (优先按 strict_rate 排序)")
    md.append("")
    md.append(_COLS.format(label="subdomain"))
    md.append(_SEP)
    sd_items = sorted(
        rep["by_subdomain"].items(),
        key=lambda kv: (
            -(kv[1].get("strict_rate") if kv[1].get("strict_rate") is not None else -1),
            -kv[1]["hit_rate"],
            -kv[1]["samples"],
        ),
    )
    for k, d in sd_items:
        md.append(_row(k, d))
    md.append("")

    # W21 v3 §6 #2: by_event_type
    if rep.get("by_event_type"):
        md.append("## 按 event_type 拆解 (lagging 类是否真的拖累?)")
        md.append("")
        md.append(_COLS.format(label="event_type"))
        md.append(_SEP)
        et_items = sorted(rep["by_event_type"].items(),
                          key=lambda kv: -kv[1]["hit_rate"])
        for k, d in et_items:
            md.append(_row(k, d))
        md.append("")

    # W21 v3 §6 #1: by_late_stage
    if rep.get("by_late_stage"):
        md.append("## 末期抱团 vs 正常 sub_domain (Tier0 联动)")
        md.append("")
        md.append(_COLS.format(label="状态"))
        md.append(_SEP)
        for k in ["normal", "late_stage"]:
            d = rep["by_late_stage"].get(k)
            if not d:
                continue
            md.append(_row(k, d))
        md.append("")

    # ─── W21 v3 §6 #4: sub_domain timeline ───────────────────────────
    # 按 sub_domain 分组 (同 sub_domain hit_rate 高的优先), 内部按 days 倒序
    # 让用户决策时先看赛道再看个股: "AI__pcb_substrate 100% 兑现 → 看里面的 ticker"
    # 而不是 "TOP5 涨幅榜 → 散点式看个股"
    perfs = _read_jsonl(_PERF_PATH)
    perfs = _enrich_perf_with_event_meta(perfs)
    since_d = dt.datetime.strptime(rep["since"], "%Y-%m-%d").date()

    def _filt(p):
        d = p.get("baseline_date") or p.get("event_trade_date")
        try:
            return dt.datetime.strptime(d, "%Y%m%d").date() >= since_d
        except (ValueError, TypeError):
            return False

    perfs = [p for p in perfs if _filt(p)]
    # 取每对 (event_ts, code) 的 latest record
    latest_by_pair: dict[tuple[str, str], dict] = {}
    for p in perfs:
        k = (p["event_ts"], p["code"])
        old = latest_by_pair.get(k)
        if old is None or p["days_since_event"] > old["days_since_event"]:
            latest_by_pair[k] = p

    # 按 sub_domain 分组
    sd_groups: dict[str, list[dict]] = defaultdict(list)
    for p in latest_by_pair.values():
        sd_groups[p.get("event_subdomain", "?")].append(p)

    # sub_domain 排序: 按 by_subdomain hit_rate 降序
    sd_order = sorted(sd_groups.keys(),
                      key=lambda sd: (-rep["by_subdomain"].get(sd, {}).get("hit_rate", 0),
                                      -len(sd_groups[sd])))

    md.append("## 按 sub_domain × 时间线 (赛道维度 → 个股维度)")
    md.append("")
    md.append("> 同 sub_domain hit_rate 高的赛道先列, 内部按 days_since_event 倒序 (最新事件在前). "
              "看完一个赛道是否在兑现, 再看里面具体哪些 ticker 在驱动.")
    md.append("")
    for sd in sd_order:
        rows = sd_groups[sd]
        rows.sort(key=lambda r: -r["days_since_event"])
        sd_meta = rep["by_subdomain"].get(sd, {})
        sd_hit = sd_meta.get("hit_rate", 0)
        sd_n = sd_meta.get("samples", 0)
        sd_med = sd_meta.get("median_excess_pct")
        late_flag = " ⚠️末期抱团" if any(r.get("late_stage") for r in rows) else ""
        md.append(f"### `{sd}` — n={sd_n} · hit_rate={sd_hit}% · "
                  f"median {_fmt_excess(sd_med)}{late_flag}")
        md.append("")
        md.append("| code | name | side | days | event_type | event 标题 | excess% | hit |")
        md.append("|------|------|------|------|------------|------------|---------|-----|")
        for r in rows:
            hit_emoji = "✅" if r.get("hit") else "❌"
            et = r.get("event_type", "—") or "—"
            md.append(f"| {r['code']} | {r['name']} | {r['side']} "
                      f"| T+{r['days_since_event']} | {et} "
                      f"| {_truncate(r['event_title'], 38)} "
                      f"| {_fmt_excess(r.get('excess_pct'))} | {hit_emoji} |")
        md.append("")

    # winners / losers
    if rep["top_winners"]:
        md.append("## TOP 5 命中 (latest excess%)")
        md.append("")
        md.append("| ticker | name | side | event_score | event 标题 | days | excess% |")
        md.append("|--------|------|------|-------------|-------------|------|---------|")
        for r in rep["top_winners"]:
            md.append(f"| {r['code']} | {r['name']} | {r['side']} | {r['event_score']} "
                      f"| {_truncate(r['event_title'], 40)} | T+{r['days_since_event']} "
                      f"| {r['excess_pct']:+.2f}% |")
        md.append("")

    if rep["top_losers"]:
        md.append("## TOP 5 失败 (latest excess%)")
        md.append("")
        md.append("| ticker | name | side | event_score | event 标题 | days | excess% |")
        md.append("|--------|------|------|-------------|-------------|------|---------|")
        for r in rep["top_losers"]:
            md.append(f"| {r['code']} | {r['name']} | {r['side']} | {r['event_score']} "
                      f"| {_truncate(r['event_title'], 40)} | T+{r['days_since_event']} "
                      f"| {r['excess_pct']:+.2f}% |")
        md.append("")

    md.append("---")
    md.append("")
    md.append("**解读 / 决策框架**:")
    md.append("- **看 §🎯 主信号 (D14-D28)** — 周报最该关注的数字. T+5/T+10 是 noise 区, 不当决策依据.")
    md.append("- **看 §按 sub_domain × 时间线** — 先确认哪个赛道在兑现 (hit_rate ≥60%), 再看里面具体 ticker. "
              "比『散点 TOP5 涨幅榜』对决策更有用.")
    md.append("- **末期抱团 vs 正常拆解** — 如果 normal hit_rate >> late_stage, 验证 late_stage_subdomains "
              "降权策略有效; 否则需要 review universe.yaml.late_stage_subdomains 列表.")
    md.append("- **event_type 拆解** — capex_lock / quant_increment 应该 hit_rate 显著高于 trailing_data / "
              "recap_news; 否则关键词分类器需要调.")
    md.append("- score=3 (重磅) 应该 hit_rate 显著高于 score=2 — 否则雷达打分校准有问题")
    md.append("- 整体 hit_rate < 50% (excess) → 雷达无 alpha, 跟大盘 / 板块 beta 同步, 需要重做筛选")
    md.append("")
    md.append(f"*narrative_track / cron: 每日 18:45 工作日 · 报告由 `narrative_track.py doc --weeks {weeks}` 生成*")
    return "\n".join(md)


def event_report(event_ts: str) -> str:
    """单 event 的所有 ticker × milestone 详情."""
    perfs = [p for p in _read_jsonl(_PERF_PATH) if p.get("event_ts") == event_ts]
    if not perfs:
        return f"event {event_ts} 无 perf 记录."

    e_meta = perfs[0]
    by_code: dict[str, list[dict]] = defaultdict(list)
    for p in perfs:
        by_code[p["code"]].append(p)
    for v in by_code.values():
        v.sort(key=lambda x: x["days_since_event"])

    md = []
    md.append(f"# Event {e_meta['event_trade_date']} · score={e_meta['event_score']}")
    md.append(f"**{e_meta['event_track']} / {e_meta['event_subdomain']}**: {e_meta['event_title']}")
    md.append("")
    md.append("| code | name | side | T+0 | latest | days | abs% | bench% | excess% | hit |")
    md.append("|------|------|------|-----|--------|------|------|--------|---------|-----|")
    for code, plist in by_code.items():
        latest = plist[-1]
        hit_str = "✅" if latest.get("hit") else "❌"
        bench = f"{latest['benchmark_pct']:+.2f}" if latest.get("benchmark_pct") is not None else "—"
        excess = f"{latest['excess_pct']:+.2f}" if latest.get("excess_pct") is not None else "—"
        md.append(f"| {code} | {latest['name']} | {latest['side']} "
                  f"| {latest['baseline_price']} | {latest['current_price']} "
                  f"| T+{latest['days_since_event']} "
                  f"| {latest['absolute_pct']:+.2f}% | {bench}% | {excess}% | {hit_str} |")
    return "\n".join(md)


# ─── CLI ────────────────────────────────────────────────────────────────

def _cmd_verify(args):
    if args.event_ts:
        events = [e for e in _read_jsonl(_EVENTS_PATH) if e.get("ts") == args.event_ts]
        if not events:
            print(f"event_ts {args.event_ts} not found")
            sys.exit(1)
        seen = _existing_perf_keys()
        cached: dict = {}
        for e in events:
            for t in e.get("tickers", []):
                perf = verify_event_ticker(e, t, verify_date=args.date,
                                            cached_baseline=cached, seen_keys=seen)
                print(json.dumps(perf, ensure_ascii=False) if perf else
                      f"skip {e.get('subdomain')} / {t.get('code')}")
        return
    stats = verify_all(verify_date=args.date)
    print(f"verify_all: events_total={stats['events_total']} "
          f"open={stats['events_open']} expired={stats['events_expired']} "
          f"ticker_pairs={stats['ticker_pairs_total']} verified={stats['verified']} "
          f"skipped={stats['skipped_dup']} fetch_failed={stats['fetch_failed']}")


def _cmd_report(args):
    rep = report(weeks=args.weeks)
    print(json.dumps(rep, ensure_ascii=False, indent=2))


def _cmd_doc(args):
    print(doc_markdown(weeks=args.weeks))


def _cmd_event_report(args):
    print(event_report(args.event_ts))


def main():
    ap = argparse.ArgumentParser(description="Narrative event T+N tracking.")
    sp = ap.add_subparsers(dest="cmd", required=True)

    v = sp.add_parser("verify")
    v.add_argument("--event-ts", help="单 event 验证 (默认全部)")
    v.add_argument("--date", help="verify against YYYYMMDD close (默认最新)")
    v.add_argument("--all", action="store_true", help="(default behavior)")
    v.set_defaults(func=_cmd_verify)

    r = sp.add_parser("report")
    r.add_argument("--weeks", type=int, default=4)
    r.set_defaults(func=_cmd_report)

    d = sp.add_parser("doc")
    d.add_argument("--weeks", type=int, default=4)
    d.set_defaults(func=_cmd_doc)

    er = sp.add_parser("event_report")
    er.add_argument("--event-ts", required=True)
    er.set_defaults(func=_cmd_event_report)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
