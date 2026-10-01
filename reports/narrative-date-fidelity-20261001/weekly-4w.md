# 叙事雷达 · 推演验证 (2026-10-01)

> 数据质量审计：显式排除 4 条历史异常记录（全账本）；原始 append-only 记录及 archive 保留不变。排除清单：narrative_perf_exclusions.jsonl。
> 排除 20261001 / 300274.SZ：CN休市：20260930价格误标20261001；benchmark_current缺失 （sha256=dd964071e4f9806e3685cf529f357893a2e40ef239c14fa8ac2d73358e29a296）
> 排除 20261001 / 601126.SH：CN休市：20260930价格误标20261001；benchmark_current缺失 （sha256=7f2d009047015a29fefbd90c071318d6ff3faeedb31d940e8ccd930ff9fa2793）
> 排除 20261001 / 601179.SH：CN休市：20260930价格误标20261001；benchmark_current缺失 （sha256=e347b4a07eedd865e2c35794ce99f00952723d596fb83cf062e7073d71ccde2e）
> 排除 20261001 / 301308.SZ：CN休市：20260930价格误标20261001；benchmark_current缺失 （sha256=1ee1b9e1bf65f98e1f16e63ecd23f23840f8e257346c3ce9148730b7365e711a）

**回看窗口**: 过去 4 周 · 自 2026-09-03 起
**覆盖 events**: 0 条 · **ticker pair**: 0 个

> **hit 判定 (vs 大盘)**: side=+ → excess_pct > 0; side=- → excess_pct < 0. excess_pct = ticker 涨跌% - benchmark 涨跌% (CSI300 / HSI)，是价格收益差，不是回归估计的alpha。
>
> **strict 判定 (vs 大盘 + 板块)**: hit AND hit_vs_sector，按原side方向分别判定。这是双基准相对表现观察，不证明因果、稳定alpha或可交易性。仅 A 股有 sector 数据，HK/US 不计入 strict；样本数和来源限制必须同时列示。
>
> baseline 锚定: 使用原始发布时间 + session；盘前/盘后取下一可交易开盘，盘中取当日收盘。旧事件缺发布时间时才回退 trade_date，不能视为严格可交易样本.
>
> **主判定窗口 = D14-D28** (W21 v3 §4 横截面证据). T+5/T+10 列为 noise 区, 仅供参考, 不当决策依据.

## 🎯 主信号 (D14-D28 窗口)

> 当前回看窗口内无 D14-D28 数据 — 大部分 event 太新还没穿越主信号窗口.
> 等 cron 累积 2+ 周后会自动填充.

## 窗口分桶汇总 (相对收益观察，桶内期限可能不同)

| bucket | 范围 | n | hit_rate | excess vs 大盘 | strict_n | strict_rate | excess vs 板块 | 性质 |
|--------|------|---|----------|----------------|----------|-------------|----------------|------|
| noise | D0-7 | 0 | 0% | — | 0 | — | — | ⚠️噪音 |
| early | D8-13 | 0 | 0% | — | 0 | — | — |  |
| main | D14-28 | 0 | 0% | — | 0 | — | — | **主判定** |
| extended | D29-60 | 0 | 0% | — | 0 | — | — |  |

## 各 milestone 命中率 (细节)

| 窗口 | n | hit_rate | 中位 excess% | strict_rate | 中位 excess vs 板块 | 标签 |
|------|---|----------|--------------|-------------|---------------------|------|
| T+5 | 0 | — | — | — | — | — |
| T+10 | 0 | — | — | — | — | — |
| T+14 | 0 | — | — | — | — | — |
| T+21 | 0 | — | — | — | — | — |
| T+28 | 0 | — | — | — | — | — |
| T+40 | 0 | — | — | — | — | — |

## 按 event score 拆解 (latest)

| score | n | hit_rate | 中位 excess% | strict_rate | excess vs 板块 |
|------|---|----------|--------------|-------------|----------------|

## Score 校准（固定 T+14，同持有期）

| score | n | hit_rate | 中位 excess% | strict_rate |
|------|---|----------|--------------|-------------|

> 仅使用 `days_since_event == 14` 的精确同期限样本判断 score 单调性；上方 latest 表只看当前状态。

## 样本池拆解（legacy vs P0 trade）

| pool | n | hit_rate | 中位 excess% | strict_rate | excess vs 板块 |
|------|---|----------|--------------|-------------|----------------|

> P0 research 事件不进入 alpha 跟踪；P0 trade 是新规则的样本外组合，legacy 仅保留历史复盘。

## 按 track 拆解

| track | n | hit_rate | 中位 excess% | strict_rate | excess vs 板块 |
|------|---|----------|--------------|-------------|----------------|

## 按 subdomain 拆解 (优先按 strict_rate 排序)

| subdomain | n | hit_rate | 中位 excess% | strict_rate | excess vs 板块 |
|------|---|----------|--------------|-------------|----------------|

## 按 sub_domain × 时间线 (赛道维度 → 个股维度)

> 同 sub_domain hit_rate 高的赛道先列, 内部按 days_since_event 倒序 (最新事件在前). 看完一个赛道是否在兑现, 再看里面具体哪些 ticker 在驱动.

---

**解读 / 决策框架**:
- **看 §🎯 主信号 (D14-D28)** — 周报最该关注的数字. T+5/T+10 是 noise 区, 不当决策依据.
- 赛道、事件类型、评分和末期抱团分组只作探索；固定同期限、核对独立事件数量后再提出假设，不凭latest混合期限校准。
- 分组差异不直接证明筛选或降权有效；须保留失败样本并做独立样本外验证。
- 不设单周50%/60%命中率为有效性结论或自动调参门槛；样本不足与数据缺失单列。

*narrative_track / cron: 每日 19:45 工作日 · 报告由 `narrative_track.py doc --weeks 4` 生成*
