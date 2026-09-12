# MT13 revision 2：执行采集、SELL恢复、三类消费

本轮针对拒收 P0/P1/P2，信号条件及收益研究不重做。MT12 数值参数、已有516测试、全部A/H/MT13证据保留。

## P0 实际采集 worker

唯一入口仍是 `python3 -m mt1.action_loop`。新增：

```bash
# 非交易日可实测实际接口，不写账本、不创造信号
python3 -m mt1.action_loop execution-worker \
  --root <已有隔离root> --out <新的采集目录> \
  --mode probe --max-seconds 75 --poll-seconds 2

# 有限时自动：预取资格 → 批量轮询 → 原响应归档 → 自动消费入隔离账本
systemd-run --user --unit=mt13-exec-<唯一ID> \
  --property=RuntimeMaxSec=1230 \
  --setenv=TUSHARE_TOKEN --setenv=HTTP_PROXY --setenv=HTTPS_PROXY \
  --working-directory=/home/emox/work/projects/market-tools \
  /usr/bin/python3 -m mt1.action_loop execution-worker \
  --root <独立验收后的隔离root> --out <唯一采集目录> \
  --mode watch --max-seconds 1200 --poll-seconds 3
```

建议由投资topic批准后在北京时间09:24左右启动有限worker。**这里只提供接线，未安装timer/cron/service**。
`max-seconds` 1–1800；HTTP超时最多8秒；4线程预取；随后每轮只请求一次腾讯批量报价。
单次网络极端慢读的最终硬界由独立 systemd cgroup 的 RuntimeMaxSec 保证，禁止裸后台。
`once` 只采一轮并自动消费；`probe` 采最多三轮、从不改账本。

原响应：
- Tushare `trade_cal` / `hk_tradecal`：本次交易日、后续日历。
- CN：`suspend_d(suspend_type=S, trade_date=当日)`、`stk_limit`、包含信号日及当日的 `adj_factor`。
- HK：新浪绝对qfq因子序列，验证信号日anchor没有被改写。
- 腾讯批量 `qt.gtimg.cn`：保留GBK原字节、股票身份、报价时间、open/last/volume；CN涨跌停价另与Tushare交叉检查。

每次HTTP写 `.raw` 与 `.meta.json`，包含真实请求开始/响应收齐时间、耗时、URL/无密钥参数、HTTP状态、hash。
不保存含token的请求体，不把CLI重排CSV冒充HTTP原响应。HTTP错误body也保留；无响应是0字节＋明确transport_error。
**原响应hash与capture ID不同**：相同body在两个时刻也有两个source_id，不混掉时间证据。

adapter生成的行含 `refs`/field_sources。消费时重新读取原响应并重算所有字段；修改封装JSON里的price/flag会拒绝。
实时入口拒绝revision1手工canonical JSON，只保留其合成测试兼容路径。
`consume-execution --bundle FILE --root ROOT` 可独立复验同一原响应链；同capture重试回原manifest。
消费只更新既有信号/attempt/模拟仓，不发现候选、不新增BUY/SELL，不从真实scope偷偷建虚拟仓。

### 两种执行模型，禁止混淆

|模型|价格/时间|资格/限制|
|---|---|---|
|strict-open-v1|下一session的open；实际响应收齐不得晚于09:31:00|预取证据必须在09:15–09:30真实取得；CN停牌清单/限价/因子/日历；本地虚拟库存CN T+1。HK无可靠盘前venue状态字段时硬阻断|
|observed-quote-v1|实际收到新增成交报价时的last价格；时间是HTTP响应收齐时刻，绝不回填open|连续两次同session provider时间及累计volume都增加；CN报价年龄≤20秒、HK允许≤1200秒的延迟报价；CN停牌/限价硬门禁仍在，HK是成交打印估值代理，不宣称halt/VCM clear或可成交|

`observed-quote-v1` 是另一个明确冻结的**模拟估值假设**，不是偷偷放宽strict-open-v1。
配置见 `execution-models-v1.json`。需要选择这个模型时：

```bash
python3 -m mt1.action_loop run --root <全新observed隔离root> \
  --out <全新数据目录> --execution-model observed-quote-v1
```

已有root不能换模型；模型合同hash固定。signal-policy-v1参数不变，但不同执行模型账本/收益不混账。
仅想只读探测另一模型可用 `execution-worker --mode probe --probe-model observed-quote-v1`，非probe拒绝覆盖模型。
HK原始报价没有足够的halt/VCM清除字段，不能凭交易日或成交量捏造盘前资格。strict HK继续诚实blocked；
替代模型只用于明确标注假设的前向模拟。首次合法实时交易窗口还未到，不能声称已经验证自然成交。

### 60秒窗口可行性与运行责任

本机真实有限worker首测：总8.91秒；三次批量HTTP为0.55/0.57/0.98秒。Tushare/Sina预取在开盘前完成，
不把采120根日线/公司研究/日报生成塞进60秒窗口。精确60秒/超1秒、晚预取、旧provider时间等用合成时钟测试。
这证明本机接口与预取/轮询结构可运行，不证明下个开盘网络延迟、供应商行情更新频率一定合格。

今日2026-09-12实际日历确认A/H休市，下一交易日2026-09-14。上个已完成日接口另作实测：
Tushare当日限价5641行、停牌12行，6只CN因子均有数据；抓取时间仍保留9月12日，**不把它们冒充9月11日盘前证据**。
原响应及latency数据位于 `reports/mt13-r2-20260912/`。

### 失败与恢复

|情况|输出与恢复|
|---|---|
|休市|market_closed_next_verified_session，附日历下一session；watch立即结束|
|错过严格窗口/盘前证据太晚|strict_window_missed / strict_preopen_eligibility_not_captured；不补写时间，下一session重新启动|
|HTTP429、权限/额度、超时|保留原响应/明确错误，订单blocked而非“低置信”；下一次有限worker重新请求；不能用unknown填true|
|报价无进展或HK延迟过大|本轮不成交，下一轮新快照重试；到硬时限退出，不无限进程|
|第三个已完成session仍未执行|attempt过期；BUY不无限重试；持续SELL按P1恢复，不要求更换策略版本|
|人工暂停/取消|自动worker不能恢复；暂停需显式resume，取消永不自动恢复|

权限未取得是外部数据状态；网络/供应商时钟未满足是数据状态；不是adapter未实现。
首个合法开盘自然运行是待验证项，与周末真实接口已验证严格区分。

## P1 同signal的执行attempt恢复

冻结策略见 `execution-recovery-v1.json`：3个已完成session一轮；SELL仍有虚拟仓、最近4个自然日内有明确风险复核、
且没有人工取消/暂停，才给原signal追加下一attempt。原signal_id、原理由、原触发时点不改。
每次attempt有number/opened_at/after_session/recovery_policy/status/ended_at/确认hash，历史过期attempt不覆盖。
恢复不能用晚收盘重新确认去补早开盘成交。开盘前worker可依据已留存的近期复核续期；过期后日评也可续期。
动作特定exit-only路径的确定风险复核可以续期，不因缺volume/入场历史卡住SELL。
真实scope持仓没有虚拟lot时不续出虚拟卖单；未持有不卖空。

```bash
python3 -m mt1.action_loop pause-signal --root ROOT --signal-id ID --reason '暂停原因'
python3 -m mt1.action_loop resume-signal --root ROOT --signal-id ID --reason '恢复原因'
python3 -m mt1.action_loop cancel --root ROOT --signal-id ID --reason '永久取消本信号执行'
```

resume不允许成交于恢复之前；cancel连expired也可显式取消，不会被下一次自动恢复。
已完成测试：原响应形状的停牌/限流3日→expired→持续风险复核→第二attempt→恢复合格open→仅退出一次；
同capture重跑幂等、人工取消永不复活、暂停明确恢复、晚复核不能早价回填。

## P2 早报、晚报、周报三类消费

`deployment/report-consumers.sh` 可直接运行，但没有被生产引用。三类命令均调用唯一模块入口：

```bash
MT13_LEDGER_ROOT=ROOT bash docs/mt13/deployment/report-consumers.sh morning FIRST.md SECOND.md COMPANY.md OUT_DIR
MT13_LEDGER_ROOT=ROOT bash docs/mt13/deployment/report-consumers.sh evening FIRST.md SECOND.md COMPANY.md OUT_DIR
MT13_LEDGER_ROOT=ROOT bash docs/mt13/deployment/report-consumers.sh weekly 2026-09-14T21:00:00+08:00 OUT_DIR
```

- **日评数据**：原报告流程在自己的时段调用 `run`，更新完成收盘的信号/风险；不必等开盘worker。
- **开盘worker**：独立有限进程，只采执行证据/更新账本；不写报告，不发布，不受日报大模型耗时影响。
- **早/晚消费**：读最新已提交manifest，将技术栏目拼到第三段，保留原第一/第二/公司研究原文。显示执行模型/来源时点。
- **周报消费**：同一root的按版本效果、attempt与跨周未执行原因。不同execution_model必须分root分别报告，不直接混算收益。
- 技术模块错误时生成明确warning，原行情/公司研究仍保留；技术周报失败不挡其他周报。外层30秒硬timeout。
- `deployment/execution-worker.service.in` 仅安装模板，需要投资topic核对root、凭据注入和本地窗口后批准使用。
- 既有晚盘 `investment-wiring.patch` 仍是未应用的opt-in数据钩子；若采用新消费脚本，不重复触发采集。

**未改VPS/群/时间/自选/真实持仓/scope，未部署任何持续调度，未自动发布。**
回退：停用相应调用/禁用MT13开关；保留root全部证据。observed模型不可拿strict root改名顶替。

## 接口规则参考

- Tushare `adj_factor` 更新时段说明：https://tushare.pro/document/2?doc_id=28
- Tushare `suspend_d` 的 S/R 及连续停牌记录说明：https://tushare.pro/document/2?doc_id=214
- HKEX VCM 时段与豁免说明：https://www.hkex.com.hk/Global/Exchange/FAQ/Securities-Market/Trading/VCM?sc_lang=en

接口说明不替代逐次原响应资格证据；HK公开源缺失venue资格仍明确阻断strict模型。
