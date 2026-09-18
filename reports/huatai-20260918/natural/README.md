# 同工单自然窗口闭环（delivery revision 2）

work_8589a04c66187c547536。仅观察既有自然早晚窗口，未新建cron、未改变时刻/群、未补跑咨询或重发文档。
此次不修改已认可生产实现，不重新执行九股样稿；取证仅只读API、文件与日志，证据写入本目录。

|自然任务|真实咨询截至（北京时间）|run|九股状态|原群发送时间|
|---|---|---|---|---|
|07:15晨报|07:20:07|aa3c8dde317cbe7565900757|9/9成功，各1次|07:32:09|
|18:30晚报|18:34:59|801116a6744054585578f215|9/9成功，各1次|18:50:30|

早报原群消息 om_x100b65f3c9539ca8b1154602ac819b7，晚报 om_x100b65e539abfcb0b306ea2a985277f；均再次用只读API现场核验消息及文档链接。
每轮两次真实consumer（初次/TA签审后）复用本轮同一个咨询run；每股仅一个原始response且attempt=1。
早晚run及全部九股answer hash互不相同；请求截至时点、代码与归档相符，未把旧回答冒充新咨询。
全部成功正文在本地最终日报逐字存在。四份线上文档再次现场读取，整个最终日报（不仅华泰部分）去Markdown/空白/标点后连续匹配；每股也单独匹配。
两轮均无华泰失败，故没有自然失败样本；失败可见/隔离回归由原topic独立复跑253 passed，本轮不人为制造失败或增加收费调用。

## 最终正文hash

- 晨：5b99bd9d6a9df841bd52fa61cc9e2ce946c0f8b0184828484129d7f2c71c679a
- 晚：038140047bcc696b5cfc7cd73c7a57bbb87cee405de286d1f734eb3dc8d3b6a9

## 真实自然文档

晨报1/2：https://tcnv6xag1i9w.feishu.cn/docx/S9CCdE5Ofo2hPwxKGdIcccOlnih
晨报2/2：https://tcnv6xag1i9w.feishu.cn/docx/QhQ5dfgFoo0qguxcBNfc45LsnMc
晚报1/2：https://tcnv6xag1i9w.feishu.cn/docx/E5itdGC9aoiFeZxAvjocwHfQnAe
晚报2/2：https://tcnv6xag1i9w.feishu.cn/docx/Ammed6h7coxxerxDDW9cN9rWnYc

## 最坏耗时边界（不把配置核验冒充故障演练）

原始工具调用记录证明，两次自然任务均将整条report-consumers.sh放入独立systemd，RuntimeMaxSec=2400；晚报签审重消费同样独立。
九股咨询批次硬预算1950秒，预留等待清理/技术拼接/签审批次90秒后仍有360秒余量，因此不会因外层任务预算小于子批次而提前杀掉消费者。
worker实效HOMESPACE_TURN_SOFT_TIMEOUT_SEC=1500；这与外层2400不是同一个预算。1950可能跨轮，独立cgroup不会随软截止退出，既有gateway silent cron自动续跑上限2次。代码查询依据为homespace gateway/feishu.py的_AUTO_RESUME_MAX及_fire_cron_silent软超时续跑分支。
本次自然咨询分别约297/303秒，早晚均在首轮完成真实文档及群投递，原日报未丢失。没有自然触发1950秒超时，不声称做过该故障演练或保证外部平台永不故障。

## 证据导航

- closure.json：两窗口最终结构化闭环与正常未知边界。
- morning/evening-audit.json：逐股状态/请求数/hash、最终消费者、发布日期及主消息链接核验。
- morning/evening-budget-evidence.json：真实执行命令及state.db消息ID；只截取systemd启动段。
- *-cron.log / *-huatai.log：真实scheduler firing与批次开始/结束记录。
- *-doc1.json / *-doc2.json：本轮现场只读飞书raw_content；不是首轮样稿。
- *-message-live.json：本轮现场只读原群消息。
- audit.py：无网络/无咨询/不更改源文件的本地复算工具；晨晚均已实际运行断言通过。

费用和独立服务日期未提供是正常来源边界，不属于工程未完成。原日报其他研究pending/MT1附件绑定问题未借本工单修改，亦未把它们说成全部完成。
本工单此前待观察的自然早晚窗口现已收集完毕；提交独立验收，不自行标通过。
