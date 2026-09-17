# 华泰逐股每日咨询交付证据

工单 work_8589a04c66187c547536；实现提交 market-tools 8fda62e；部署合同 investment 09a05a7。
未push；home-ubuntu工作区即真实入口，CLI每次新进程加载，已生效；不重启gateway/worker。

- 真实咨询run：e4b6a755749da1a19705a857；北京时间2026-09-18 01:48:20截至。
- 9只股票真实请求各1次，均成功；API输出不代表人工分析师签署研报，财务/预测观点未经本方自动背书。
- 实际日报入口读取生产MT13账本，本方原三段来自9月17日晚报，仅用于消费者手动回放；不是9月18自然早报/晚报。
- 9只成功data.answer逐字包含在daily.md，原三段也逐字保留；重跑无新增请求。
- consumer-final/daily.md与publication-part-01.md SHA256：439d429473a558302bd5b1d52875de8ceabb9ce0165e8f9ab6f0e8d87d49dc8c。
- TA/MT13及新增测试253 passed；回归命令与输出见regression-final.txt及docs/huatai-daily.md。
- 验证前后2293个受保护文件hash不变；未碰并发脏文件/持仓/自选/策略参数/旧归档，未调用交易功能。
- 实际费用未知，服务未返回计费字段；独立服务日期字段未提供，正文资料日期原样保留；未采购任何服务。

## 用户可见完整样稿

https://tcnv6xag1i9w.feishu.cn/docx/Sq72dUmoUoRf6IxWpsbc7d6enSF

86,935字节原稿完整上传；真实raw_content读回九股全文规范化连续匹配，2066原生block、28表格。
MCP hs_read_doc自身遇到大行缓冲错误，使用gateway已认证lark-cli只读API替代成功；未改homespace。

|标的|代码|状态|请求次数|
|---|---|---|---|
|德明利|001309.SZ|成功，全文已消费/文档readback|1|
|腾讯控股|00700.HK|成功，全文已消费/文档readback|1|
|小米集团|01810.HK|成功，全文已消费/文档readback|1|
|美团|03690.HK|成功，全文已消费/文档readback|1|
|宁德时代|300750.SZ|成功，全文已消费/文档readback|1|
|德福科技|301511.SZ|成功，全文已消费/文档readback|1|
|工业富联|601138.SH|成功，全文已消费/文档readback|1|
|兆易创新|603986.SH|成功，全文已消费/文档readback|1|
|腾景科技|688195.SH|成功，全文已消费/文档readback|1|

## 产物入口

- huatai-morning/：scope/manifest、逐股request/response/result与完成清单，失败同样保留。
- consumer-final/consumer-receipt.json、huatai-consumer-receipt.json：真实消费覆盖/hash/独立模块回执。
- consumer-final/publication-parts.json：无损分页清单，沿原飞书发布通道使用。
- verification.json、feishu-verification.json：消费者/文档实际读取回执。
- deployed-cron-readback.json：原早晚cron的调度及入口只读核验（未修改）。
- feishu-create.json、feishu-raw-content.json、feishu-blocks.json：完整服务回执及读回（后两者本机留档，摘要hash在feishu-verification）。
- systemd-journal.txt：独立真实批次进程记录。

## 如实保留的缺口

首次自然早报和晚报投递尚未到验证窗口；当前仅声明代码部署、九股真实咨询、手工消费者与用户可见文档完成。自然窗口由原topic独立核验，不自行标验收通过。
