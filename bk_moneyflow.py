"""bk_moneyflow.py — 板块大单 / 散户 资金流信号 (sector_score v3.1 fund_flow 子模块).

替代失效的 ETF share-flow 信号 (回测 r=-0.07 反向). 数据源:
  tushare moneyflow_ind_dc — 东财行业/概念板块每日大单+超大单+散户净流入额.

回测证据 (backtest_elastic_net.py, n=2603, 7 AI BK, 2024-01 ~ 2026-03):
  ━━━ 单变量 Pearson r ↔ T+20 rel ━━━
    main_minus_sm_20d      +0.22    ← 主力相对散户 (推荐主信号)
    sm_20d                 -0.22    ← 散户反指 (镜像)
    rate_20d               +0.10    ← 净流入率% (规一化)

回测证据 (backtest_sector_v3.py --compare, n=3955 panel, 11 mapped concepts):
  strategy        IC_5d   IC_20d  Q5-Q1_5d  极端%   中段%  IC_std(月度)
  v3 (老)         +0.111  +0.220   +1.75    27.4%  26.0%   0.388
  v3.1 (软+反转)   +0.134  +0.236   +1.59     0.0%  23.2%   0.297 ⭐

v3.1 = v3 + 两步软化:
  1. clip ±1σ (而非 ±2σ): 极值压回 ±1σ, 把"非黑即白"消除
  2. |z| > 1.5σ → 反转拉回中位 30%: 极端高分衰减 (Q5<Q4 实证) +
     极端低分反弹 (Q1>Q3 实证) 都被纠正

打分逻辑 (v3.1):
  1. 拉 80d moneyflow (60d 历史 z, 20d 当前窗口)
  2. 计算 main_minus_sm_20d_z + rate_20d_z (vs 60d 历史)
  3. clip 到 [-1, +1], 加权: cm*10 + cr*3 = ±13 swing, 加 base=20
  4. 极端 |z_main|>1.5 → score = 0.7*score + 0.3*20 (反转拉回)
  5. 多 BK 时, z 取均值

  ⚠ 避免 step_20 阶梯 (复刻验证 r=+0.008 信号被桶化) — 必须连续映射.
  ⚠ 不再用 sm 反指做硬阈值 — main_minus_sm 已隐含 (镜像), 重复计算反致偏.

输出:
  fund_flow_score_v3(concept) → (score: float[0-40], note: str, diagnostics: dict)
"""
from __future__ import annotations

import csv
import json
import os
import statistics
import subprocess
import sys
import contextlib
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"
_BK_MAP_YAML = _HERE / "concept_bk_map.yaml"

_MONEYFLOW_IND_DC_UNAVAILABLE_REASON: str | None = None

# AkShare/同花顺 fallback cache. 2026-06-14: Tushare moneyflow_ind_dc
# 权限不可用时, 用同花顺行业/概念资金流排行做 5d/20d 方向性替代。
# 该源不是逐日历史明细, 不能复刻 v3.2 z-score, 但能稳定提供
# cron 需要的 flow_5d / flow_20d 净额方向。
_THS_FLOW_CACHE: dict[tuple[str, str], Any] = {}
_THS_FLOW_UNAVAILABLE_REASON: str | None = None


def _looks_like_no_permission(text: str) -> bool:
    m = (text or "").lower()
    return any(x in m for x in (
        "没有接口", "没有权限", "无权限", "访问权限",
        "no permission", "permission denied", "not authorized", "unauthorized",
    ))


# ─── concept→BK 映射加载 (lazy + cached) ──────────────────────────────────

_concept_bk_map_cache: dict[str, dict] | None = None


def load_concept_bk_map() -> dict[str, dict]:
    global _concept_bk_map_cache
    if _concept_bk_map_cache is not None:
        return _concept_bk_map_cache
    try:
        import yaml
    except ImportError:
        sys.stderr.write("⚠️  PyYAML not installed, falling back to json parse\n")
        return {}
    if not _BK_MAP_YAML.exists():
        return {}
    with open(_BK_MAP_YAML) as f:
        doc = yaml.safe_load(f)
    _concept_bk_map_cache = doc.get("concepts", {}) or {}
    return _concept_bk_map_cache


def bks_for_concept(concept: str) -> list[dict[str, str]]:
    """Return list of {code, name, kind} for the concept, or [] if pending/missing."""
    cmap = load_concept_bk_map()
    cfg = cmap.get(concept, {})
    if not cfg or cfg.get("status") == "pending":
        return []
    return cfg.get("bks", []) or []


# ─── tushare fetch ──────────────────────────────────────────────────────────

def _ts_csv(api: str, **params) -> list[dict[str, str]]:
    global _MONEYFLOW_IND_DC_UNAVAILABLE_REASON
    if api == "moneyflow_ind_dc" and _MONEYFLOW_IND_DC_UNAVAILABLE_REASON:
        return []

    args = ["python3", str(_TUSHARE), api]
    for k, v in params.items():
        if k == "fields":
            args.append(f"--fields={v}")
        else:
            args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        if api == "moneyflow_ind_dc" and _looks_like_no_permission(r.stderr):
            _MONEYFLOW_IND_DC_UNAVAILABLE_REASON = (
                "moneyflow_ind_dc unavailable/no permission; neutral fallback"
            )
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def fetch_bk_recent(bk_code: str) -> list[dict[str, Any]]:
    """Fetch all available rows for a BK; return ASC by date."""
    rows = _ts_csv("moneyflow_ind_dc", ts_code=bk_code)
    parsed = []
    for r in rows:
        try:
            parsed.append({
                "date": r["trade_date"],
                "close": float(r["close"]),
                "elg": float(r.get("buy_elg_amount") or 0),
                "lg":  float(r.get("buy_lg_amount") or 0),
                "md":  float(r.get("buy_md_amount") or 0),
                "sm":  float(r.get("buy_sm_amount") or 0),
                "net": float(r.get("net_amount") or 0),
                "rate": float(r.get("net_amount_rate") or 0),
            })
        except (KeyError, ValueError):
            continue
    parsed.sort(key=lambda x: x["date"])
    return parsed


# ─── 信号计算 ──────────────────────────────────────────────────────────────

@dataclass
class BkFlowSignal:
    bk_code: str
    bk_name: str
    n_days_history: int
    main_5d: float
    main_20d: float
    sm_5d: float
    sm_20d: float
    main_minus_sm_5d: float
    main_minus_sm_20d: float
    rate_5d: float
    rate_20d: float
    # z-scores vs trailing 60d
    main_minus_sm_z: float | None
    rate_z: float | None
    sm_z: float | None        # 散户净流入 z (正向 = 散户狂买, 反指)
    # v3.2 末期反转 features
    nav20d: float | None      # close[-1] / close[-21] - 1, %
    pos60: float | None       # close[-1] 在最近 60d 的百分位, [0..1]


def compute_bk_signal(rows: list[dict]) -> BkFlowSignal | None:
    """从 ASC 排序的 BK rows 中算最新窗口的信号."""
    if len(rows) < 21:  # 至少要 20d 当前 + 1d
        return None
    last = rows[-1]
    win5 = rows[-5:]
    win20 = rows[-20:]
    main_5d  = sum(r["elg"] + r["lg"] for r in win5)
    main_20d = sum(r["elg"] + r["lg"] for r in win20)
    sm_5d    = sum(r["sm"] for r in win5)
    sm_20d   = sum(r["sm"] for r in win20)
    rate_5d  = sum(r["rate"] for r in win5) / 5
    rate_20d = sum(r["rate"] for r in win20) / 20

    # v3.2: nav20d 和 pos60 (末期反转检测)
    nav20d = None
    if len(rows) >= 21:
        c0 = rows[-21]["close"]; c1 = rows[-1]["close"]
        if c0 > 0:
            nav20d = (c1 - c0) / c0 * 100  # %
    pos60 = None
    if len(rows) >= 60:
        win60 = [r["close"] for r in rows[-60:]]
        cur = win60[-1]
        rank = sum(1 for c in win60 if c <= cur)
        pos60 = rank / len(win60)

    # 历史 20d 滚动序列, 用于 z-score
    # 需要 60d 历史 + 20d 窗口 = 至少 80 行
    main_minus_sm_z = rate_z = sm_z = None
    if len(rows) >= 80:
        hist_main_diff: list[float] = []
        hist_rate: list[float] = []
        hist_sm: list[float] = []
        # 倒数第 80 天到倒数第 21 天 (避免和当前 20d 重叠)
        for end in range(len(rows) - 20 - 60, len(rows) - 20):
            if end < 19:
                continue
            w = rows[end - 19: end + 1]
            md = sum(r["elg"] + r["lg"] - r["sm"] for r in w)
            rt = sum(r["rate"] for r in w) / 20
            sm = sum(r["sm"] for r in w)
            hist_main_diff.append(md)
            hist_rate.append(rt)
            hist_sm.append(sm)
        if len(hist_main_diff) >= 30:
            mu_md = statistics.mean(hist_main_diff)
            sd_md = statistics.stdev(hist_main_diff)
            cur_md = main_20d - sm_20d
            if sd_md > 0:
                main_minus_sm_z = (cur_md - mu_md) / sd_md
        if len(hist_rate) >= 30:
            mu_rt = statistics.mean(hist_rate)
            sd_rt = statistics.stdev(hist_rate)
            if sd_rt > 0:
                rate_z = (rate_20d - mu_rt) / sd_rt
        if len(hist_sm) >= 30:
            mu_sm = statistics.mean(hist_sm)
            sd_sm = statistics.stdev(hist_sm)
            if sd_sm > 0:
                sm_z = (sm_20d - mu_sm) / sd_sm

    return BkFlowSignal(
        bk_code=last.get("bk_code", ""),  # filled by caller
        bk_name=last.get("bk_name", ""),
        n_days_history=len(rows),
        main_5d=main_5d, main_20d=main_20d,
        sm_5d=sm_5d,    sm_20d=sm_20d,
        main_minus_sm_5d=main_5d - sm_5d,
        main_minus_sm_20d=main_20d - sm_20d,
        rate_5d=rate_5d, rate_20d=rate_20d,
        main_minus_sm_z=main_minus_sm_z,
        rate_z=rate_z,
        sm_z=sm_z,
        nav20d=nav20d,
        pos60=pos60,
    )


# ─── score map ──────────────────────────────────────────────────────────────

def _z_to_score(z: float | None, max_pts: float, default_mid: float | None = None) -> float:
    """Linear map: z=-2 → 0, z=0 → max/2, z=+2 → max. Clipped."""
    if z is None:
        return default_mid if default_mid is not None else max_pts * 0.5
    if z <= -2: return 0
    if z >= +2: return max_pts
    return max_pts * (z + 2) / 4




# ─── 华泰 OpenClaw sector-flow fallback cache ────────────────────────────────

_HTSC_FLOW_CACHE_FILE = _HERE / ".cron_state" / "htsc_sector_flow.json"


def _load_htsc_sector_flow(concept: str, *, max_age_hours: float = 24.0) -> dict[str, Any] | None:
    """Read pre-refreshed HTSC sector main-flow cache.

    Network calls are intentionally NOT done here: `sector_score --all` must stay
    deterministic and bounded. Refresh the cache with:
      python3 htsc_sector_flow.py refresh-default

    Returns None when missing/stale/unusable.
    """
    try:
        if not _HTSC_FLOW_CACHE_FILE.exists():
            return None
        payload = json.loads(_HTSC_FLOW_CACHE_FILE.read_text(encoding="utf-8"))
        rec = (payload.get("concepts") or {}).get(concept)
        if not rec or not rec.get("ok"):
            return None
        ts = rec.get("updated_at")
        if ts:
            from datetime import datetime, timezone
            t = datetime.fromisoformat(str(ts))
            now = datetime.now(t.tzinfo or timezone.utc)
            if (now - t).total_seconds() > max_age_hours * 3600:
                return None
        if rec.get("flow_5d_cny") is None and rec.get("flow_20d_cny") is None:
            return None
        return rec
    except Exception:
        return None


def fund_flow_score_htsc_cached(concept: str) -> tuple[float, str, dict[str, Any]] | None:
    """0-40 fallback based on HTSC/OpenClaw 主力净流入 cache.

    This is preferred over THS/AkShare because the original model's intent is
    main-money direction (Tushare moneyflow_ind_dc large/super-large style), not
    THS total-flow `净额`, which was audited to conflict on hot tech themes.
    """
    rec = _load_htsc_sector_flow(concept)
    if not rec:
        return None
    f5 = rec.get("flow_5d_cny") or 0.0
    f20 = rec.get("flow_20d_cny") or 0.0
    pct5 = rec.get("pct_5d")
    pct20 = rec.get("pct_20d")

    def _clip(x, lo, hi): return max(lo, min(hi, x))
    score = 20.0
    score += _clip(f5 / 1e9, -1, 1) * 8.0       # 主力5d更重要; ±10亿 soft cap
    score += _clip(f20 / 5e9, -1, 1) * 5.0      # 20d背景; ±50亿 soft cap
    if f5 > 0 and f20 < 0:
        score += 3.0  # short-term turn positive
    if f5 < 0 and f20 > 0:
        score -= 3.0
    if pct5 is not None:
        score += _clip(float(pct5) / 5.0, -1, 1) * 2.0
    if pct20 is not None:
        score += _clip(float(pct20) / 10.0, -1, 1) * 1.0
    score = round(_clip(score, 0.0, 40.0), 1)
    note = f"HTSC fallback: main_flow5d={_fmt_cny(f5)} main_flow20d={_fmt_cny(f20)}"
    if pct5 is not None:
        note += f" pct5={float(pct5):+.1f}%"
    if pct20 is not None:
        note += f" pct20={float(pct20):+.1f}%"
    note += f"; score={score:.1f}/40 (cached HTSC main-flow fallback)"
    diag = {
        "source": "htsc_openclaw",
        "fallback": True,
        "flow_5d_cny": f5,
        "flow_20d_cny": f20,
        "pct_5d": pct5,
        "pct_20d": pct20,
        "flow_confidence": "medium",
        "htsc_cache_updated_at": rec.get("updated_at"),
        "htsc_labels": rec.get("labels"),
        "htsc_entries": rec.get("entries"),
        "version": "htsc_cached_main_flow_v1",
    }
    return score, note, diag


def neutral_no_htsc_flow(concept: str, reason: str, *, entries: list | None = None) -> tuple[float, str, dict[str, Any]]:
    return 20.0, f"({reason}; HTSC main-flow unavailable; NEUTRAL fallback)", {
        "fallback": True,
        "source": "neutral_htsc_unavailable",
        "reason": reason,
        "entries": entries or [],
        # Explicit zeroes prevent sector_score from falling back to deprecated ETF
        # share-flow display when HTSC cache is missing.
        "flow_5d_cny": 0.0,
        "flow_20d_cny": 0.0,
        "pct_5d": None,
        "pct_20d": None,
        "flow_confidence": "none",
    }

# ─── 同花顺 AkShare fallback ────────────────────────────────────────────────

_THS_DIRECT_ALIASES: dict[str, list[tuple[str, str]]] = {
    # concept -> [(source_type, THS板块名)] where source_type in {industry, concept}
    "AI芯片 (算力核心)": [("industry", "半导体"), ("concept", "芯片概念")],
    "存储芯片 (HBM/DDR/NAND)": [("concept", "存储芯片"), ("industry", "半导体")],
    "先进封装 (CoWoS/Chiplet)": [("concept", "先进封装"), ("industry", "半导体")],
    "光通信 (光模块/CPO)": [("concept", "共封装光学(CPO)"), ("industry", "通信设备")],
    "英伟达产业链": [("industry", "半导体"), ("industry", "通信设备")],
    "AI 应用 (软件侧)": [("concept", "AI应用"), ("concept", "人工智能")],
    "AIDC/算力租赁": [("concept", "数据中心(AIDC)"), ("concept", "算力租赁"), ("concept", "东数西算(算力)")],
    "PCB (算力基建)": [("concept", "PCB概念")],
    "华为产业链": [("industry", "半导体"), ("industry", "通信设备")],
    "硅片 (半导体材料)": [("industry", "半导体")],
    "半导体设备 (国产替代)": [("industry", "半导体")],
    "金融-银行": [("industry", "银行")],
    "金融-证券": [("industry", "证券")],
    "金融-保险": [("industry", "保险")],
    "军工": [("concept", "军工"), ("industry", "军工装备"), ("industry", "军工电子")],
    "有色金属": [("industry", "工业金属"), ("industry", "小金属"), ("industry", "贵金属")],
    "锂电产业链": [("concept", "锂电池概念"), ("industry", "电池")],
    "煤炭": [("industry", "煤炭开采加工"), ("concept", "煤炭概念")],
    "电力": [("industry", "电力"), ("concept", "绿色电力")],
    "钢铁": [("industry", "钢铁")],
    "化工": [("industry", "化学原料"), ("industry", "化学制品"), ("concept", "氟化工概念")],
    "工业机械": [("industry", "通用设备"), ("industry", "专用设备")],
    "新能源车": [("concept", "新能源汽车")],
    "新能源 (光伏/风电)": [("industry", "光伏设备"), ("concept", "绿色电力")],
    "光伏": [("industry", "光伏设备"), ("concept", "光伏概念")],
    "游戏传媒": [("industry", "游戏"), ("industry", "文化传媒"), ("concept", "网络游戏")],
    "人形机器人": [("concept", "人形机器人"), ("concept", "机器人概念")],
    "白酒 (消费龙头)": [("industry", "白酒"), ("concept", "白酒概念")],
    "食品饮料": [("industry", "食品加工制造"), ("industry", "饮料乳品")],
    "消费 (非白酒+非食品饮料)": [("industry", "零售"), ("industry", "商业百货")],
    "家电": [("industry", "白色家电"), ("industry", "小家电"), ("industry", "黑色家电")],
    "创新药 (医药龙头)": [("concept", "创新药"), ("industry", "化学制药")],
    "中药": [("industry", "中药")],
    "农业畜牧": [("industry", "养殖业"), ("industry", "农产品加工")],
    "地产": [("industry", "房地产开发")],
    "旅游酒店": [("industry", "旅游及酒店"), ("concept", "旅游概念")],
    "高股息 (红利防御)": [("industry", "银行"), ("industry", "煤炭开采加工"), ("industry", "电力")],
    "央企国企": [("concept", "央企国企改革")],
    "固态电池": [("concept", "固态电池")],
}


def _ths_load(kind: str, window: str):
    """Load THS fund-flow rank via akshare with process cache.

    kind: industry|concept; window: 5日排行|20日排行|即时|...
    Returns pandas.DataFrame or None. Suppresses tqdm/progress noise.
    """
    global _THS_FLOW_UNAVAILABLE_REASON
    key = (kind, window)
    if key in _THS_FLOW_CACHE:
        return _THS_FLOW_CACHE[key]
    try:
        import akshare as ak
        fn = ak.stock_fund_flow_industry if kind == "industry" else ak.stock_fund_flow_concept
        # akshare uses tqdm; keep sector_score output clean.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            df = fn(symbol=window)
        _THS_FLOW_CACHE[key] = df
        return df
    except Exception as e:
        _THS_FLOW_UNAVAILABLE_REASON = f"THS akshare fallback unavailable: {type(e).__name__}: {e}"
        _THS_FLOW_CACHE[key] = None
        return None


def _num_yi_to_cny(x: Any) -> float | None:
    """THS fund-flow tables report amounts in 亿元-like numeric columns."""
    if x is None:
        return None
    try:
        s = str(x).strip().replace("%", "").replace(",", "")
        if s in ("", "None", "nan"):
            return None
        return float(s) * 1e8
    except Exception:
        return None


def _pct_to_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(str(x).strip().replace("%", "").replace(",", ""))
    except Exception:
        return None


def _ths_lookup(kind: str, name: str, window: str) -> dict[str, Any] | None:
    df = _ths_load(kind, window)
    if df is None or getattr(df, "empty", True):
        return None
    if "行业" not in df.columns:
        return None
    rows = df[df["行业"].astype(str) == name]
    if rows.empty:
        return None
    r = rows.iloc[0].to_dict()
    net = _num_yi_to_cny(r.get("净额"))
    inflow = _num_yi_to_cny(r.get("流入资金"))
    outflow = _num_yi_to_cny(r.get("流出资金"))
    pct = _pct_to_float(r.get("阶段涨跌幅") if "阶段涨跌幅" in r else r.get("行业-涨跌幅"))
    return {
        "kind": kind,
        "name": name,
        "window": window,
        "net_cny": net,
        "inflow_cny": inflow,
        "outflow_cny": outflow,
        "pct": pct,
        "rank": int(r.get("序号")) if str(r.get("序号", "")).isdigit() else r.get("序号"),
        "raw": r,
    }


def _ths_entries_for_concept(concept: str) -> list[tuple[str, str]]:
    # Explicit mapping first. This intentionally covers both mapped and pending concepts.
    if concept in _THS_DIRECT_ALIASES:
        return _THS_DIRECT_ALIASES[concept]

    # Reuse BK names where possible for mapped concepts (e.g. 半导体 / 通信设备).
    out: list[tuple[str, str]] = []
    for bk in bks_for_concept(concept):
        kind = "industry" if bk.get("kind") == "industry" else "concept"
        name = (bk.get("name") or "").replace("概念", "概念")
        out.append((kind, name))
    return out


def fund_flow_score_ths(concept: str) -> tuple[float, str, dict[str, Any]]:
    """0-40 fallback based on THS 5d/20d sector net-flow direction.

    This is a **directional fallback**, not a replacement for v3.2 z-score:
    - Uses 同花顺 industry/concept 5日排行 + 20日排行 via akshare.
    - Scores around base=20 with bounded swings from 5d/20d net flow and return.
    - Primary purpose: keep weekend preview's flow_5d/flow_20d direction usable
      when Tushare moneyflow_ind_dc is unavailable.
    """
    entries = _ths_entries_for_concept(concept)
    if not entries:
        return 20.0, "(no THS mapping; NEUTRAL fallback)", {"fallback": True, "source": "neutral_no_ths_mapping", "entries": []}

    hits = []
    for kind, name in entries:
        h5 = _ths_lookup(kind, name, "5日排行")
        h20 = _ths_lookup(kind, name, "20日排行")
        if h5 or h20:
            hits.append({"kind": kind, "name": name, "flow_5d": h5, "flow_20d": h20})

    if not hits:
        reason = _THS_FLOW_UNAVAILABLE_REASON or "THS mapped sectors not found"
        return 20.0, f"({reason}; NEUTRAL fallback)", {
            "fallback": True, "source": "neutral_ths_unavailable", "entries": entries, "reason": reason}

    f5_vals = [h["flow_5d"]["net_cny"] for h in hits if h.get("flow_5d") and h["flow_5d"].get("net_cny") is not None]
    f20_vals = [h["flow_20d"]["net_cny"] for h in hits if h.get("flow_20d") and h["flow_20d"].get("net_cny") is not None]
    pct5_vals = [h["flow_5d"]["pct"] for h in hits if h.get("flow_5d") and h["flow_5d"].get("pct") is not None]
    pct20_vals = [h["flow_20d"]["pct"] for h in hits if h.get("flow_20d") and h["flow_20d"].get("pct") is not None]

    avg5 = statistics.mean(f5_vals) if f5_vals else 0.0
    avg20 = statistics.mean(f20_vals) if f20_vals else 0.0
    pct5 = statistics.mean(pct5_vals) if pct5_vals else None
    pct20 = statistics.mean(pct20_vals) if pct20_vals else None

    # 2026-06-17 audit: AkShare/THS `stock_fund_flow_*` 5日排行的 `净额`
    # can strongly disagree with HTSC/OpenClaw "主力净流入" for the same hot
    # semiconductor/CPO/PCB themes. Example: THS 5日净额 negative while HTSC
    # reports large positive 5d main inflow and THS itself shows strong positive
    # price momentum + positive 20d net. Treat that state as LOW CONFIDENCE:
    # preserve raw THS value for audit, but do not let it hard-downgrade a sector.
    conflict_likely = bool(avg5 < 0 and avg20 > 0 and (pct5 or 0.0) > 2.0)
    scoring_avg5 = 0.0 if conflict_likely else avg5
    flow_confidence = "low" if conflict_likely else "medium"

    # Robust bounded score. THS净额为板块总额, 不同板块公司数差异大；因此只给有限 swing。
    # 5d 更重视拐点, 20d 看趋势背景。阈值用 10/50 亿作软饱和。
    def _clip(x, lo, hi): return max(lo, min(hi, x))
    score = 20.0
    score += _clip(scoring_avg5 / 1e9, -1, 1) * 6.0     # ±6 for ±10亿 5d
    score += _clip(avg20 / 5e9, -1, 1) * 7.0            # ±7 for ±50亿 20d
    if scoring_avg5 > 0 and avg20 < 0:
        score += 3.0  # flow 拐点
    if scoring_avg5 < 0 and avg20 > 0:
        score -= 2.0  # 短期转弱
    if pct5 is not None:
        score += _clip(pct5 / 5.0, -1, 1) * 2.0
    if pct20 is not None:
        score += _clip(pct20 / 10.0, -1, 1) * 2.0

    score = round(_clip(score, 0.0, 40.0), 1)
    note = (f"THS fallback: flow5d={_fmt_cny(avg5)} flow20d={_fmt_cny(avg20)}"
            f" pct5={pct5:+.1f}%" if pct5 is not None else f"THS fallback: flow5d={_fmt_cny(avg5)} flow20d={_fmt_cny(avg20)}")
    if pct20 is not None:
        note += f" pct20={pct20:+.1f}%"
    if conflict_likely:
        note += "; LOW_CONF: THS 5d total-net conflicts with HTSC/main-flow style signals; 5d not used for hard downgrade"
    note += f"; score={score:.1f}/40 (directional, not z-score)"
    diag = {
        "source": "ths_akshare",
        "fallback": True,
        "entries": entries,
        "hits": hits,
        "flow_5d_cny": avg5,
        "flow_5d_cny_for_scoring": scoring_avg5,
        "flow_20d_cny": avg20,
        "pct_5d": pct5,
        "pct_20d": pct20,
        "flow_confidence": flow_confidence,
        "cross_source_conflict_likely": conflict_likely,
        "version": "ths_fallback_v2_low_conflict_guard",
    }
    return score, note, diag


def fund_flow_score_v3(concept: str) -> tuple[float, str, dict[str, Any]]:
    """0-40 板块资金面分 (v3.2, soft + revert + 末期反转 penalty).

    设计 (基于 backtest_sector_v3.py --compare 多次迭代):
      score = 20 (base) + cm * 10 + cr * 3
        cm = clip(z(main_minus_sm_20d), -1, +1)   # 主信号 (回归 r=+0.22)
        cr = clip(z(rate_20d),         -1, +1)    # 辅信号 (净流入率)

      若 |z(main)| > 1.5σ:  极端反转拉回 30% 中段
        score' = 0.7 * score + 0.3 * 20

      [v3.2 新增] 末期反转 penalty (hi_only 版):
      若 nav20d ≥ 15% AND pos60 ≥ 0.9 AND raw_score > 20 (base):
        score'' = 0.5 * score' + 0.5 * 20  (拉回 50% 中段)
        ⚠ 只罚高分: 低位+末期是"高位回调"非"末期狂热", 不应被错误奖励
        (历史 prereq: 末期 A 状态 fwd5d=+0.01% vs 整体 +1.04%, 命中率清晰)

    回测对比 (n=3955 panel, 11 mapped concept × 360d):
      v3 (老)         IC_5d=+0.111  IC_20d=+0.220  极端%=27.4%  IC_std=0.388
      v3.1 (soft+rev) IC_5d=+0.134  IC_20d=+0.236  极端%= 0.0%  IC_std=0.297
      v3.2 (+ob_h05)  IC_5d=+0.140  IC_20d=+0.252  Q5-Q1=+6.30  IC_std=0.314 ⭐

      v3.2 vs v3.1: IC_5d +4.5%, IC_20d +6.8%, Q5-Q1_20d +8.4%, IC_std 退化 5.7%

    [D 任务实证, 弃用]: 横截面 rank 替换在我们的 case 反而 IC 暴跌:
      xs_rank_pure   IC_5d=+0.026 (-82%)  -- 11 concept 信号同源, rank 丢绝对水平信息
      xs_hybrid_30   IC_5d=+0.112 (-21%)
    所以保留绝对 z-score, 接受同源 BK 时同分必然. 上层 sector_score 总分通过基本面/
    技术面/Gate 自然打散 (经实证: 4 共用 BK1036 板块的 fund 同分 25.3, 但总分被基本面
    拉开 14 分: 61.4 / 52.1 / 51.0 / 47.0).

    Return (score, note_str, diagnostics).
    """
    bks = bks_for_concept(concept)
    if not bks:
        # Pending/missing BK mapping: prefer pre-refreshed HTSC main-flow cache.
        htsc = fund_flow_score_htsc_cached(concept)
        if htsc:
            score, note, diag = htsc
            diag.setdefault("bks", [])
            return score, note, diag
        if os.environ.get("MARKET_TOOLS_ENABLE_THS_FLOW_FALLBACK") == "1":
            score, note, diag = fund_flow_score_ths(concept)
            diag.setdefault("bks", [])
            return score, note, diag
        return neutral_no_htsc_flow(concept, "no BK mapping and no fresh HTSC sector-flow cache")

    if _MONEYFLOW_IND_DC_UNAVAILABLE_REASON:
        htsc = fund_flow_score_htsc_cached(concept)
        if htsc:
            score, note, diag = htsc
            diag.update({
                "bks": bks, "tushare_fetch_failed": True,
                "tushare_reason": _MONEYFLOW_IND_DC_UNAVAILABLE_REASON,
            })
            return score, note, diag
        if os.environ.get("MARKET_TOOLS_ENABLE_THS_FLOW_FALLBACK") == "1":
            score, note, diag = fund_flow_score_ths(concept)
            diag.update({
                "bks": bks, "tushare_fetch_failed": True,
                "tushare_reason": _MONEYFLOW_IND_DC_UNAVAILABLE_REASON,
            })
            return score, note, diag
        return neutral_no_htsc_flow(concept, _MONEYFLOW_IND_DC_UNAVAILABLE_REASON, entries=bks)

    sigs: list[BkFlowSignal] = []
    for entry in bks:
        rows = fetch_bk_recent(entry["code"])
        if not rows:
            continue
        sig = compute_bk_signal(rows)
        if sig is None:
            continue
        sig.bk_code = entry["code"]
        sig.bk_name = entry["name"]
        sigs.append(sig)

    if not sigs:
        reason = _MONEYFLOW_IND_DC_UNAVAILABLE_REASON or "BK fetch all failed"
        htsc = fund_flow_score_htsc_cached(concept)
        if htsc:
            score, note, diag = htsc
            diag.update({"bks": bks, "tushare_fetch_failed": True, "tushare_reason": reason})
            return score, note, diag
        if os.environ.get("MARKET_TOOLS_ENABLE_THS_FLOW_FALLBACK") == "1":
            score, note, diag = fund_flow_score_ths(concept)
            diag.update({"bks": bks, "tushare_fetch_failed": True, "tushare_reason": reason})
            return score, note, diag
        return neutral_no_htsc_flow(concept, reason, entries=bks)

    z_main_diff = [s.main_minus_sm_z for s in sigs if s.main_minus_sm_z is not None]
    z_rate = [s.rate_z for s in sigs if s.rate_z is not None]
    z_sm = [s.sm_z for s in sigs if s.sm_z is not None]
    avg_z_main = statistics.mean(z_main_diff) if z_main_diff else None
    avg_z_rate = statistics.mean(z_rate) if z_rate else None
    avg_z_sm = statistics.mean(z_sm) if z_sm else None

    # v3.1 软化映射: clip ±1σ → 直接 [-1, +1] (不再除 2)
    def _zclip1(z): return max(-1.0, min(1.0, z)) if z is not None else None

    base = 20.0
    coef_main = _zclip1(avg_z_main)
    coef_rate = _zclip1(avg_z_rate)
    main_pts = (coef_main * 10.0) if coef_main is not None else 0.0  # ±10
    rate_pts = (coef_rate *  3.0) if coef_rate is not None else 0.0  # ±3

    raw_score = base + main_pts + rate_pts
    revert_applied = False
    if avg_z_main is not None and abs(avg_z_main) > 1.5:
        raw_score = 0.7 * raw_score + 0.3 * base  # 极端反转拉回 30%
        revert_applied = True

    # v3.2 末期反转 penalty: nav20 ∈ [15%, 30%) AND pos60≥0.9 → 拉回 50% 中段
    # ⚠ 只罚高分 (raw_score > base), 不奖励低分: 低位 + 末期是"高位回调"而非"末期狂热"
    # ⚠ nav20 ≥ 30% 上限保护: 主升浪强势趋势 (e.g. 月度涨 30%+), 不视为末期
    #    回测 (panel n=3955, 2024-01~2026-04):
    #    - z_main≥2.5 末期 (n=64) fwd_30d 跑输正常 -4.77pp, fwd_60d 修复
    #    - nav20+pos60 末期信号偏弱: 20D gap 仅 +0.9pp (建议未来用 z_main 替代)
    avg_nav20 = statistics.mean([s.nav20d for s in sigs if s.nav20d is not None]) \
        if any(s.nav20d is not None for s in sigs) else None
    avg_pos60 = statistics.mean([s.pos60 for s in sigs if s.pos60 is not None]) \
        if any(s.pos60 is not None for s in sigs) else None
    overbought_applied = False
    if (avg_nav20 is not None and 15.0 <= avg_nav20 < 30.0
            and avg_pos60 is not None and avg_pos60 >= 0.9
            and raw_score > base):  # 只罚高分 + 上限保护
        raw_score = 0.5 * raw_score + 0.5 * base  # 末期反转拉回 50%
        overbought_applied = True

    score = max(0.0, min(40.0, round(raw_score, 1)))

    avg_sm_20d = statistics.mean([s.sm_20d for s in sigs])
    avg_main_minus_sm_20d = statistics.mean([s.main_minus_sm_20d for s in sigs])
    sm_str = _fmt_cny(avg_sm_20d)
    md_str = _fmt_cny(avg_main_minus_sm_20d)

    if avg_z_main is None and avg_z_rate is None:
        note = f"BK 历史不足 (<80d, 走中位 base). main-sm_20d_raw={md_str}, sm_20d={sm_str}"
    else:
        zm = f"{avg_z_main:+.2f}σ" if avg_z_main is not None else "n/a"
        zr = f"{avg_z_rate:+.2f}σ" if avg_z_rate is not None else "n/a"
        zs = f"{avg_z_sm:+.2f}σ"   if avg_z_sm is not None else "n/a"
        rv  = " 🔄 极端反转拉回" if revert_applied else ""
        ob  = (f" ⚠️末期反转 (nav20={avg_nav20:+.1f}%, pos60={avg_pos60*100:.0f}%)"
               if overbought_applied else "")
        note = (f"base 20 + main-sm_z {zm} ({main_pts:+.1f}) + rate_z {zr} ({rate_pts:+.1f}) "
                f"= {score:.1f}/40{rv}{ob}; sm_z={zs} (info), raw main-sm={md_str}, sm={sm_str}")

    diag = {
        "bks": [{"code": s.bk_code, "name": s.bk_name,
                 "main_minus_sm_z": s.main_minus_sm_z,
                 "rate_z": s.rate_z,
                 "sm_z": s.sm_z,
                 "sm_20d": s.sm_20d, "main_20d": s.main_20d,
                 "main_minus_sm_20d": s.main_minus_sm_20d,
                 "nav20d": s.nav20d, "pos60": s.pos60,
                 "n_history": s.n_days_history} for s in sigs],
        "avg_z_main_minus_sm": avg_z_main,
        "avg_z_rate": avg_z_rate,
        "avg_z_sm": avg_z_sm,
        "avg_nav20d": avg_nav20,
        "avg_pos60": avg_pos60,
        "main_pts": main_pts,
        "rate_pts": rate_pts,
        "base": base,
        "revert_applied": revert_applied,
        "overbought_applied": overbought_applied,
        "version": "v3.2",
    }
    return score, note, diag


# ─── helpers ────────────────────────────────────────────────────────────────

def _fmt_cny(x: float) -> str:
    if abs(x) >= 1e8:
        return f"{x/1e8:+.2f}亿"
    if abs(x) >= 1e4:
        return f"{x/1e4:+.0f}万"
    return f"{x:+.0f}"


# ─── CLI ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--concept", required=False, help="测试单个 concept 的 fund_flow_score_v3")
    p.add_argument("--list", action="store_true", help="列出 yaml 里所有 mapping 状态")
    p.add_argument("--all", action="store_true", help="跑全部已映射 concept")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    cmap = load_concept_bk_map()
    if args.list:
        mapped = [c for c, v in cmap.items() if v.get("bks")]
        pending = [c for c, v in cmap.items() if v.get("status") == "pending"]
        print(f"📋 concept_bk_map.yaml 状态:")
        print(f"  已映射: {len(mapped)} concepts")
        for c in mapped:
            bks = cmap[c]["bks"]
            print(f"    {c}: {[b['code'] for b in bks]}")
        print(f"\n  PENDING: {len(pending)} concepts")
        for c in pending:
            print(f"    {c}: hint={cmap[c].get('hint','-')}")
        return

    targets = []
    if args.concept:
        targets = [args.concept]
    elif args.all:
        targets = [c for c, v in cmap.items() if v.get("bks")]
    else:
        p.print_help(); return

    out = []
    for c in targets:
        score, note, diag = fund_flow_score_v3(c)
        out.append({"concept": c, "score": score, "note": note, "diag": diag})
        if not args.json:
            print(f"\n━━━ {c} ━━━")
            print(f"  score: {score}/40")
            print(f"  note:  {note}")
            if diag.get("source") == "ths_akshare":
                for h in diag.get("hits", []):
                    f5 = h.get("flow_5d") or {}
                    f20 = h.get("flow_20d") or {}
                    print(f"    THS {h.get('kind',''):<8} {h.get('name',''):<16} "
                          f"5d={_fmt_cny(f5.get('net_cny') or 0)} 20d={_fmt_cny(f20.get('net_cny') or 0)}")
            else:
                for bk in diag.get("bks", []):
                    if not isinstance(bk, dict) or "main_minus_sm_z" not in bk:
                        continue
                    zm = f"{bk['main_minus_sm_z']:+.2f}" if bk['main_minus_sm_z'] is not None else "n/a"
                    zr = f"{bk['rate_z']:+.2f}" if bk['rate_z'] is not None else "n/a"
                    print(f"    {bk['code']:<14} {bk['name']:<14} z(main-sm)={zm}  z(rate)={zr}  n={bk['n_history']}")

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
