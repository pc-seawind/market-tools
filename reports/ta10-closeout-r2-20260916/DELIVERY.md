# 源文边界返修 R2｜待原 topic 独立验收

工单 `work_877303d371f64862a748`。仅修独立验收指出的两项缺陷，没有再扩采八股、没有模型调用、没有改宁德。

## 已修复

1. **预测与事实隔离**：7月3日 TrendForce 文章在德明利、兆易的两条绑定，及6月16日文章在兆易的一条绑定，全部保守改为 `institution_forecast`。6月16日原文同样混合“上半年将达”与“下半年预估”，本轮不拆造历史已实现统计。保留背景/条件推论价值，但现有 `validate_output` 会以 `nonfact_source_as_fact` 拒绝放入 facts。没有放宽 coverage；受影响行业覆盖由 pass 降为 blocked。
2. **正文窗口**：`admit` 增加可选且必须经原文hash绑定的人工审核边界。7月3日文章为原 text `[931,2698)`，6月16日文章为 `[1204,2596)`；均从实际文章标题开始，到购报告提示之前结束。导航、上下篇、订阅、相关文章和9月14日后续标题均在窗口外。段落和数值 offset 均映射回原 text 的绝对 Unicode 字符下标（左闭右开），不是临时裁剪字符串坐标。原 raw/text 不变，日期核验与数值抽取也不能逃逸窗口。

## 新 revision 与消费者

- 新 collection：`collection.json`，sha256 **3c333d3f4383e15fc6334b29640b6b2ca6b92ff851d3a5662df6d821cad3bd49**。
- 父级是上轮 `../ta10-closeout-20260916/collection.json`；旧 collection、原 source-review、原消费者及索引均原样保留，未回写旧证据。
- 三条绑定使用新 evidence_id，映射见 `evidence-id-map.json`；其余53条证据逐字段相同，公共证据总数仍56。
- 原九股范围不变；仅两股覆盖重算，其他七股整条 stock 记录原样复用。九股终态见 `terminal-nine.json`，正常未知与风险未删除。
- 默认研究根目录追加 `evidence-supplements/work_877303d371f64862a748-r2.json`，未覆盖旧描述符。消费者仅让同工单且父hash相符的**有效**子 revision 替代旧待审记录；旧记录显示 `superseded_by_source_revision` 便于追溯，不再混作当前待审版本。损坏子记录不会使父记录失效。
- 通过既有 `report-consumers.sh morning/weekly` 两个真实入口生成独立新输出目录，均读到九股新collection hash。只是无发布的消费者验收执行，复用原三段，不冒充新的自然早报/周报投递；未触发evening模型入口。

## 测试与保全

- **201 passed，22.12s**：原190项基础上新增11项，使用 `scripts/test-ta10.sh -q tests/test_ta_article_boundary.py tests/test_ta_evidence_supplement_inbox.py tests/test_ta_evidence.py tests/test_ta_evidence_r2.py tests/test_ta_research.py tests/test_ta_r2.py tests/test_ta_r3.py tests/test_mt13_action_loop.py`。
- 反例直接使用本地归档的两份真实TrendForce材料：预测放facts必拒绝、用于有条件bull_case允许；9月14日推荐文字不能通过文章身份验证；推荐段数值不能抽入该文章；错误边界/hash拒绝整页回退；原文绝对offset逐字核对。
- `python3 reports/ta10-closeout-r2-20260916/verify.py`：56条raw/text hash对、三条改类型/边界、53条其他证据不变、两个真实消费者hash匹配全部通过。
- **8958个受保护文件无变化**，包含旧交付归档、原模型/宁德六角色、scope及消费者所用原账本。宁德manifest仍为 `6b5488fc26726ca669190e3a7e257740991ce1ca9be5605ca784feac924bc85c`。
- 新网络采集 **0**、新模型调用 **0**、付费采购 **0**；不改cron/群/时刻、持仓/自选/参数，不动并发工作区原有修改。

## 正常研究未知与范围外事项（不是开发未完成）

八股整体仍blocked、经营命题unknown；宁德采购量化unknown。完整商业研报/部分线索真实性、客户供应关系与具体采购份额、同口径经营因果/AI ROI等，继续按上轮九股终态和本轮 `terminal-nine.json` 记录。行业预测降级并不改变这些风险，也不把研究刷绿。

自然投递、未成熟收益验证不在本单范围；原 topic 独立验收仍由原请求方执行，不在此自签通过。两项必需来源/工程缺陷均已修并测试，因此本次 `delivery.incomplete=[]`；这不表示研究问题全部已知或已经独立验收。
