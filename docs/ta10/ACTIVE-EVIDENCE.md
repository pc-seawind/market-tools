# TA 主动取证层：worker 执行合同

本层修复 R3「流程有结果、研究资料却未覆盖」的问题。它不增加辩论轮数、不改变交易参数、scope、持仓、账本或调度。代码域执行不等于 investment 独立验收。

## 实际路径

`report-consumers.sh evening` → `request_refresh` → 原有独立 systemd transient worker → `ta_evidence.collect` → **新目录 freeze** → B / bull / bear / cross / C → 原报告消费者。

- 采集只用 worker 已安装的 Python requests、BeautifulSoup 与 pdftotext，不依赖主 agent 的 web/MCP 工具。
- 配置：`docs/ta10/evidence-plan.json`。新闻窗口为 7/30 天；九股从真实 tracking-scope.json 读取。公司行业、风险命题、反向查询、关系实体是研究线索，不是已确认供货关系。
- 百度直连 → Bing RSS；原文直连 → Agent-Reach Jina。另有真实可执行的新浪研报索引、东方财富公告索引/全文/PDF。缺 Exa、验证码或 Jina 权限均留下原响应，不停止其他通道。
- 搜索结果仅是线索；正文未核发布时间、主体或机构时不能自动成为事实。不能把门户索引或搜索摘要作为研报正文。
- worker 的原有一小时硬超时保留。网络并发最多四，下载限时/限大小、PDF 子进程限时；失败按任务归档，不抹掉同股其他材料。同 collection 根是 checkpoint，配置/scope 改动要求新根。

## 证据和数值

六种新材料语义类别：company_disclosure、industry_statistics、media_report、institution_forecast、market_narrative、internal_hypothesis；原有 quote / financial / company_primary / hypothesis 保留兼容。

每份材料保存 URL、原机构作者（未知明确 unknown）、发布时间、实际 discovered_at/fetched_at、原文件/text hash、转载链/独立来源键，以及选段的字符起止、页码、段落。PDF 保留全文；输入按客户、成本、风险及产业词抽选段，不再只抽财报科目。

`numeric_metrics` 是可审计白名单，不是从数字正则推断业务含义：
- 声明式 selector 必须唯一命中原文；保留 raw_value、位置、value、conversion_factor、formula。
- 必须记录主体、指标、币种单位、期间、统计范围、basis、is_forecast。
- 模型引用这些数字时必须原样带回语义维度；字段遗漏也报错，不本地修模型响应。
- 装车/出货、单月/累计、现货/期货、股权/采购比例不同；同维度数值不一致先标冲突，不能取平均或先判谁错。
- 媒体和机构预测不能放 facts；公司数值也不能越过原文定位及未来实际数据门禁。
- 数值 selector 是人工审阅配置，并非全自动财务语义理解；遗漏的表格数字仍未知，不能宣称全行业数值已结构化。

## 覆盖与消费

`research_coverage` 与协议 pass 独立。没有搜索、无结果、访问受限、无已核实质正文分别保留；每项有 owner、next_check_at、remedy。实际不可核验不等于证实「未公开」。重大命题的量化链条没核实仍 blocked，即使九个角色文件都完好。

反向查询是任务计划的一部分；历史战略合作和累计份额只能提供有限反证，不能否定后来新增风险。没有来源支持的订单损失、跌幅归因、目标价和个人盈亏不可生成。

消费者实际返回覆盖内容及其 hash；未覆盖旧 run 也不再展示全绿研究方向。技术动作与日报原三段仍照常拼接。`TA10_RESEARCH_ROOT` 只用于显式指定待验研究包；默认仍是原 production root，不改默认指针冒充线上交付。

## 首跑与恢复

```bash
# 生产独立worker自动调用，不需要用户每天抄命令
python3 -m mt1.ta_evidence --root /abs/new-collection
# 开发验收入口：九股冻结，但仅宁德真正调模型；其他股明确 not_run
python3 scripts/ta-evidence-first-run.py --root /abs/new-model-run \
  --report /abs/actual-evening-report --collection /abs/collection.json
```

长任务须用 `systemd-run --user --property=RuntimeMaxSec=...`，不能前台长期挂住。首次实际失败的模型调用也留存成本/token/耗时；每个角色最多一次真实 schema 纠错，绝不能手填返回值。新证据冻结生成新 run，重新调用所有角色，旧 cross/C 不复用。

`supplement(parent, new_root)` 用于已实际执行查询后的有限追加资料/纠正数值口径：复用来源保留原发现时间，新来源真实抓取；查询变化必须重新 collect。它不是修改旧 collection。原 9/15 run 不改；当前研究与任何历史重建分开，未做 PIT 认证。

## 验证边界

测试包括错单位、主体、期间、范围、统计口径、未来实际数据、预测充事实、定位篡改、来源冲突、转载不足、无结果/受限区别、失败回退、checkpoint 与配置变更。`scripts/test-ta10.sh` 显式复用本 worker 的既有 user-site BeautifulSoup；不安装新依赖、不改 homespace venv。

真实交付见 `reports/ta10-evidence-20260916/DELIVERY.md`。开发回执不等于独立验收，手工执行消费者不等于自然线上投递，历史成功运行也不证明未来收益。
