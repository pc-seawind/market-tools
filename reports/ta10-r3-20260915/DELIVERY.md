# TA-1.0 R3 返修交付

工单 `work_854551e568a8903434de`；R3只处理恢复、研究接续和依赖约束，不重复已经合格的九股模型跑数。本文件为开发交付，原topic独立验收尚待进行。

## 结论

- 启动失败不再永久缓存：区分已完成、仍运行、未知监督状态、启动失败/worker退出；60/300/900秒有界退避，默认最多3次；显式恢复理由可开启下一组最多3次。所有失败历史保留，worker进程锁进一步阻止重复执行。
- **真实验证了本机systemd启动失败→第二次启动、worker失败→修复后恢复、硬超时→恢复完成、已完成请求不重复启动。** 启动失败通过隔离进程的无效本机bus地址触发；硬超时使用无网络sleep替身，由真实systemd RuntimeMaxSec杀停。二者不是新的金融样本。
- 真实恢复演练复用R2 r7完整不可变checkpoint，原model response hash均一致，**新增模型调用0、输入/输出tokens 0**。不把复制的r7工程checkpoint冒充新的合格九股批次，生产latest仍指向已合格最终b978de…manifest。
- 研究接续已进入**现有报告脚本与实际部署REPORT-CONTRACT**：每日/周末生成待审与到期inbox，当前investment agent负责原文补采/catalog更新、逐源签审、短期/经营命题/错误分类分开写回。批次有owner/reviewer/next_check_at、证据与身份门禁；失败不阻断原三段。
- 实际经现有report-consumers.sh回写4个run质量记录+36个原run/股票跟踪记录；再次经现有weekly入口执行同批回写，全部幂等。due_queue真实读取36份proposition_checks与短期判定，不再固定传空列表。现时判断均为未成熟/缺后续证据，下一检查时间为 **2026-09-16 21:00 北京时间**，不是提前判命中。
- 实质初判变化自动使两个cross及C失效重调；cross读取当前初判，不沿用旧payload。audit逐依赖比较实际输出hash。唯一schema等价规则是claim缺numbers字段与numbers=[]等价，任何论点/引用/数值改变不等价。德明利本次旧/新bear恰属schema等价，九股原始结果不改。

## 九股保留与重验

最终run：`ta10-revision-d3947a666b4113873caa5ed1`。
manifest SHA256：`b978de19609345994e5ad20504ddcdbd572c4b946e4a1cd21f252217b2acb2dd`，R3未改变。

|标的|新增依赖/协议审计|现有消费者|
|---|---|---|
|德明利 001309.SZ|pass；缺numbers=[]等价被明确记录|pass|
|兆易创新 603986.SH|pass|pass|
|腾景科技 688195.SH|pass|pass|
|德福科技 301511.SZ|pass|pass|
|宁德时代 300750.SZ|pass|pass|
|工业富联 601138.SH|pass|pass|
|小米 01810.HK|pass|pass|
|美团 03690.HK|pass|pass|
|腾讯 00700.HK|pass|pass|

`nine-dependency-audit.json`、`final-consumer-nine.json`为实际重读。原始R1/R2失败记录和质量记录均保留；历史失败轮在新工作队列明确blocked/superseded，不重新展示方向，但保留原预测逐期核验。

## 实际链路与责任

1. `/home/emox/work/investment/reference/operations/mt13-production-20260912/REPORT-CONTRACT.md` 新增强制R3段，由既有早晚/周末agent读取；未改cron、群、时刻。
2. `docs/mt13/deployment/report-consumers.sh` 接受当前agent写出的 `TA10_REVIEW_FILE`，调用 `mt1.ta_workflow apply`，再执行原report-cycle。
3. `action_integration.cycle` 实际保存 `research-work-inbox.json` 和其hash/owner/覆盖数到consumer-receipt，不仅是prompt描述。
4. `ta_workflow.apply` 整批先校验、后不可变落盘；质量绑定原manifest与result hashes，followup绑定原股/时间/新证据/判断理由；报价不能证明经营命题。confirmed/refuted不得早于短期目标收盘，且须有目标交易日证据。
5. `due_queue` 读取这些写回的proposition_checks/short_term/error_categories并调用evaluate；收益与经营结论仍分离。收盘未到不能写收益，季度逻辑不能以涨跌自动证实。
6. 新文件 `docs/ta10/R3-WORKFLOW.md` 给职责与可执行入口，当前investment agent在原轮直接处理，不转嫁给用户。无资料必须保留具体blocked原因和next_check_at；达到重试上限由agent调查后带reason恢复，不能无限重试。

**真实执行证据**：
- `actual-agent-review-batch.json`：实际4质量+36跟踪写回；原文/文本132处引用hash重读见 `actual-source-hash-readback.json`。
- `existing-consumer-command.txt`、`existing-consumer-writeback/consumer-receipt.json`：现有日报消费者回放，使用保留的原三段，禁新增模型调用。
- `existing-weekly-command.txt`、`existing-weekly-writeback/consumer-receipt.json`：现有周报路径重复批次、幂等读回。
- `inbox-before.json` / `inbox-after.json`：36项由待处理变为有记录、有owner、下一检查时间的状态。
- `actual-due-writeback.json`：36项经营检查实际进入evaluate，短期保留not_matured判断，五类错误保持unknown。
- `consumer-protection.json`：日报正文与R2逐字节相同；原MT13 manifest hash相同。新增后台工作回执没有改写原报告内容。

这次是**开发按委托通过真实既有消费者处理路径执行回放**，不是冒称自然定时investment turn或线上投递已经发生。未来自然agent遵循新合同的效果仍需后续回执核验。

## 失败恢复回执

- `REAL-recovery/first-failure-receipt.json`：真实systemd bus不可达，exit1。
- `second-launch-receipt.json`：同key退避后真实exit0；该worker又发现旧automatic-quality不可变路径冲突，失败保留。
- 修复为质量结果单独内容寻址的quality-history，不覆盖历史质量文件；`third-recovery-receipt.json` 根据代码identity变化派生新key，沿原checkpoint完成。
- `completed-no-relaunch-receipt.json`：完成状态再请求不启动新worker。
- `zero-new-model-response-proof.json`：所有原响应hash相同。
- `REAL-supervisor-timeout/real-hard-timeout.json`：真实ActiveState=failed/Result=timeout；`timeout-recovery-completed.json`证实退出后恢复完成。sleep仅是无副作用的worker替身，不冒充模型自然超时。

## 测试与提交

环境沿用R2可复现入口：`scripts/test-ta10.sh` 无参数默认运行TA/R2/R3/MT13专项；`scripts/test-ta10.sh tests --ignore=tests/test_iteration_mature_run.py -q` 跑快速全集。解释器为homespace venv，显式加载Ubuntu dist-packages，不新装依赖。

专项最终 **135 passed /21.19秒**，日志 `targeted-135.txt`。新增20个R3场景覆盖启动失败、退避、上限、worker失败/超时、活跃/未知不重复、完成幂等、依赖hash与实质变动级联、工作队列写回正反例、质量版本化、价格不能证明经营。早期负例fixture的报价缺字段失败也保留，修正为完整合成报价后才命中价格归因门禁；未放松业务校验。

最终代码快速全集 **739 passed /230.70秒**，见 `release-regression.txt`；两个未修改的慢成熟窗口测试沿用R2已通过证据，不冒称本轮重跑。

提交：market-tools代码 `e818719`；investment执行合同 `5b069d1`，分仓提交，均未push。证据提交另列结构化交付。所有原始数据/代码时点记录保留；失败日志及HTML空白不做格式清洗。

## 保护与剩余

`protection-final.json`：两个仓库起点已跟踪文件逐一hash核对；market-tools仅本任务代码/脚本/测试改变，investment原有所有已跟踪脏文件未改变。此前未跟踪的既有REPORT-CONTRACT仅追加授权R3段并单独纳入git。未改真实持仓、scope、自选、生产技术参数、cron/群/时刻。

待原topic独立验收。自然线上投递、后续投资agent定时处理回执、9月16日短期及20/40/60交易日收益尚待发生；未知个人成本、美元费用、A非严格可比仍保留。新原文发现/语义判断由既有investment agent承接，不冒充全市场爬虫或自动财务鉴证。旧轮错误不删除、原始预测不覆盖。

演练收尾：仅清理本任务无网络sleep测试unit的failed状态（reset-failed），不停止或修改任何生产service/timer；原始systemd journal和timeout回执保留。
