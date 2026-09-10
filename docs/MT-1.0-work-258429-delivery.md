# MT-1.0 派修交付：工程复核与补修，等待独立验收

工单 `work_2584297bb8bd6304e99f`，来源 investment #3055，2026-09-11。

## 版本与复现

接单 HEAD 已为 `a3442373a4f574738570e772268056a87d63d14d`，四个原始阻塞已有修复，未重复开发。
把 `f93d3fd` 通过 git archive 解包到临时目录，原始测试为 **48 passed**。
另在临时 package 中加载其 plans.py/store.py，复现：财务 fail 的 final EXIT 被拒；
字符串证据的 pending EXIT 被接受；无 method_id/version 的 BUY 有资格；2020 年到期 BUY 有资格。
当前四项修复对应 `tests/test_mt1_acceptance_fixes.py`，全套 69 项先通过。

进一步先写回归，在 a344237 复现 4 个失败：
- review_due 更新时 extension_evidence 只验非空，字符串、字符串列表、未来证据均放行。
- 晚盘 finalize 硬编码 morning 门禁，CN 收盘前也可写新增 BUY。

本次改为结构化来源/日期/claim 校验，以及 evening 使用 evening 门禁；
周末研究仍可执行，但新增 BUY 不因研究休市豁免而放行。新增 22 项回归，最终 **91 passed**。
测试命令：`/home/emox/work/homespace/.venv/bin/python -m pytest tests -q`。
新增文件 `tests/test_mt1_work_delivery.py`；包含四时段门禁、真实周末拒绝新增 BUY、
SELL/EXIT × final/pending × 空claim/坏日期/缺来源矩阵。测试产物全部在临时目录。

## 当前推荐逻辑（供原 topic 向用户解释与复验）

1. **候选发现**：现有 sector_picks 三通道候选保留；全市场质量价值独立扫描，不依赖 HOT。
   原始机器 BUY 不是最终推荐，新质量价值因子仍 shadow。
2. **研究审查**：VALUE 验证未来 1—3 个月价值修复路径；TREND 验证业绩/订单/景气；
   REVERSAL 验证经营或供需改善，深跌本身不构成准入。URL/date/claim 的结构校验不能证明来源真实，需研究员核验原文。
3. **新增 BUY**：必须 final、有效证据与审查人、五项财务/流动性/重大事件/技术/估值 pass，
   并具备价格条件、风险边界、失效条件、近期节点与复核日；绑定注册表实际 active 的 method_id/version。
   方法撤回或版本不符即无新增自选资格，旧事件保留。旧生产方法显式 hash 登记，不冒充新因子已回测。
4. **持有和退出**：未持有 BUY/WATCH 与已有持仓 WATCH/HOLD/SELL/EXIT 分开。
   HOLD 看持有逻辑，不要求新买入估值继续通过；SELL/EXIT 凭有效证伪/风险/兑现/到期复核证据，
   不被财务恶化等买入门禁反向阻挡。所有新退出都校验来源、日期、claim、审查人和退出依据。
   历史关闭仅迁移入口可豁免，不能给新事件套迁移豁免。到期不是自动卖出。
5. **期限与溯源**：original_date/original_deadline/参考价/原始理由不可覆盖；HOLD 不重置 episode。
   原始或有效延长期限到期则重论证，不能继续新增 BUY；未知迁移期限需重建事实，不能无限放行。
   投资期限延期绑定前一账本版本、有效证据、审查人及新期限；复核日更新也必须结构化新证据。
6. **运行节奏**：早盘复用上一已完成交易日，晚盘核验当日已收盘；CN/HK/US 独立日历，缺失 fail-closed。
   周六复盘计划与退出，周日联网研究并安排下周验证；没有新证据不每日凑推荐或规则升级。
7. **记录与自选**：append-only 事件、CAS、请求幂等；finalize 输出差异与覆盖，未审查项不算完成。
   自选只消费合格最终 BUY，当前仅 dry-run；不执行交易，不将推荐参考价冒充用户真实成本。
8. **后验证与迭代**：daily-rec-verify 的旧命令已通过代码兼容调用新账本 verify，再输出旧统计，
   不把两套样本混算。新路径保留已退出 episode 的首次 final BUY 基线，仅未复权报价观测，非实盘收益。
   新因子保持 candidate/shadow，取得精确通道/PIT/20、40、60日/独立样本及滚动样本外证据后才可升级。

完整计划字段与明确标为虚构的 WATCH 示例见 `docs/MT-1.0.md`「输入契约」。

## 分层现场证据

- **线上配置，只读**：四个核心 cron 均含 mt1_job 和 finalize；daily-rec-verify 仍调用 rec_log.py verify，
  但当前代码先运行新账本验证。具体 domain/cwd/delivery/silent/model/cron/hash 见配套 JSON。
  未改任何配置，未新增 cron，不凭配置字符串判运行成功。
- **分批真实取数**：复制现有游标到隔离缓存，在独立 systemd transient unit、RuntimeMaxSec=300 内运行两批各 1 只。
  依次 000523.SZ、000524.SZ；累计尝试 105→106/5561，errors=[]，exit 0。仅证明游标推进与真实接口。
  隔离目录当前快照覆盖 1→2，不能冒充生产当日完整覆盖 106。失败重试、跨日、断点由既有单测验证。
- **账本真实输入**：生产 DB 只读一致性备份，64 计划、0 final、1 旧生产 active 方法基线。
  新验证在副本返回 verified=0、unknown=64、errors=[]；没有最终 BUY 基线就不编造收益。
- **四时段**：本地 SQLite→方法资格→finalize→Markdown→自选 dry-run 回归已有 4/4，
  本轮加验实际日历门禁（部分测试隔离 plan reducer，其他测试覆盖完整资格）。这些不是飞书送达测试。
- **安全范围**：未写生产计划或旧rec，未发送合成标的报告，未真实自选写入、未执行交易，未修改其他仓库。
  三个既有无关 untracked 文件保留。未 push，未部署其他 worker。

## 未完成与补齐路径

|缺口|补齐路径|
|---|---|
|64 个迁移计划冷启动证据研究|原投资 topic 核验公司公告、原推荐时间/价格/期限；不能重建者保持 pending，不为验收造 BUY|
|全市场当日完整覆盖|继续有界批次，核对 attempted 与 current-date observed、完整财务数据覆盖，缺失失败重试；104/5561 的旧采样不是全量|
|四时段真实端到端|逐次保留实际调度、manifest/report、研究来源与 review bundle、finalize result、飞书 message/doc 回执并独立核对；不得拿本地合成报告发真实用户|
|精确三通道 20/40/60 历史回测|先补 point-in-time 财务/行业、历史成分及退市、归档/重算真实通道、复权/可成交/费用/基准数据，再按滚动窗口样本外及退出vs持有同入场比较；数据不足维持 incomplete/unsupported，不用代理收益替代|
|其他 worker|明确授权部署同一 commit 后检查运行路径；磁盘 HEAD 不等于进程已加载|
|自选真写入回执|待真实最终合格计划与相应授权后小范围验证；当前 dry-run 不能证明真实成功|

这是开发交付，不自行标验收通过。原 topic 应按以上分层证据独立复验。
