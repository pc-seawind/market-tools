# 叙事特征 schema (predictive features)

> **目的**: 在 event 发生当下打标, 用于预判该 narrative 的兑现概率。
> 所有特征**必须**仅依赖 event 本身的内容 (title / rationale / thesis_seed / track / subdomain),
> **不能**依赖未来涨跌 — 否则就是 outcome attribution 不是 prediction。

## 8 个维度

### 1. catalyst_type — 催化剂类型 (枚举)
- `business_data`: 含具体业务数字 (营收/出货/价格变化)。最强预测信号 (有锚)。
- `policy`: 政策/标准/监管发布
- `industry_chain`: 产业链上游事件 (TSMC 收购 / 三星 capex / NVDA 发布)
- `m_a_capex`: 公司层面 M&A / capex 公告
- `concept`: 概念/趋势/转型预期 (无具体数据,如 "AI 转型加速")
- `technical_signal`: 价格/量能信号 (突破/缩量/分歧)
- `other`

### 2. has_quantified_data — 是否含量化数据 (bool)
True if title 含百分比 / 数字 / 倍数 / 美元金额 等具体数据。
- 强信号: "HBM3 价格 180→700 美元 (+260%)" / "CoWoS 产能扩张 96000 平方米"
- 弱信号: "AI 转型加速" / "市场需求强劲"

### 3. has_explicit_catalyst_date — 是否有显式时间锚 (bool)
True if rationale 提到具体未来日期/财报季 (e.g. "Q2 业绩"、"6/10 WWDC")。
有时间锚 → 市场知道何时验证 → 资金愿意 position。

### 4. supply_chain_position — A 股 ticker 在产业链的位置
- `upstream`: 材料 / 设备 / 封装 / 接口芯片 (卖铲人)
- `midstream`: 制造 / 代工
- `downstream`: 终端 / 应用 / 设计公司 (面对最终需求)
- `cross_position`: 同 narrative 下 ticker 跨多个层级 (混合定位)

### 5. driver_origin — 驱动源
- `global_demand`: 全球需求拉动 (NVDA / TSMC / Apple 等海外巨头)
- `domestic_substitution`: 国产替代 (本土需求 + 政策推动)
- `domestic_demand`: 纯国内需求 (家电/食品)
- `cross`: 全球需求 + 国产替代叠加

### 6. specificity — 受益标的特异性 (枚举)
- `narrow`: 受益标的明确且少 (≤5 家),narrative 直接传导
- `medium`: 中等 (6-15 家)
- `broad`: 整个板块 (>15 家),容易稀释成 beta

### 7. is_lagging_indicator — 是 lagging indicator 吗? (bool)
True if narrative 描述的是**已发生事实** (出货数据 / 历史业绩),
False if 描述**未来预期** (产能扩张 / 政策落地 / 价格趋势)。

lagging → 市场可能已 priced in。

### 8. cross_domain — 是否 cross-domain 叙事 (bool)
基于 track 字段: 含 "×" 即为 cross_domain (如 "AI×家居")。
cross-domain 历史兑现率最低。

---

## 打标规则 (heuristic)

### catalyst_type 分类规则 (优先级从高到低)
```python
if any(kw in title for kw in ["收购", "capex", "扩产", "投资"]) and 公司名 in title:
    return "m_a_capex"
if any(kw in title for kw in ["TSMC", "NVDA", "三星", "美光"]) or "上游" in title:
    return "industry_chain"
if any(kw in title for kw in ["发布", "标准", "监管", "商务部", "国务院"]):
    return "policy"
if has_quantified_data and ("出货" in title or "营收" in title or "价格" in title):
    return "business_data"
if any(kw in title for kw in ["突破", "破位", "分歧", "缩量"]):
    return "technical_signal"
if any(kw in title for kw in ["趋势", "加速", "转型", "时代"]):
    return "concept"
return "other"
```

### has_quantified_data
正则: `\d+(\.\d+)?%|\d+\s*(亿|万|倍|美元|台|片)|\d+→\d+`

### has_explicit_catalyst_date
关键词: `Q1`, `Q2`, `Q3`, `Q4`, `H1`, `H2`, `财报`, `年报`, `预期`, 具体日期 (yyyy-mm)

### supply_chain_position
基于 subdomain 推:
- `ai__advanced_packaging` → upstream (封装上游)
- `ai__memory_storage` → midstream (存储模组)
- `ai__compute_chip` → cross_position (代工/接口/设计混合)
- `ai__edge_device` → downstream
- `ai__optical_module` → upstream
- `ai__pcb_substrate` → upstream
- `cross__*` → cross_position
- `home__*` → downstream

### driver_origin
- title 含 "国产" / "替代" / "自主" → `domestic_substitution`
- title 含 "TSMC/NVDA/Apple/三星/美光/欧美" 等海外巨头 → `global_demand`
- track 是 "家居"/"消费" 且无国产替代关键词 → `domestic_demand`
- 其他 AI 类 → `cross` (默认有全球需求 + 国产替代叠加)

### specificity
基于 tickers 数量 (但 radar 只挑 3-5 个核心 ticker, 实际不准确)
更可靠的方法: 看 subdomain 在 stock_basic 里的潜在受益标的池大小:
- `advanced_packaging` 板块: ~10 家 OSAT → narrow
- `memory_storage`: ~15 家 → medium
- `edge_device`: ~50+ 家果链/消费电子 → broad
- `compute_chip`: ~10 家但分上下游 → medium

### is_lagging_indicator
title 含 "已" / "出货" / "全年" / 历史季度数据 → True
title 含 "拐点" / "加速" / "趋势" / "扩产" → False

### cross_domain
track 含 "×" → True
