#!/usr/bin/env python3
"""
thesis_enrich_daily.py — Thesis 每日数据富集（脚本化确定性部分）。

职责：
  1. 读取所有 ACTIVE thesis YAML
  2. 为每只 thesis 获取当日行情 + 120 日历史，计算技术指标
  3. 从 evening_recap JSON 取所属板块评分/资金流信息
  4. 生成 update_log 条目（technical_status + stop_loss_check），pillar_impact 默认 NEUTRAL
  5. append 写回 YAML，写完 yaml.safe_load 校验
  6. 输出 JSON 摘要：有异动的 / 静默的 / 失败的

用法：
  python3 thesis_enrich_daily.py --thesis-dir ~/work/investment/thesis \\
      --evening-recap-json /tmp/evening_recap_YYYY-MM-DD.json \\
      [--date YYYY-MM-DD] [--dry-run] [--max-stocks N]

输出（stdout，末行 RESULT_JSON=<path>）：
  {
    "date": "2026-08-25",
    "total": 43,
    "active": 41,
    "updated": 41,
    "skipped_existing": 0,  // 同日重跑时跳过已有 entry，避免重复写入
    "failed": [{"ticker": "...", "error": "..."}],
    "alerts": [          // 需要 agent 关注的异动
      {
        "ticker": "300750.SZ",
        "name": "宁德时代",
        "alert_types": ["SELL_EXTREME", "STOP_LOSS_NEAR", "PILLAR_NEWS_CANDIDATE"],
        "prev_signal": "CLEAN",
        "curr_signal": "SELL_EXTREME",
        "price": 376.73,
        "pct_today": -2.88,
        "position": 50,
        "sector": "锂电产业链",
        "sector_tier1_pass": true,
        "summary": "技术面进入 SELL_EXTREME..."
      }
    ],
    "silent": true   // 如果全部无异动则为 true
  }
"""

import argparse
import csv
import datetime
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# ---- 配置 ----

HERE = Path(__file__).resolve().parent
TUSHARE_PY = HERE / "tushare.py"
SIGNALS_PY = HERE / "signals.py"

# 引入 signals.py
sys.path.insert(0, str(HERE))
import signals  # noqa: E402


# ---- 工具函数 ----

def run_tushare(api_name, **params):
    """调 tushare.py，返回 list[dict]。失败抛异常。"""
    cmd = ["python3", str(TUSHARE_PY), api_name]
    for k, v in params.items():
        cmd.append(f"{k}={v}")
    cmd.append("--csv")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"tushare {api_name} failed: {result.stderr.strip()[:300]}")
    lines = result.stdout.strip().splitlines()
    if not lines:
        return []
    reader = csv.DictReader(lines)
    return list(reader)


def load_yaml_safe(path):
    """用 PyYAML safe_load。没装就用简陋 parser（只读关键字段）。"""
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f)
    except ImportError:
        return _yaml_basic_read(path)


def has_update_for_date(thesis, date_str):
    """Return True when update_log already contains date_str.

    A cron retry must be safe: completed tickers are skipped while missing/failed
    tickers can still be filled by the retry.
    """
    for entry in (thesis.get("update_log", []) or []):
        if isinstance(entry, dict) and str(entry.get("date", "")) == date_str:
            return True
    return False


def _validate_yaml_text(text):
    """Validate generated YAML before replacing the original file."""
    try:
        import yaml
        yaml.safe_load(text)
        return True, None
    except ImportError:
        # Production has PyYAML. Keep a minimal fallback for standalone use.
        if text.count("  pillar_impact:") != text.count("  technical_status:"):
            return False, "pillar_impact / technical_status 数量不匹配"
        return True, None
    except Exception as exc:
        return False, str(exc)


def _yaml_basic_read(path):
    """极简 YAML 读取：只提取 top-level 标量 + update_log 长度。
    用于没装 PyYAML 的 fallback（不推荐）。"""
    data = {}
    lines = open(path).readlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#") or not line.strip():
            i += 1
            continue
        m = re.match(r"^(\w+):\s*(.*)", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            data[key] = val
        i += 1
    # count update_log entries
    in_update = False
    count = 0
    for line in lines:
        if line.strip().startswith("update_log:"):
            in_update = True
            continue
        if in_update:
            if re.match(r"^  - date:", line):
                count += 1
            elif line.strip() and not line.startswith(" ") and not line.startswith("#"):
                break
    data["_update_log_count"] = count
    return data


def get_ticker_market(ticker):
    """从 ts_code 判断市场：CN / HK / US"""
    if ticker.endswith((".SH", ".SZ", ".BJ")):
        return "CN"
    if ticker.endswith(".HK"):
        return "HK"
    return "US"


def fetch_daily_bars(ticker, days=150):
    """获取最近 N 个交易日的日线，返回按日期正序排列的 list[dict]。
    A 股走 tushare，港股走腾讯财经 kline（tushare hk_daily 有配额）。
    """
    market = get_ticker_market(ticker)

    if market == "CN":
        end_date = datetime.date.today().strftime("%Y%m%d")
        start_date = (datetime.date.today() - datetime.timedelta(days=days + 50)).strftime("%Y%m%d")
        rows = run_tushare("daily", ts_code=ticker, start_date=start_date, end_date=end_date,
                           fields="ts_code,trade_date,open,high,low,close,vol,amount")
        # tushare 返回倒序（最新在前），转正序
        rows.reverse()
        # 统一字段名（tushare: trade_date, vol, amount → date, vol, amount）
        result = []
        for r in rows:
            result.append({
                "date": r.get("trade_date", ""),
                "open": _to_float(r.get("open")),
                "high": _to_float(r.get("high")),
                "low": _to_float(r.get("low")),
                "close": _to_float(r.get("close")),
                "vol": _to_float(r.get("vol")),
                "amount": _to_float(r.get("amount")),
            })
        return result

    elif market == "HK":
        # 走腾讯财经 kline（免费，无配额限制）
        from quote_sources import _tencent_daily_kline
        raw = _tencent_daily_kline(ticker, days=days)
        # 字段：date, open, close, high, low, volume → 对齐统一格式
        result = []
        for r in raw:
            result.append({
                "date": r.get("date", "").replace("-", ""),
                "open": float(r.get("open", 0)),
                "high": float(r.get("high", 0)),
                "low": float(r.get("low", 0)),
                "close": float(r.get("close", 0)),
                "vol": float(r.get("volume", 0)),
                "amount": 0.0,
            })
        return result

    else:
        # 美股等其他市场：暂用 quote_sources yahoo fallback
        from quote_sources import _yahoo_daily_bars
        raw = _yahoo_daily_bars(ticker, days=days)
        result = []
        for r in raw:
            result.append({
                "date": r.get("date", "").replace("-", ""),
                "open": float(r.get("open", 0)),
                "high": float(r.get("high", 0)),
                "low": float(r.get("low", 0)),
                "close": float(r.get("close", 0)),
                "vol": float(r.get("volume", 0)),
                "amount": 0.0,
            })
        return result


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def calc_technical_metrics(bars):
    """从 K 线计算技术指标。返回 dict。"""
    if len(bars) < 20:
        return None

    closes = [b["close"] for b in bars if b.get("close") is not None]
    vols = [b["vol"] for b in bars if b.get("vol") is not None]

    latest = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else latest

    # 今日涨跌幅
    r1d = (latest - prev) / prev * 100 if prev else 0.0

    # 1 周（5 个交易日）
    r1w = (latest - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else None

    # 1 月（20 个交易日）
    r1m = (latest - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else None

    # 3 月（60 个交易日）
    r3m = (latest - closes[-61]) / closes[-61] * 100 if len(closes) >= 61 else None

    # 位置 = 120 日百分位
    window_120 = closes[-120:] if len(closes) >= 120 else closes
    high_120 = max(window_120)
    low_120 = min(window_120)
    position = ((latest - low_120) / (high_120 - low_120) * 100) if high_120 != low_120 else 50.0

    # 量比 = 今日成交量 / 过去 5 日平均成交量
    if len(vols) >= 6:
        avg_vol_5d = sum(vols[-6:-1]) / 5
        vol_ratio = vols[-1] / avg_vol_5d if avg_vol_5d > 0 else None
    else:
        vol_ratio = None

    return {
        "price": latest,
        "r1d": round(r1d, 2),
        "r1w": round(r1w, 2) if r1w is not None else None,
        "r1m": round(r1m, 2) if r1m is not None else None,
        "r3m": round(r3m, 2) if r3m is not None else None,
        "position": round(position, 1),
        "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
        "high_120": round(high_120, 2),
        "low_120": round(low_120, 2),
    }


def detect_signal(metrics):
    """用 signals.detect 检测信号，返回 list[str]（信号名，如 SELL_EXTREME）。"""
    if metrics is None:
        return []
    m = {
        "r1w": metrics["r1w"],
        "r1m": metrics["r1m"],
        "r3m": metrics["r3m"],
        "vol_ratio": metrics["vol_ratio"],
        "pos": metrics["position"],
        "pct_chg_today": metrics["r1d"],
    }
    raw = signals.detect(m)
    # signals.detect 返回 list[(icon, signal_type, description)]
    return [s[1] for s in raw if isinstance(s, (list, tuple)) and len(s) >= 2]


def get_last_update_log_entry(path, ticker):
    """读 YAML 的最后一条 update_log，返回其 technical_status 等信息。
    用正则解析，不依赖 PyYAML。"""
    text = open(path).read()
    # 找到 update_log section
    m = re.search(r"^update_log:\s*\n(.*?)(?=\n[a-z_]+:|\Z)", text, re.MULTILINE | re.DOTALL)
    if not m:
        return None
    section = m.group(1)
    # 拆条目
    entries = re.split(r"\n  - date: ", section)
    if not entries:
        return None
    # 最后一条（第一条是 split 前的空串或内容）
    last_entry_str = entries[-1].strip()
    if not last_entry_str:
        return None
    # 提取 signal
    sig_m = re.search(r"signal:\s*(\S+)", last_entry_str)
    signal = sig_m.group(1) if sig_m else None
    # 提取 date
    date_m = re.match(r"['\"]?(\d{4}-\d{2}-\d{2})['\"]?", last_entry_str)
    date = date_m.group(1) if date_m else None
    return {"date": date, "signal": signal, "_raw": last_entry_str}


def format_price(p, market="CN"):
    """格式化价格。"""
    if market == "HK":
        return f"HK${p:.2f}"
    elif market == "US":
        return f"${p:.2f}"
    return f"¥{p:.2f}"


def check_stop_loss(thesis, metrics):
    """检查 stop_loss 三类触发。返回 (status_str, triggered_list)。"""
    triggered = []
    checks = []

    sl = thesis.get("stop_loss", {}) or {}

    # price_trigger
    pt = sl.get("price_trigger", {}) or {}
    level_str = str(pt.get("level", ""))
    # 优先用 thesis 里明确封存的基准价（entry_price / reference_price / cost_basis）
    ref_price = thesis.get("entry_price") or thesis.get("reference_price") or thesis.get("cost_basis")
    
    if metrics and ref_price and isinstance(ref_price, (int, float)):
        # 有明确基准价，精确计算
        drawdown = (metrics["price"] - ref_price) / ref_price * 100
        if "-15%" in level_str:
            triggered_flag = drawdown <= -15
        elif "-20%" in level_str:
            triggered_flag = drawdown <= -20
        elif "-10%" in level_str:
            triggered_flag = drawdown <= -10
        else:
            triggered_flag = False
        if triggered_flag:
            triggered.append("price_trigger")
        checks.append(
            f"price_trigger: 距基准价 {ref_price:.2f} {drawdown:+.1f}%（规则 {level_str}）"
            + (" → 触发" if triggered_flag else " → 未触发")
        )
    elif metrics and level_str and "-15%" in level_str:
        # 无明确基准价，给信息性描述（距 120 日高点 / 距近高），但不自动判定触发
        drawdown_from_high = (metrics["price"] - metrics["high_120"]) / metrics["high_120"] * 100
        checks.append(
            f"price_trigger: 无法精确量化（规则 '{level_str}' 缺少已封存基准价）；"
            f"参考：距 120 日高点 {drawdown_from_high:+.1f}%（仅作参考，不自动触发）"
        )
    elif metrics:
        checks.append(f"price_trigger: 无法精确量化（规则 '{level_str}' 缺少已封存基准价）")
    else:
        checks.append("price_trigger: 无行情数据，无法检查")

    # thesis_trigger（只能做框架性检查，具体 pillar 判断留给 agent）
    tt = sl.get("thesis_trigger", []) or []
    if tt:
        checks.append("thesis_trigger: 框架性检查通过（具体 pillar 证伪由新闻/财报验证，见 pillar_impact）")

    # time_stop
    ts = sl.get("time_stop", {}) or {}
    established = thesis.get("established_date")
    window_str = str(ts.get("window", ""))
    if established and window_str:
        try:
            est_date = datetime.date.fromisoformat(str(established))
            # 提取月份数
            m_mo = re.search(r"(\d+)\s*个月", window_str)
            if m_mo:
                months = int(m_mo.group(1))
                # 粗略估算到期日
                expiry = est_date + datetime.timedelta(days=months * 30)
                days_left = (expiry - datetime.date.today()).days
                if days_left <= 0:
                    triggered.append("time_stop")
                checks.append(
                    f"time_stop: 建立于 {est_date.isoformat()}，窗口 ~{months} 个月，"
                    f"到期约 {expiry.isoformat()}（剩余 {days_left} 天）"
                    + (" → 到期触发" if days_left <= 0 else "")
                )
        except (ValueError, TypeError):
            checks.append("time_stop: 日期解析失败")
    else:
        checks.append("time_stop: 无 established_date 或无 window 设定")

    return "; ".join(checks), triggered


def find_sector_for_ticker(ticker, name, evening_recap):
    """从 evening_recap 的 picks 里找这只股票属于哪个板块。返回 (concept, score_obj)。

    evening_recap.picks 结构: {sector_name: {evaluations: [{stock: {code, name, ...}}, ...], sector_score: {...}, ...}}
    """
    if not evening_recap:
        return None, None
    picks = evening_recap.get("picks", {})
    scores = evening_recap.get("scores", [])
    score_map = {s["concept"]: s for s in scores}

    for concept, sector_data in picks.items():
        if not isinstance(sector_data, dict):
            continue
        evaluations = sector_data.get("evaluations", [])
        if not isinstance(evaluations, list):
            continue
        for ev in evaluations:
            stock = ev.get("stock", {}) if isinstance(ev, dict) else {}
            if not isinstance(stock, dict):
                continue
            if stock.get("code") == ticker or stock.get("name") == name:
                # 优先用 picks 里的 sector_score，fallback 到 scores
                sector_score = sector_data.get("sector_score") or score_map.get(concept)
                return concept, sector_score
    return None, None


def build_update_entry(date_str, thesis, metrics, sig_list, sector_name, sector_score,
                       stop_loss_str, source_str):
    """构建 update_log 条目（dict 形式）。"""
    name = thesis.get("name", "")
    market = thesis.get("market", "CN")
    p = metrics["price"] if metrics else "N/A"
    p_fmt = format_price(p, market) if metrics else "N/A"

    # data_point 文本
    parts = []
    if metrics:
        pct_str = f"{metrics['r1d']:+.2f}%"
        w_str = f"{metrics['r1w']:+.2f}%" if metrics["r1w"] is not None else "n/a"
        m_str = f"{metrics['r1m']:+.2f}%" if metrics["r1m"] is not None else "n/a"
        vol_str = f"{metrics['vol_ratio']:.1f}x" if metrics["vol_ratio"] is not None else "n/a"
        pos_str = f"{metrics['position']:.0f}%"
        parts.append(f"收 {p_fmt} ({pct_str}), 1W {w_str}, 1M {m_str}, 量比 {vol_str}, 位置 {pos_str}.")
    if sector_name:
        tier = sector_score.get("tier", "?") if sector_score else "?"
        parts.append(f"所属板块 {sector_name}（Tier {tier}）。")

    parts.append("pillar_impact 待新闻/财报验证后更新（默认 NEUTRAL，不把价格波动直接等同基本面证伪）。")

    data_point = " ".join(parts)

    # pillar_impact dict
    pillars = thesis.get("pillars", []) or []
    pillar_impact = {}
    for p_item in pillars:
        pname = p_item.get("name", "")
        if pname:
            pillar_impact[pname] = "NEUTRAL"

    # technical_status
    signal_label = sig_list[0] if sig_list else "CLEAN"
    technical_status = {
        "signal": signal_label,
        "position": metrics["position"] if metrics else None,
        "w1_pct": metrics["r1w"] if metrics else None,
        "m1_pct": metrics["r1m"] if metrics else None,
        "volume_ratio": metrics["vol_ratio"] if metrics else None,
    }
    if sig_list:
        technical_status["interpretation"] = f"信号为 {', '.join(sig_list)}。"
    else:
        pos = metrics["position"] if metrics else 0
        w = metrics["r1w"] if metrics else 0
        m = metrics["r1m"] if metrics else 0
        vr = metrics["vol_ratio"] if metrics else 0
        technical_status["interpretation"] = (
            f"CLEAN；位置 {pos:.0f}% / 1W {w:+.2f}% / 1M {m:+.2f}% / 量比 {vr:.1f}x。"
            f"未触发高优先级技术信号。"
        )

    # action_hint
    if any("SELL" in s for s in sig_list):
        action_hint = f"技术面触发 {signal_label}，建议关注基本面是否同步恶化；具体操作结合 pillar 判断。"
    elif any("BUY" in s for s in sig_list):
        action_hint = f"技术面出现 {signal_label} 信号，可结合 pillar 强度评估是否调整关注级别。"
    else:
        action_hint = "无新增动作；进入周度 thesis review 汇总。"

    entry = {
        "date": date_str,
        "data_point": data_point,
        "source": source_str,
        "pillar_impact": pillar_impact,
        "technical_status": technical_status,
        "stop_loss_check": stop_loss_str,
        "action_hint": action_hint,
    }
    return entry


def append_update_to_yaml(path, entry):
    """向 YAML 文件 append 一条 update_log 条目。
    策略：找到 update_log 段末尾，以 YAML 格式插入新条目。
    不整体 reformat，保留原文件格式。"""
    with open(path) as f:
        text = f.read()

    # 构造 YAML 字符串
    yaml_str = entry_to_yaml(entry)

    # 找 update_log section 的最后一条条目。
    # 空列表常被 safe_dump 写成 `update_log: []`，需要先展开为 block list。
    empty_inline = re.search(r"^update_log:\s*\[\s*\]\s*$", text, re.MULTILINE)
    if empty_inline:
        new_text = text[:empty_inline.start()] + "update_log:\n" + yaml_str + text[empty_inline.end():]
    else:
        # 用模式：找到 "update_log:"，然后找到下一个顶层级 key 或文件尾
        m = re.search(r"(^update_log:.*?$)(.*?)(?=\n[a-z_]+:|\n[a-z_]+_.*:|\Z)", text, re.MULTILINE | re.DOTALL)
        if not m:
            # 找不到 update_log，需要新建
            new_text = text.rstrip() + "\n\nupdate_log:\n" + yaml_str + "\n"
        else:
            before = text[:m.start(2)]
            section_content = m.group(2)
            after = text[m.end(2):]
            if not section_content.endswith("\n"):
                section_content += "\n"
            new_section = section_content + yaml_str + "\n"
            new_text = before + new_section + after

    ok, err = _validate_yaml_text(new_text)
    if not ok:
        raise ValueError(f"生成的 YAML 校验失败，原文件未改动: {err}")

    path = Path(path)
    original_mode = path.stat().st_mode
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as tmp:
            tmp.write(new_text)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name
        os.chmod(tmp_name, original_mode)
        os.replace(tmp_name, path)
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)


def entry_to_yaml(entry, indent="  "):
    """把 update_log entry dict 转成 YAML 字符串（列表项）。"""
    lines = []
    lines.append(f"{indent}- date: '{entry['date']}'")
    lines.append(f"{indent}  data_point: >-")
    # data_point 长文本，用 folded scalar
    dp = entry["data_point"]
    # 按 ~80 字折行
    wrapped = wrap_text(dp, 78)
    for w_line in wrapped:
        lines.append(f"{indent}    {w_line}")
    lines.append(f"{indent}  source: {entry['source']}")

    # pillar_impact
    pi = entry.get("pillar_impact", {})
    if pi:
        lines.append(f"{indent}  pillar_impact:")
        for k, v in pi.items():
            lines.append(f"{indent}    {k}: {v}")

    # technical_status
    ts = entry.get("technical_status", {})
    if ts:
        lines.append(f"{indent}  technical_status:")
        for k, v in ts.items():
            if v is None:
                lines.append(f"{indent}    {k}: null")
            elif isinstance(v, (int, float)):
                lines.append(f"{indent}    {k}: {v}")
            else:
                # interpretation 可能很长
                if k == "interpretation" and len(str(v)) > 60:
                    lines.append(f"{indent}    {k}: >-")
                    for w_line in wrap_text(str(v), 72):
                        lines.append(f"{indent}      {w_line}")
                else:
                    lines.append(f"{indent}    {k}: {v}")

    # stop_loss_check
    if "stop_loss_check" in entry:
        slc = entry["stop_loss_check"]
        if slc:
            lines.append(f"{indent}  stop_loss_check: >-")
            for w_line in wrap_text(slc, 72):
                lines.append(f"{indent}    {w_line}")

    # action_hint
    ah = entry.get("action_hint", "")
    if ah:
        if len(ah) > 60:
            lines.append(f"{indent}  action_hint: >-")
            for w_line in wrap_text(ah, 72):
                lines.append(f"{indent}    {w_line}")
        else:
            lines.append(f"{indent}  action_hint: {ah}")

    return "\n".join(lines)


def wrap_text(text, width):
    """简单的中文友好折行（按字符数近似）。"""
    if not text:
        return [""]
    result = []
    current = ""
    for ch in text:
        current += ch
        if len(current) >= width and ch in (" ", "，", "。", "；", "、", "）"):
            result.append(current.strip())
            current = ""
    if current.strip():
        result.append(current.strip())
    return result or [""]


def validate_yaml(path):
    """校验 YAML 能否被 safe_load。"""
    try:
        import yaml
        with open(path) as f:
            yaml.safe_load(f)
        return True, None
    except ImportError:
        # 没装 PyYAML，做基本格式检查
        try:
            with open(path) as f:
                text = f.read()
            # 检查基本平衡
            if text.count("  pillar_impact:") != text.count("  technical_status:"):
                return False, "pillar_impact / technical_status 数量不匹配"
            return True, None
        except Exception as e:
            return False, str(e)
    except Exception as e:
        return False, str(e)


def is_significant_change(last_entry, sig_list, stop_triggered):
    """判断是否与上次有显著变化（需要飞书推送告警）。
    只对高优先级风险信号 + stop_loss 触发告警；
    普通买卖信号（BUY_EARLY 等）只记入 update_log，不单独推送。
    """
    HIGH_PRIORITY_SELL = {"SELL_EXTREME", "SELL_CONFIRMED", "SELL_TOP", "SELL_BREAKDOWN"}
    EXTREME_DAILY = {"TODAY_DROP"}  # 跌停/暴跌需要注意

    # stop_loss 触发 → 始终告警
    if stop_triggered:
        return True

    last_signal = None
    if last_entry:
        last_signal = last_entry.get("signal")

    curr_high_priority = set(sig_list) & (HIGH_PRIORITY_SELL | EXTREME_DAILY)
    last_high_priority = {last_signal} & (HIGH_PRIORITY_SELL | EXTREME_DAILY) if last_signal else set()

    # 新增了高优先级风险信号 → 告警
    if curr_high_priority - last_high_priority:
        return True

    return False


def classify_alerts(sig_list, stop_triggered, sector_score):
    """分类告警类型。"""
    alerts = []
    if "SELL_EXTREME" in sig_list:
        alerts.append("SELL_EXTREME")
    if "SELL_CONFIRMED" in sig_list:
        alerts.append("SELL_CONFIRMED")
    if "SELL_TOP" in sig_list:
        alerts.append("SELL_TOP")
    if "SELL_BREAKDOWN" in sig_list:
        alerts.append("SELL_BREAKDOWN")
    if "SELL_EXHAUSTION" in sig_list:
        alerts.append("SELL_EXHAUSTION")
    if "BUY_BREAKOUT" in sig_list:
        alerts.append("BUY_BREAKOUT")
    if "BUY_PULLBACK" in sig_list:
        alerts.append("BUY_PULLBACK")
    if "TODAY_DROP" in sig_list:
        alerts.append("TODAY_DROP")
    if "TODAY_SURGE" in sig_list:
        alerts.append("TODAY_SURGE")
    if stop_triggered:
        alerts.append("STOP_LOSS_TRIGGERED")
    # 板块 tier 变化（需要对比上次，这里简单标记一下）
    if sector_score and not sector_score.get("tier1_pass", True):
        alerts.append("SECTOR_TIER2")
    return alerts


def main():
    parser = argparse.ArgumentParser(description="Thesis daily enrichment (scripted)")
    parser.add_argument("--thesis-dir", required=True, help="thesis YAML 目录")
    parser.add_argument("--evening-recap-json", help="evening_recap_data.sh 输出的 JSON")
    parser.add_argument("--date", help="交易日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--dry-run", action="store_true", help="不写回文件")
    parser.add_argument("--max-stocks", type=int, help="最多处理 N 只（调试用）")
    args = parser.parse_args()

    date_str = args.date or datetime.date.today().isoformat()
    thesis_dir = Path(args.thesis_dir).resolve()

    # 加载 evening_recap
    evening_recap = None
    if args.evening_recap_json and os.path.exists(args.evening_recap_json):
        try:
            evening_recap = json.load(open(args.evening_recap_json))
        except Exception as e:
            print(f"[warn] evening_recap JSON 加载失败: {e}", file=sys.stderr)

    # 收集所有 ACTIVE thesis
    thesis_files = []
    for f in sorted(thesis_dir.glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        try:
            data = load_yaml_safe(f)
            status = data.get("status", "ACTIVE")
            if status == "ACTIVE":
                thesis_files.append((f, data))
        except Exception as e:
            print(f"[warn] 读取 {f.name} 失败: {e}", file=sys.stderr)

    if args.max_stocks:
        thesis_files = thesis_files[: args.max_stocks]

    total = len(list(thesis_dir.glob("*.yaml")))
    active = len(thesis_files)

    result = {
        "date": date_str,
        "total": total,
        "active": active,
        "updated": 0,
        "skipped_existing": 0,
        "failed": [],
        "alerts": [],
        "sector_map": {},  # ticker -> sector_name
        "silent": True,
    }

    source_str = "thesis_enrich_daily.py (tushare daily + signals.py + evening_recap sector data)"

    for path, thesis in thesis_files:
        ticker = thesis.get("ticker", path.stem)
        name = thesis.get("name", "")
        market = thesis.get("market", "CN")

        # Idempotency: retries only fill tickers that did not complete earlier.
        if has_update_for_date(thesis, date_str):
            result["skipped_existing"] += 1
            continue

        try:
            # 获取行情 + 计算指标
            bars = fetch_daily_bars(ticker)
            metrics = calc_technical_metrics(bars)
            if metrics is None:
                raise RuntimeError(f"日线不足 20 条（实际 {len(bars)} 条），不写入空数据 entry")
            sig_list = detect_signal(metrics)

            # 板块信息
            sector_name, sector_score = find_sector_for_ticker(ticker, name, evening_recap)
            if sector_name:
                result["sector_map"][ticker] = sector_name

            # stop_loss 检查
            stop_loss_str, stop_triggered = check_stop_loss(thesis, metrics)

            # 上次 entry
            last_entry = get_last_update_log_entry(path, ticker)

            # 构建 entry
            entry = build_update_entry(
                date_str, thesis, metrics, sig_list, sector_name, sector_score,
                stop_loss_str, source_str
            )

            # 写回
            if not args.dry_run:
                append_update_to_yaml(path, entry)
                ok, err = validate_yaml(path)
                if not ok:
                    result["failed"].append({"ticker": ticker, "error": f"YAML 校验失败: {err}"})
                    continue

            result["updated"] += 1

            # 判断是否异动
            significant = is_significant_change(last_entry, sig_list, stop_triggered)

            if significant:
                alert_types = classify_alerts(sig_list, stop_triggered, sector_score)
                alert = {
                    "ticker": ticker,
                    "name": name,
                    "alert_types": alert_types,
                    "prev_signal": last_entry.get("signal") if last_entry else None,
                    "curr_signal": sig_list[0] if sig_list else "CLEAN",
                    "price": metrics["price"] if metrics else None,
                    "pct_today": metrics["r1d"] if metrics else None,
                    "position": metrics["position"] if metrics else None,
                    "sector": sector_name,
                    "tier1_pass": sector_score.get("tier1_pass") if sector_score else None,
                }
                if alert_types:
                    result["alerts"].append(alert)
                    result["silent"] = False

        except Exception as e:
            result["failed"].append({"ticker": ticker, "error": str(e)[:200]})
            result["silent"] = False

    # 写结果 JSON 到临时文件
    result_file = f"/tmp/thesis_enrich_{date_str}.json"
    with open(result_file, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 打印摘要
    print(f"Date: {date_str}")
    print(f"Total thesis files: {total}")
    print(f"ACTIVE: {active}")
    print(f"Updated: {result['updated']}")
    print(f"Skipped existing date: {result['skipped_existing']}")
    print(f"Failed: {len(result['failed'])}")
    if result["failed"]:
        for f_item in result["failed"]:
            print(f"  FAIL {f_item['ticker']}: {f_item['error']}")
    print(f"Alerts: {len(result['alerts'])}")
    for a in result["alerts"]:
        print(f"  ⚠ {a['ticker']} {a['name']}: {', '.join(a['alert_types'])} (signal: {a['prev_signal']} -> {a['curr_signal']})")
    print(f"Silent: {result['silent']}")
    print(f"RESULT_JSON={result_file}")


if __name__ == "__main__":
    main()
