# 华泰逐股每日咨询（2026-09-18）

生产入口仍为 `docs/mt13/deployment/report-consumers.sh morning|evening FIRST SECOND COMPANY OUT`。
已核对 gateway 当前两份 cron 均真实调用该入口；不创建/更改 cron，不改群和时间。
`mt1.huatai_daily` 复用已安装 financial-analysis CLI，不维护第二套接口/凭证。

## 执行边界

- 从真实 tracking-scope.json 冻结持仓、活跃候选及推荐并去重；代码保留零前缀与市场后缀。
- 原稿所在目录 + phase 是唯一 report run；TA 签审重跑不重复咨询，新报告使用新目录。
- 独立 systemd transient，2 并发、180 秒单请求硬超时、网络最多重试一次；业务和HTTP 4xx不重试。
- 批次硬预算 `max(1950, ceil(N/2)*365+60)` 秒；中断请求不自动重发，显示未知/失败。
- `HT_APIKEY` 仅按变量名交给 systemd-run，未注入时由安装 skill 读已有配置；不输出密钥。
- 请求明确代码/名称/市场/带时区截至点/短期1—10交易日和中期1—3个月/驱动风险/分歧/推演情景失效条件。
- 原始 query、CLI完整响应、抓取时刻、sha256、错误全部归档。skill不暴露HTTP服务日期或费用字段，均明确未知/未提供；正文内日期不篡改。

## 消费与投递

原日报及 TA/MT13 保留。消费者分节追加完整 `data.answer`，不经 TA 事实筛选，不补方向或概率，不冒充正式签署研报。
每股失败单独显示，其余结果继续；scope变化或hash不符不使用旧正文。
`consumer-receipt.json` / `huatai-consumer-receipt.json` 提供run/hash/覆盖回执。
`publication-parts.json` 指向≤90000字节的无损切片，拼接等于 daily.md；使用原 hs_create_doc 渠道逐份发布，主消息给全部裸URL。
消费者不自行群发、不以 not_published 回执冒充成功。每日agent需实际发布、readback、留工具回执。

## 首跑证据

`reports/huatai-20260918/verification.json`：九股真实调用均成功，三个港股也成功；所有请求各1次。
原始存档 `huatai-morning/`；截至北京时间2026-09-18 01:48:20。
`consumer-final/daily.md`：复用9月17日晚报三段原稿验证消费者，新华泰全文独立标时点；这是手动验证，不是自然晨报。
`feishu-verification.json`：用户可见文档真实readback，九股完整原文规范化后连续匹配；native表格保留。
`regression-final.txt`：TA/MT13及新增测试；初始venv缺requests/bs4，复用本机已有系统包后通过，未安装/修改依赖。
`protected-before-verification.json`：消费者验证前对2293个持仓/自选/技术账本/并发数据文件取hash，验证后未变化；不声称它是开工前快照。

## 剩余验证与回退

部署已生效于home-ubuntu工作区，下次cron启动读取新模块，无需worker/gateway重启。
早报/晚报首次自然发布仍待各自真实窗口，不能用本次手工样稿代替。
回退仅撤回本次入口与消费拼接代码，不删除咨询档案，不碰原账本或cron。
