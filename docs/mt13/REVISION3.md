# revision3：已选择observed模拟估值，分市场有限窗口

投资域已选择observed-quote-v1，不再要求重复选模型。本轮不重跑真实接口、不改生产调度、不造自然信号；MT12/信号条件/strict资格不变。

## 窗口（北京时间，09:24启动）

| 模型 | 市场 | 采集截止 | max-seconds | systemd RuntimeMaxSec / TimeoutStartSec |
|---|---|---|---|---|
| observed-quote-v1 | CN | 09:40 | 960 | 990 |
| observed-quote-v1 | HK | 10:05 | 2460 | 2490 |
| strict-open-v1（独立诊断） | CN/HK分别 | 09:31:01 | 421 | 451 |

HK报价允许最多1200秒旧，不改变。09:50收到provider=09:30，下一次更新后可估值；留到10:05覆盖多次更新和网络重试。两次来源时间/成交量严格增长、实际收齐价和时刻、最大配对间隔60秒仍保留。CN仍最多20秒旧。strict仍09:30–09:31的60秒，不因worker多1秒退出边界放宽。

worker同时受时长预算和绝对市场截止约束；晚启动只用剩余窗口，不回填。预算上限2700秒；单请求8秒，systemd额外30秒用于结束和硬杀。盘前资格并行预取，窗口内每3秒单市场批量quote。两个市场worker共享root，消费事务锁内重读，分市场只处理自己的订单，不能将另市场缺资格误写blocked。

休市按各自交易日历退出；日历未知/权限不足明确失败；未更新/过旧/限流轮询有界到窗口结束，保留原因后下次合法运行恢复。半日市正常上午仍覆盖；台风/特别延迟开市没有09:30之后合格报价时本次不估值，不推定交易开始。超出有限上午窗口的特殊开市需另行审阅窗口版本，不自动放宽报价/资格规则。

## 最小部署清单（投资topic执行，code未部署）

以下在market-tools目录；ROOT须全新且仅用于observed，不复制strict账本。OUT各次唯一。凭据仅从现有EnvironmentFile注入。

```bash
# 首次初始化=真实只读日评写全新隔离root（可能全部HOLD，不建真实持仓）
python3 -m mt1.action_loop run --execution-model observed-quote-v1 --root "$ROOT" --out "$INIT_OUT"
# 此后每天原日评阶段执行同一命令，OUT换唯一目录；不等开盘worker
python3 -m mt1.action_loop run --execution-model observed-quote-v1 --root "$ROOT" --out "$DAILY_OUT"
# 以下两项由投资域按09:24分别调度，使用service模板与表中硬预算
python3 -m mt1.action_loop execution-worker --root "$ROOT" --out "$CN_OUT" --market CN --mode watch --max-seconds 960 --poll-seconds 3
python3 -m mt1.action_loop execution-worker --root "$ROOT" --out "$HK_OUT" --market HK --mode watch --max-seconds 2460 --poll-seconds 3
# 原第一段/第二段/公司研究文件来自既有报告，不用技术采集替代
MT13_LEDGER_ROOT="$ROOT" bash docs/mt13/deployment/report-consumers.sh morning "$FIRST" "$SECOND" "$COMPANY" "$MORNING_OUT"
MT13_LEDGER_ROOT="$ROOT" bash docs/mt13/deployment/report-consumers.sh evening "$FIRST" "$SECOND" "$COMPANY" "$EVENING_OUT"
MT13_LEDGER_ROOT="$ROOT" bash docs/mt13/deployment/report-consumers.sh weekly "$ASOF" "$WEEKLY_OUT"
```

worker长命令必须在独立systemd服务里按表设置硬时限，不前台绑定日报。service模板未安装。原日报/发布不依赖worker成功，消费30秒外层超时，失败保留原行情/公司研究；周报技术模块失败不阻断其他周报。

回退：投资域停用新增worker调用和技术消费调用，保留ROOT原证据；不删除账本、不改真实持仓/scope。strict诊断用原独立root，收益/账本不得混算。

## 首个自然开市核验：唯一CLI

2026-09-14自然窗口尚未验证；投资域部署后，对CN/HK分别执行同一个入口：

```bash
python3 -m mt1.action_loop verify-run --root "$ROOT" --capture-dir "$HK_OUT" \
  --report-dir "$MORNING_OUT" --report-dir "$EVENING_OUT" --report-dir "$WEEKLY_OUT"
```

输出worker结构化执行日志、全部原响应hash核验、不可变归档核验、最新动作/订单attempt/模拟仓和退出、三报告消费回执及缺失项；CN用CN_OUT再执行一次。未到周报日期可见missing=true，不伪造产物。systemd异常被硬杀且无worker-result会直接报错，应读取该unit的journal补查，不当成功。此命令只读，不续单、不采集、不发布。

## 验证与限制

新增worker循环端到端合成时钟/HTTP/存储注入测试：09:24开始，HK完整20分钟延迟，09:50与09:50:30两次更新，真实adapter与execute函数在09:50:30模拟估值；过旧、未更新、429全部有界到10:05不成交。并非只直接调用adapter。所有合成数据标SYNTHETIC，不当真实接口采集或自然成交。

observed是已授权的隔离模拟估值，不是实际可成交或validated。HK strict公开源资格缺失仍独立硬阻断，不得影响技术BUY/SELL生成。工程交付不等于收益有效；首个自然开盘、实际生产接线仍由投资域执行并复验。保留原546测试、全部历史证据与未提交用户数据。

轮询与落账解耦：每次原始HTTP仍归档；bundle仅引用预取资格＋上一/当前报价，避免长窗口复制累计响应造成平方级膨胀。无信号/相同阻断最多每60秒落账，合格更新立即消费。双市场并发若另一个已提交更新时刻，旧capture记录可恢复跳过回执，下一轮再采，不倒写账本时间。
