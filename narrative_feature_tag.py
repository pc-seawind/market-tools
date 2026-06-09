"""narrative_feature_tag.py — 给 narrative_events.jsonl 打 predictive 特征标签。

输出: narrative_events_features.jsonl, 每行 = 原 event + features dict。

特征维度 (见 narrative_feature_schema.md):
  catalyst_type, has_quantified_data, has_explicit_catalyst_date,
  supply_chain_position, driver_origin, specificity,
  is_lagging_indicator, cross_domain

用法:
  python3 narrative_feature_tag.py
  python3 narrative_feature_tag.py --print  # 不写文件, 打印每条 event 的标签
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
EVENTS_PATH = _HERE / "narrative_events.jsonl"
FEATURES_PATH = _HERE / "narrative_events_features.jsonl"


# ─── catalyst_type ──────────────────────────────────────────────

# 海外巨头关键词 (industry_chain 信号)
_GLOBAL_GIANTS = ["TSMC", "台积电", "NVDA", "英伟达", "三星", "Samsung", "美光", "Micron",
                  "Apple", "苹果", "Meta", "Google", "微软", "Microsoft", "OpenAI",
                  "DeepSeek", "ASML", "Intel"]

# m_a / capex 关键词
_MA_CAPEX_KW = ["收购", "并购", "capex", "扩产", "投建", "投资额", "上调"]

# policy 关键词
_POLICY_KW = ["商务部", "国务院", "工信部", "发改委", "标准", "监管", "新规",
              "规定", "公告", "白皮书", "政策"]

# business_data 关键词 (业绩/出货/价格)
_BUSDATA_KW = ["出货", "营收", "净利", "毛利", "销量", "市占率", "合约价",
               "价格", "短缺", "缺口", "份额"]

# technical 关键词
_TECH_KW = ["突破", "破位", "缩量", "放量", "分歧", "信号", "趋势线"]

# concept 关键词
_CONCEPT_KW = ["趋势", "加速", "时代", "转型", "革命", "崛起", "黄金"]


def classify_catalyst(title: str, rationale: str = "") -> str:
    """
    分类逻辑 (优先级从高到低):
      1. m_a_capex: 显式公司 capex / 收购公告 (含金额)
      2. policy: 政府/标准/监管 (政策性发布最明确)
      3. business_data: 含业绩/出货/订单/份额/价格的具体业务数字
      4. industry_chain: 海外巨头驱动 (TSMC/NVDA/三星等) 但不含业务数字
      5. technical_signal: 价格/量能信号
      6. concept: 概念/趋势/转型 (无具体数据)
      7. other
    """
    text = title + " " + rationale

    # 优先级 1: m_a_capex
    if any(kw in text for kw in _MA_CAPEX_KW):
        if re.search(r"\d+\s*(亿|万|台币|美元|元)", text):
            return "m_a_capex"

    # 优先级 2: policy (政府/标准, 最明确)
    if any(kw in text for kw in _POLICY_KW):
        return "policy"

    # 优先级 3: business_data (业务数字, 比海外巨头更具体)
    # 关键词扩展: 加入"市占"、"订单"、"批量"、"量产"、"销量"
    busdata_kw_ext = _BUSDATA_KW + ["市占", "订单", "批量供货", "量产", "销量", "供货"]
    has_busdata_kw = any(kw in text for kw in busdata_kw_ext)
    has_quantity = bool(re.search(r"\d+(\.\d+)?\s*[%倍亿万]|\d+\s*[→\->]\s*\d+", text))
    if has_busdata_kw and has_quantity:
        return "business_data"

    # 优先级 4: industry_chain (海外巨头驱动 / 上游事件)
    if any(g in text for g in _GLOBAL_GIANTS):
        return "industry_chain"
    if any(kw in text for kw in ["上游", "产能扩张", "产业链"]):
        return "industry_chain"

    # 优先级 5: technical_signal (但要排除 business_data 关键词)
    if any(kw in text for kw in _TECH_KW) and not has_busdata_kw:
        return "technical_signal"

    # 优先级 6: concept
    if any(kw in text for kw in _CONCEPT_KW):
        return "concept"

    return "other"


# ─── has_quantified_data ────────────────────────────────────────

_QUANTIFIED_RE = re.compile(
    r"\d+(\.\d+)?\s*%"          # 百分比
    r"|\d+\s*(亿|万|倍|美元|台币|元|片|台)"   # 金额/单位
    r"|\d+\s*[→\->]\s*\d+"      # 价格变化 (180→700)
    r"|\$\d+"                    # 美元符号
)


def has_quantified_data(title: str, rationale: str = "") -> bool:
    text = title + " " + rationale
    return bool(_QUANTIFIED_RE.search(text))


# ─── has_explicit_catalyst_date ────────────────────────────────

_DATE_KW = ["Q1", "Q2", "Q3", "Q4", "H1", "H2", "财报", "年报",
            "中报", "季报", "业绩", "下半年", "上半年"]
# 具体月份: 6月, 6/10, 6-10, 2026Q3 etc.
_DATE_RE = re.compile(r"\d+月|\d{1,2}/\d{1,2}|20\d\d[Qq]\d|20\d\d-\d\d")


def has_explicit_catalyst_date(title: str, rationale: str = "", thesis_seed: str = "") -> bool:
    text = title + " " + rationale + " " + thesis_seed
    if any(kw in text for kw in _DATE_KW):
        return True
    if _DATE_RE.search(text):
        return True
    return False


# ─── supply_chain_position ─────────────────────────────────────

_POSITION_MAP = {
    "ai__advanced_packaging": "upstream",
    "ai__memory_storage": "midstream",
    "ai__compute_chip": "cross_position",
    "ai__edge_device": "downstream",
    "ai__optical_module": "upstream",
    "ai__pcb_substrate": "upstream",
    "ai__nvda_supplychain": "upstream",
    "ai__data_center": "upstream",
    "ai__server": "midstream",
    "ai__software": "downstream",
    "cross__robot_appliance": "cross_position",
    "cross__ai_appliance": "cross_position",
    "home__appliance_white": "downstream",
    "home__appliance_black": "downstream",
}


def supply_chain_position(subdomain: str) -> str:
    return _POSITION_MAP.get(subdomain, "cross_position")


# ─── driver_origin ─────────────────────────────────────────────

def driver_origin(title: str, rationale: str = "", thesis_seed: str = "", track: str = "") -> str:
    text = title + " " + rationale + " " + thesis_seed
    has_substitution = any(kw in text for kw in ["国产", "替代", "自主", "自给率", "国产化"])
    has_global = any(g in text for g in _GLOBAL_GIANTS)

    if has_substitution and has_global:
        return "cross"
    if has_substitution:
        return "domestic_substitution"
    if has_global:
        return "global_demand"
    if track in ("家居", "消费", "医药"):
        return "domestic_demand"
    # AI 类默认 cross (大多数 AI narrative 都是全球需求 + 国产替代叠加)
    if track and "AI" in track:
        return "cross"
    return "domestic_demand"


# ─── specificity ───────────────────────────────────────────────

# 基于 subdomain 受益标的池大小估计
_SPECIFICITY_MAP = {
    "ai__advanced_packaging": "narrow",      # OSAT 仅长电/华天/通富/甬矽等 ~10
    "ai__memory_storage": "medium",          # 兆易/江波龙/佰维等 ~15
    "ai__compute_chip": "medium",            # 寒武/海光/中芯/澜起等 ~10
    "ai__edge_device": "broad",              # 果链消费电子 50+
    "ai__optical_module": "narrow",          # 中际/天孚/新易盛等 ~8
    "ai__pcb_substrate": "narrow",           # 兴森/深南/胜宏等 ~6
    "ai__nvda_supplychain": "medium",        # ~15
    "cross__robot_appliance": "narrow",      # 石头/科沃斯 + 几家代工
    "cross__ai_appliance": "broad",          # 整个家电板块 50+
    "home__appliance_white": "broad",
    "home__appliance_black": "broad",
}


def specificity(subdomain: str) -> str:
    return _SPECIFICITY_MAP.get(subdomain, "medium")


# ─── is_lagging_indicator ──────────────────────────────────────

_LAGGING_KW = ["已", "出货", "全年", "营收", "Q1", "Q2", "Q3", "Q4",
               "中报", "年报", "实现", "完成"]
_FORWARD_KW = ["拐点", "加速", "趋势", "扩产", "扩张", "上调", "预期",
               "未来", "下半年", "Q3", "Q4", "明年", "新一代"]


def is_lagging_indicator(title: str, rationale: str = "") -> bool:
    text = title + " " + rationale
    lag_hits = sum(1 for kw in _LAGGING_KW if kw in text)
    fwd_hits = sum(1 for kw in _FORWARD_KW if kw in text)
    # 偏差: lagging 关键词比 forward 多 → lagging
    return lag_hits > fwd_hits


# ─── cross_domain ──────────────────────────────────────────────

def cross_domain(track: str) -> bool:
    return "×" in (track or "")


# ─── 人工 override (heuristic 错判时手动修正) ───────────────────
# key = title 前 30 字符 (去空格), value = 部分 features 覆盖
# 只覆盖被 override 的字段, 其他 feature 仍从 heuristic 来
_MANUAL_OVERRIDES = {
    # PCB 价值量翻 3.3x — 产业链上游量化 + NV 驱动, industry_chain (NV 是源头)
    "Rubin/GB300单机柜PCB价值量3.5万→11.6万": {"catalyst_type": "industry_chain"},
    # 鹏鼎 1.6T 光模块 PCB 批量供货 — 业务量产事实, business_data
    "鹏鼎控股1.6T光模块PCB批量供货,3.2T与客户开发中;": {"catalyst_type": "business_data"},
    # 新易盛 1.6T 订单 — business_data (两条几乎重复)
    "新易盛:1.6T光模块订单情况良好,预计今年出货量增长,泰国": {"catalyst_type": "business_data"},
    "新易盛:1.6T光模块订单情况良好,今年出货量增长趋势,泰国": {"catalyst_type": "business_data"},
    # 追觅市占率近50% — 含市占率定量, business_data (heuristic 误判 policy 因为 "策略"词族)
    "追觅扫地机德国市占率近50%,售价1499欧元vs同类899": {"catalyst_type": "business_data"},
}


def _override_key(title: str) -> str:
    """归一化 title 前 30 字符作 override key (去空格)."""
    return title.replace(" ", "")[:30]


# ─── 主函数 ────────────────────────────────────────────────────

def tag_event(event: dict) -> dict:
    title = event.get("title", "")
    rationale = event.get("rationale", "")
    thesis_seed = event.get("thesis_seed", "")
    track = event.get("track", "")
    subdomain = event.get("subdomain", "")

    features = {
        "catalyst_type": classify_catalyst(title, rationale),
        "has_quantified_data": has_quantified_data(title, rationale),
        "has_explicit_catalyst_date": has_explicit_catalyst_date(title, rationale, thesis_seed),
        "supply_chain_position": supply_chain_position(subdomain),
        "driver_origin": driver_origin(title, rationale, thesis_seed, track),
        "specificity": specificity(subdomain),
        "is_lagging_indicator": is_lagging_indicator(title, rationale),
        "cross_domain": cross_domain(track),
    }

    # 应用人工 override
    key = _override_key(title)
    if key in _MANUAL_OVERRIDES:
        features.update(_MANUAL_OVERRIDES[key])
        features["_overridden"] = True

    return features


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true",
                    help="不写文件, 只打印每条 event 的标签 (调试)")
    args = ap.parse_args()

    if not EVENTS_PATH.exists():
        print(f"❌ {EVENTS_PATH} not found")
        return 1

    events = []
    with open(EVENTS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    print(f"读入 {len(events)} events")

    out_rows = []
    for e in events:
        feats = tag_event(e)
        out_row = {**e, "features": feats}
        out_rows.append(out_row)
        if args.print:
            title = e.get("title", "")[:60]
            print(f"\n{e.get('ts', '?')[:10]} score={e.get('score')} {e.get('subdomain')}")
            print(f"  title: {title}")
            print(f"  features: {feats}")

    if not args.print:
        with open(FEATURES_PATH, "w", encoding="utf-8") as f:
            for r in out_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"✅ 写入 {FEATURES_PATH.name} ({len(out_rows)} 行)")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
