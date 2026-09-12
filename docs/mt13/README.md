> **revision3时窗更新**：投资域已选择observed-quote-v1。部署以 [REVISION3.md](REVISION3.md) 为准；下文09:24+1200秒通用示例已废弃，不覆盖HK延迟报价。

# MT-1.3 明确行动信号闭环

基线 `a17635a`；实施 handoff：`investment/reference/technical-research/MT-1.3-action-signal-loop.md`。
批准的是 **experimental_signal_use_not_validated**。公司研究、技术信号、真实交易三者独立。
不重新证明收益，不等待统计显著性，不保证每天交易，不改真实持仓/自选/scope/生产计划。

## 唯一入口

在 market-tools 根目录运行（Python 标准库；测试使用 homespace 的 pytest 环境）：

```bash
python3 -m mt1.action_loop run \
  --scope /home/emox/work/investment/reference/tracking-scope.json \
  --root .cron_state/mt13/action \
  --out /tmp/mt13-<unique-run-id>
```

一个命令：当前9只scope只读采集 → CN OHLC/复权/交易日历 → HK原始日线及绝对供应商因子补查
→ 明确逐只动作 → 信号登记 → 隔离执行/持有/退出 → 不可变日报第三段及回执。
stdout 的 `manifest` 指向归档，`materials/daily-third-section.md` 是实际生成的报告。
**非空旧 MT1/MT12 root 拒绝混写**，epoch 和合成/真实不能混根目录。

预计数分钟的采集由独立 transient unit 承载，不能裸后台：

```bash
systemd-run --user --unit=mt13-<unique-run-id> \
  --property=RuntimeMaxSec=1200 \
  --setenv=TUSHARE_TOKEN --setenv=HTTP_PROXY --setenv=HTTPS_PROXY \
  --working-directory=/home/emox/work/projects/market-tools \
  /usr/bin/python3 -m mt1.action_loop run \
  --root .cron_state/mt13/action --out /tmp/mt13-<unique-run-id>
```

`--setenv=NAME` 只继承调用者环境，不把密钥值写入命令或日志。首次采集失败证据保留，不能伪装网络成功。
`collect` 只采集；`observe --bundle FILE --root ROOT` 离线评估/收割已采集文件。
真实新输入必须距离调用时间不超过30分钟；已有相同事务重试直接回原manifest，不能用旧数据重新开单。

## 冻结与状态

`signal-policy-v1.json` 原样包含现有 MT12 数值参数；`mt1/timing.py` 未修改。
V1 刚开始观察时不回放历史入场：先冻结 setup，再等后续已完成收盘确认。

|动作|含义|
|---|---|
|BUY|冻结 breakout / pullback 满足且无风险冲突；已持仓显示“增持观察”，不是首次建仓|
|SELL|真实scope持仓或隔离模拟仓存在且触发退出风险；独立于公司研究与入场资格|
|HOLD|存在持仓且所有所需风险判断可完成，无退出|
|WAIT|未持仓且入场未满足；或风险禁止新买入（同时取消未执行BUY），不卖空|
|DATA_BLOCKED|影响当前动作的硬字段缺失，逐项列field/reason/remedy_status|

置信度、策略有效性、行业/宽基RS只注释不拦截。价格身份/已完成时间/日历/复权/异常OHLC仍硬门禁。
完整指标不可算时，独立 frozen exit-only 路径检查已冻结结构/ATR线：缺volume/入场历史不阻挡确定的退出。
没有确定的线下破位时，不能以此简化路径冒称 HOLD；价格/因子修订也不能通过此路径绕开。

## 信号、模拟、复盘三层状态

- `signal_id = sha256(epoch, version, code, side, episode)`；发出即永久保留原理由/触发时点/价口径/hash。
- `execution_status = pending / blocked / filled / cancelled / expired` 独立演进。
- 同episode风险持续只保留一张SELL；expired执行attempt可在有虚拟仓和近期风险复核时审计续期，不要求换版本。人工取消不自动恢复，暂停需显式resume。详见REVISION2.md。
- 未持仓风险在后续已完成收盘全部解除后，开始新的前向setup观察；不沿用旧触发回填BUY。真实持仓风险episode不因此消失。
- 未成交订单有效期3个**已验证已完成交易日**。风险优先取消BUY，执行已发生的较早开盘不能被晚收盘倒销。
- 所有初始真实持仓仅作观察；**不建立虚拟仓、不捏造真实成本/买入日期**。
  真实持仓SELL可明确发出，但无模拟仓时执行标记 `no_virtual_position_real_holding_not_seeded`。
- 只有策略BUY在后续合法开盘模拟成交才创建一个归一化虚拟单位；每版本/标的仅一个模拟lot，不给实盘数量。
  SELL关闭该lot。净收益是因子调整价格、含冻结费用场景，不是用户个人盈亏或可实现组合总回报。
- 执行终态不等于复盘终态：20/40/60个后续收盘继续积累，退出/取消后也继续观察。
  longitudinal 的事件直到执行终态且60日价格观察成熟才 `archived`；此前跨周pending。

## 执行采集与模型（revision 2已实现）

完整实现、真实接口实测、有限worker命令与故障恢复见 [REVISION2.md](REVISION2.md)。
`execution-worker --mode watch` 自动预取资格、轮询原响应、生成可重算bundle、消费入隔离账本。
`consume-execution` 可独立复验；真实输入不接受手工canonical JSON盖hash。
strict-open-v1的60秒/盘前时刻门禁保留；HK无法证明的venue状态不伪造。
另有明确隔离的observed-quote-v1估值模拟模型，不冒称可成交证明、不回填open，不在既有root偷偷切换。
首个合法开盘自然验证仍待运行；这与adapter实现完成是两件事。

## 日报/周报与接线补丁

```bash
python3 -m mt1.action_loop compose \
  --section-one <原第一段.md> --section-two <原第二段.md> \
  --company-section <原第三段公司研究.md> --manifest <动作manifest.json> \
  --out <完整日报.md>
python3 -m mt1.action_loop weekly --root <同一root> \
  --asof 2026-09-12T23:59:59+08:00 --out <周报.json>
```

compose原样保留三段原文，只在第三段附加独立技术栏目；生成本地日报及source hash回执，不调用云发布。
周报同时输出JSON和`.json.md`：本周BUY/SELL、累计取消/过期、跨周pending原因、虚拟持仓/退出、
净价格收益、路径回撤、假突破、卖飞观察、20/40/60样本与缺口、按版本对比。小样本不称validated。
日线低价/收盘路径是诊断，不能还原日内高低顺序；费用是场景，不是核验的券商费率。

`investment-wiring.patch` 是对当前 `evening_recap_data.sh` 的可应用**未应用**补丁，
`git apply --check` 已核对。开关默认0；1时调用唯一入口并打印 `MT13_RECEIPT_JSON`，原采集失败码不变。
它只提供晚盘数据接线，不冒称所有早报/周报prompt已经修改。
投资topic复验后可复用同一入口接早报与周报，并把原三段交给compose；不得仅显示工程进度。
真实自然触发/发布/归档全链路尚未运行，longitudinal没有expected schedule时诚实保持not_verified。

回退：`MT13_ACTION_ENABLED=0` 或不应用补丁；暂停MT13相关调用，保留账本与归档。
无数据迁移、无真实持仓变更、无需清空历史。**本单没有改VPS/群/时间，也没有发布到真实日报。**

## 每周版本实验

```bash
python3 -m mt1.action_loop register-policy --root <root> --policy <新版本.json>
python3 -m mt1.action_loop run --root <同一root> --out <新采集目录> --policy <新版本.json>
```

新版本必须有新version、parent、改动reason、冻结timing参数、晚于现有观察的effective_at，
approval仍为experimental。不能修改已注册版本；不能回填旧日；同输入可让v1/v2独立向前观察。
每周效果不好可以提交实验，不自动追涨杀跌改参，不替换v1。旧MT12规则逐卡并列，
没有旧规则模拟成交序列时不编造对照收益。

## 复验

```bash
/home/emox/work/homespace/.venv/bin/python -m pytest -q
python3 -m mt1.action_loop demo --out /tmp/mt13-synthetic-<new-id>
```

合成样例明确贯通 WAIT→BUY→HOLD→SELL→WAIT，两个下一开盘成交回执以及一个已退出模拟lot。
它验证机制，不证明收益；真实当前9卡本轮均HOLD，不保证每天有交易。
