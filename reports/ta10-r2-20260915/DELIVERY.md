# TA-1.0 R2 返修交付（2026-09-15）

**开发自检：九股协议检查与逐股原文语义复核均通过，实际日报/周报消费者已读取九股。最终独立验收仍由原 topic 执行。** 本轮不是 20/40/60 交易日收益验收，也没有验证自然线上投递。

## 九股逐一结果

|标的|本轮协议/源文复核|已修正或明确的研究边界|下一验证点|
|---|---|---|---|
|德明利 001309.SZ|pass / pass|补全 bear schema；previous_close 与开盘比较分离；假设不写成事实；ROE口径差异不强行对齐|短期依据不足保持未知；中期营收、库存、回款与现金流|
|兆易创新 603986.SH|pass / pass|补公司原文，保留存储周期与扣非利润命题|短期未知；后续同口径业绩和存储经营验证|
|腾景科技 688195.SH|pass / pass|补齐证据声明字段与公司原文；不编造客户/产业卡位|短期未知；光通信收入、产能与现金转化|
|德福科技 301511.SZ|pass / pass|补公司原文，经营/融资现金流和杠杆分别解释|短期未知；加工费、营运资金及杠杆|
|宁德时代 300750.SZ|pass / pass|价格不能反驳估值；补激励股份上市公告|9月16日股份上市仅作条件观察，不能证明涨跌；中期储能毛利率与现金流|
|工业富联 601138.SH|pass / pass|补公司原文，AI服务器增长与营运资金分别核查|短期未知；后续收入、现金与库存|
|小米 01810.HK|pass / pass|单日成交量不能推断放量；利润当前为正但同比下降，不写成持续增长|短期未知；中期成本与利润恢复|
|美团 03690.HK|pass / pass|短期理财不译作国债；季度利润与半年现金流不直接做同口径背离；季度利润触发移中期|短期未知；同期间分部盈利、补贴及现金流|
|腾讯 00700.HK|pass / pass|承认原文已覆盖部分分部收入；未确定净现金比较期不猜；算力预付款与资本开支区别处理|短期未知；中期 AI 投入、自由现金流及分部利润|

上述 pass 是可定位的协议与开发侧原文复核结果，不是收益有效性或原请求方验收结论。完整事实/推断/反证、多空交叉质询、短中期命题、缺口及逐股证据 ID 位于最终 manifest 和质量记录。缺证据不展示方向；季度变量不能成为下一交易日失效条件。

## 不可变 revision 与真实调用

最终 run_id：`ta10-revision-d3947a666b4113873caa5ed1`

manifest SHA256：`b978de19609345994e5ad20504ddcdbd572c4b946e4a1cd21f252217b2acb2dd`

manifest：`.cron_state/ta10-production/revisions/analyst-r2/runs/ta10-revision-d3947a666b4113873caa5ed1/manifest.json`

语义复核：`final-semantic-quality.json`，SHA256 `2fb14e838ecb6455b5fdf16340858e4883cee3528378b2a7e0a659e746293929`。记录绑定 run/input/manifest/逐股 result hashes，消费者实际读取，不是仅在交付里披露已知错句。

- R1 r4 保留，未覆盖。R2 r5 使用新 previous_close 及新增九股原文做全量真实调用，失败保留。
- r6、r7 均经既有 evening report-cycle 实际启动新 revision，分别54与55次调用；r7仍有德明利schema问题及源文语义问题。
- 最终是 r7 的**同一冻结输入九股子 revision**。48个角色原始响应逐字节复用，6个角色真实重调，另1次真实有界纠错。并不把复用称作新调用。DML bear/C、小米 B、美团 C、腾讯 B/C 的旧响应保存在 parent-records。
- 全部 R2 实际消耗去重：**170 个 provider responses，1,397,850 input tokens / 207,142 output tokens**；调用耗时累计3343.080秒，物理首末跨度1271.297秒。包含失败JSON和纠错调用，不把复制件重复收费。模型 glm-5.3；美元费用缺账单未知。
- 最终活跃 cohort 的审计55响应/463194输入/64129输出含复用，不得与170再相加。`all-model-costs-r2.json` 为全部物理请求成本口径。
- A 为真实原日报基线，但模型、证据截止和内容结构与 B/C 不同，**不严格可比**。不声称 C 胜出、胜率改善或可归因收益提升。

## 原文与真实消费者

`evidence-index.json` 给九股原始URL、发布时间精度、as_of、原文/文本hash及截取页码或字符范围。六A股补关键公司原文，港股不再只取前两页；腾景采用公司报告的新浪镜像，标明不是交易所原始URL。公告观察时间与精确发布时间区别记录。保留全部原始PDF/HTML/text，原始失败不删除。

实际消费者回执：
- `final-consumers/daily.md.receipt.json`
- `final-consumers/weekly.json.research-receipt.json`
- `consumer-readback.json`、`base-preservation.json`

消费者读到上述 run_id/hash、9股覆盖及语义检查；原三段前缀与 MT13 manifest 保持不变。`production-entry/`、`production-entry-repair/`、`production-shell-receipt/` 证明既有生产脚本真实启动/幂等复用，并非只改prompt。隔离负例 `SYNTHETIC-negative-consumer/receipt.json` 证明已知错误股方向被隐藏；`SYNTHETIC-corrupt-consumer/receipt.json` 证明质量hash损坏不会阻断基础日报。负例仅测故障隔离，不算真实九股样本。

## 持续接续，而非三日后固定旧包

既有 evening consumer → request_refresh → 有硬超时的 transient systemd service → 按日报输入/scope/代码/catalog内容生成新key → 新根重新freeze → 真实B/C → 归档/到期队列。重复key不重复调用；新的日报产生新revision，不复用昨日input。未复核revision不展示方向。无新增cron，无改群或发送时刻；当前晚报不等待异步研究，后续既有报告读取已完成版本。

`final-due-queue-receipt.json` 包含36个独立原run/股观察项：R1 seed及三份生产revision各9，不混成样本。下一交易日观察、经营命题及错误分类分开；20/40/60只在完整交易日历和实际价格齐全后计算未复权价格观察。到期缺报价或日历即blocked，不插值、不依据涨跌编因果。

接续实现/操作入口见 `docs/ta10/R2-OPERATIONS.md`。新公司原文仍需研究取证更新catalog；已建供应商数据自动取新vintage，但**没有冒充完成全市场公告自动发现或语义自动签审**。原文过旧、质量未复核只降级研究层，不阻塞原日报。

## 测试与保护

可复现命令：`scripts/test-ta10.sh tests/test_ta_research.py tests/test_ta_r2.py tests/test_mt13_action_loop.py -q`。

环境：`/home/emox/work/homespace/.venv/bin/python` + 显式 `PYTHONPATH=/usr/lib/python3/dist-packages`，pytest9.0.3 / requests2.31.0 / PyYAML6.0.1。不修改系统或homespace虚拟环境。系统python无pytest、裸venv无requests的原问题已在入口显式处理。

- 全集阶段快照：713 passed /1328.72秒（含两个长成熟核验测试，日志保留）。
- 后续最终发布回归：718 passed /225.50秒，仅排除上述未改动的两个长测试。
- 最后针对质量损坏状态复位新增1例后：目标集 **115 passed /20.55秒**（`final-targeted-v6.txt`）；最终代码全量快速回归 **719 passed /229.04秒**（`delivery-regression.txt`，仅排除上述未改动的两个长测试）。不把早先全集谎称最后一字节全集。
- 新增跨期限、数值、成交量、语义复核hash、真实有界纠错协议、幂等、新revision、故障隔离、交易日历/成熟收益正反例。
- `protection-final.json`：R2初始4662个已跟踪文件核对，原有脏ledger未变；scope、持仓、thesis、自选与生产技术参数未改。R1保护集156文件中仅授权消费者代码变化。R1起点动态脏文件历史hash没有记录，不补造。

## 明确未完成/自然 pending

1. 原 topic 独立R2验收待执行。
2. 自然线上日报/周报投递尚未发生，只有实际消费者执行回执；次次自然生产稳定性待观察。
3. 9月16日短期观察及20/40/60交易日结果未成熟；个人持仓成本数量未知，无法算个人收益。
4. A不严格可比，未做收益归因或统计显著性验证。
5. 新公司原文持续发现/逐股语义签审仍由研究取证接续；现有自动链路负责新revision、供应商数据、门禁和到期队列，不冒充全自动研究审批。

本轮不推送git remote、不改投递配置。提交号见同目录 commits.json 及结构化 dev_work 交付。
