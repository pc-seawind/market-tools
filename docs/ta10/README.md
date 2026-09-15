# TA-1.0 证据约束研究旁路

来源：TauricResearch/TradingAgents，commit `be952b8eccb49720509af544c6675233bc1f10d0`，Apache-2.0。
吸收研究编排思想，不复制交易系统，不迁入 LangGraph，不替代 MT13/MT14。

## 单入口与边界

```bash
python3 -m mt1.ta_research --report-dir <已有真实日报目录> --company-sources <补采公司原文目录>
```

默认写 `.cron_state/ta10-r4`；`--root` 指定新 revision，旧轮保留。scope 只读；不写持仓、自选、荐股、技术策略参数，不新建 cron。生产消费默认入口与根见代码 DEFAULT_ROOT。
模型采用现有 `ANTHROPIC_HUOSHAN_URL` + `HUOSHAN_API_KEY`，**OpenAI 兼容 /v3/chat/completions**，固定 glm-5.3、temperature=0、reasoning_effort=low、max_tokens=6000。凭证不落盘。供应商 alias 不算固定模型：返回模型不符则拒绝。

长运行用有 RuntimeMaxSec 的 systemd-run；父进程工作目录独立，三个股票并发、各股六次顺序调用，每次网络最多两次，连接/读超时明确。重试回执先留存，同 root 已完成请求读回原结果，不再次付费；输入或模型变更必须新 revision。被杀死的在途请求可能已产生不可知费用，不能声称 exactly-once 远端执行。

## 真实 A/B/C

- A：已有晚报 company.md 原字节、hash、逐股原文行，是真实基线，不是重新调用的 mock。A 的证据截止、模型及成本无法与新输入统一，**A/B/C 非严格因果实验**。
- B：一轮模型生成完整结构化判断。
- C：bull / bear 各自初判，互不见对方；bull_cross / bear_cross 各读两份初判质询；C 看完整原证据和四份对话裁决。
- B 与 C 每个请求保存相同 frozen_input_hash，不能在辩论中补新闻污染对照。quote / financial / company_primary / hypothesis 分型；旧 thesis 不自动变事实。
- 每角色 request、完整 provider response（包括实际 reasoning）、provider id、token、耗时、尝试/错误、parsed output 均保留。原输出不可改；`ta_audit.audit()`从请求/响应重新校验。

## 数据与门禁

复用报价原始解析器及三地日历模块。九股只有 CN/HK，自选/代码映射覆盖沪/深/港；US 日历作为原日报背景只读保留，本轮不声称有美股研究样本。
精确报价保留 currency/share、CNY/HKD、unadjusted_spot、provider_at、fetched_at、expected_date。盘后供应商快照不证明可成交。
财务复用 Tushare 现有入口，保存实际 CLI CSV 字节（不是 HTTP body）；公告日只有日期时使用日终上界，不发明秒级原发布时间。ROE 标为报告期百分比，不年化。当前供应商财务版本不能认证历史 PIT。
港股补采原始 PDF 与 pdftotext，选前两页作为模型输入，全文留档；元信息必须可在正文核对公司身份及公告日。这里只覆盖最新报告重点，不声称已完成全量公告搜索。
引用不存在、未来材料、陈旧报价、未知币种/复权、精确数值不匹配、非法概率/目标价拒绝。精确值允许有限十进制字符串与数值等价，不把序列化类型差异当财务错误。

**门禁不等于全部语义认证。** 字段引用正确不能证明“经营必然改善”等推理正确；中文数字、定性强弱、公司因果仍需人工复核。事实栏目引用假设被保守拒绝，即使原句说“假设未核”，会列为 schema blocked，不应把所有 blocked 都称为模型捏造。

## 归档与实际消费者

1. 全部单股结果先写不可变 run/manifest，再调用现有 `longitudinal.archive(job=ta10-research)`增量登记九个 company_research 事件；输入、原判断、原证据全文也复制入该已有账本。
2. 完成共享归档后才切研究 latest 指针；该指针**不是技术策略 active**。
3. 现有 `mt1.action_report.compose`（晨/晚报真实路径）原样保留原三部分和技术段，在后面追加公司研究表，并将 run_id/hash/逐股覆盖放入真实 receipt。
4. 现有 `publish_weekly`追加研究表及独立 research receipt；长期 pending 在现有 weekly_index 持续可见。不更改定时器/发送群。
5. 缺包/损坏/跨 epoch/未来/过期包降级为可见缺口，基础日报不被阻断。当前判断的三日新鲜度与长期事件队列分开：旧判断不冒充当前，旧 pending 不删除。
6. 本轮 report-cycle 实际本地消费不等于后续自然线上发送验收，投递状态保持 not_published。

## 分层复盘

`ta_review.evaluate`分别返回：交易时段观察、20/40/60交易日价格观察、经营命题新证据检查、市场/行业/风格分解缺口、五类错误 unknown。事件命题确证/证伪需要预测后新增的非价格材料、归属 reviewer 和依据，涨跌不能作为经营确证输入。
首轮价格窗口未成熟，收益为 null；未有因子模型时贡献率 null，剩余收益不命名为公司事件因果贡献；不算用户个人盈亏，也不进入正式荐股成功率。
目前还没有全自动事后经营语义裁决或因子归因模型，必须如实保留 blocked，不自动生成反思后修改规则。

## 验收

- 新增 tests/test_ta_research.py 为 SYNTHETIC_ONLY 正反例，绝不加入真实九股统计。
- 实际原日报消费、共享周报 index、hash 保护和九股 A/B/C 审核见 reports/ta10-20260915 与交付文档。
- 独立验收以工单合同为准；开发者的协议 pass 不是公司签审，也不是投资有效性验证。

## 日期与原始失败回执修订

短期目标日期的权威来源是冻结交易所日历，不是 LLM。模型 `target_date=null` 表示不自行报日期，允许由外层审计 envelope 的 `target_session`提供已核日期；模型若写出其他非空日期则仍拒绝。这不是把模型的错误日期改正确。旧 r3/r4 的早期门禁曾将 null 错当冲突，原错误回执保留，消费采用从原请求/响应重新计算的 audit 结果。数字类型等价的误判同理修正。审计不忽略真实错误引用、不匹配的金额、币种或伪精确值。

## 报价基准补救

人工首轮发现“相对开盘高”被模型写成“当日上涨”。新增 research_quote 包装器单独提供previous_close和两个比较方向，正反例回归；不修改现有MT13解析/动作。原r4快照不追灌新字段，旧模型输出不改写，新字段的模型效果未以旧轮冒充验证。
