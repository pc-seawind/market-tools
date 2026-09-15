# TA-1.0 R2 生产接续与测试入口

## 不改变的边界

仍按现行 MT13 / MT14 及三段日报政策工作。无真实交易、持仓/scope/自选修改；无新 cron、发送群、时刻修改。A 是已发生的原日报，不是与 B/C 同一模型/证据截止的严格对照。成本未知，不产生个人盈亏。

## 真正的调用链

现行 investment `REPORT-CONTRACT.md` 要求调用：

`docs/mt13/deployment/report-consumers.sh evening FIRST SECOND COMPANY OUT`

该脚本 → `mt1.action_loop report-cycle` → `mt1.action_integration.cycle`。
R2 在这个实际 consumer 中添加非阻塞 `ta_pipeline.request_refresh`：

1. 只有真实归档 daily-reports 下的 evening 源目录可启动；合成/临时测试及早报不发模型请求。
2. 根据报告原始输入 hash、scope hash、TA 代码 hash、公司材料 catalog hash 计算 request_id。同输入重复读已有请求，不重复模型调用；下一份日报及代码/资料版本改变产生不同 key。
3. 一个本机 `systemd-run --user` transient service，硬上限一小时。不是 timer / cron；不发送飞书。日报最多等待启动回执，不等待模型。
4. 独立 revision 重新 `freeze`：重新采行情与财务供应商、读本轮日历/报告/公司材料，绝不复用上一日 input.json。原 `freeze` 的同根恢复是 checkpoint，不再用作跨日生产根。
5. 归档六角色真实请求响应。每个角色最多一次额外真实模型纠错，保留 initial/repair 两份原响应；canonical 是被选择原响应的字节副本，非本地填字段。最终仍错就 blocked，不重试到满意。
6. 写 shared longitudinal 归档、生产 latest 指针和逐原始 run/stock 到期队列。所有新研究在逐股语义复核完成前不展示方向；仅机械协议通过不等于完成签审。
7. 下一份现有早报/晚报/周报读取最近已完成 revision；新一轮正在运行不会阻塞原三段与 MT13。**异步生产意味着当前晚报不等待本轮研究，不能宣称当晚已自然投递该 revision。**

本轮目录 `.cron_state/ta10-production/requests/*/input.json,status.json,launch.json` 及 `revisions/*/runs/*/manifest.json` 留有真实调用链回执。

## 到期核验不是自动因果结论

`ta_pipeline.worker` 完成每次现有晚报触发后执行 `due_queue`：
- 原始预测保持不变，按原 run_id、公司、manifest hash 单独登记。
- `ta_forward.panel` 汇合历次冻结日历的实际交易日与盘后供应商原报价；完整日历缺日就 blocked，不把自然日当交易日、不插值补收盘。
- 下一交易日已到而无报价明确 blocked；有报价也只记观察到，条件情景尚需复核，不自动报预测命中。
- 20/40/60 由 `ta_review.evaluate` 在核验日历、首个未来收盘和后续第 N 个交易日均有原行情时计算未复权价格观察；不是含分红总收益、超额归因或个人收益。没成熟就 not_matured；成熟缺价格就 blocked。
- 财务/公司新披露和价格分别登记。经营命题需 reviewer、rationale、新证据引用，不能因股价涨跌变成 confirmed/refuted；错误分类未知仍未知，绝不自动改参数。

继续核验入口：
- 正常：下一次已有 evening consumer 自动启动。
- 原始队列：`.cron_state/ta10-production/due-latest.json`。
- 人工公司命题核验：`mt1.ta_review.evaluate` 的 `proposition_checks`（已提供完整正反例测试）。
- 逐股发布复核：`ta_quality.checked_review(manifest, review_path)`，quality 必须绑定 run/input/manifest/result hashes、各检查证据 ID；pointer 绑定 quality 文件 hash 后消费者再读。未完成的语义签审不可假装自动完成。

公司原文 catalog 目前覆盖本轮九家公司及已核公告，是有界资料集，不冒充穷尽公告扫描。后续财务供应商自动采新 vintage；新公司原文/事件须由研究取证更新 `*.meta.json` 指向新原文并核日期/身份，改变 catalog 会触发新 revision。尚无独立的全市场公告发现器；这不是把过期旧原文当新披露。

## 可复现测试环境（不修改 homespace venv）

```bash
cd /home/emox/work/projects/market-tools
scripts/test-ta10.sh
# 精确选择：
scripts/test-ta10.sh tests/test_ta_research.py tests/test_ta_r2.py tests/test_mt13_action_loop.py -q
# 全集（约二十余分钟，应 systemd-run，RuntimeMaxSec 至少 2400）：
scripts/test-ta10.sh tests -q
```

脚本使用 `/home/emox/work/homespace/.venv/bin/python` 的 pytest，显式 `PYTHONPATH=/usr/lib/python3/dist-packages` 使用 Ubuntu 的 requests/PyYAML。启动即打印实际解释器与版本；系统 python 无 pytest、裸 homespace venv 无 requests，均不再隐含。`TA10_TEST_PYTHON` 可覆盖解释器，但必须满足同依赖。

## 回退

无需删除任何归档：撤销 `action_integration.cycle` 中 research_refresh 调用即可停止后续生产；消费者缺包/陈旧/未复核仍保留原三段。不要删除 ledger、修改 scope 或停用原 MT13 timers。

## 原文复核后的定点修订（不是伪装成再次全量调用）

`mt1.ta_revision` 建立**同一冻结九股集合**的子 revision：`input_hash` 不变，父 manifest hash、复核反馈与 changed_roles 明确登记。未改角色的请求/响应逐字节复用并注明不是新调用；被改角色实际再调用同一模型并保留旧响应到 parent-records。修改过多空意见时，C 再读当前四份意见。新 manifest、result hash 与语义 quality 另存，绝不手改原响应或降低门禁。

本轮定点修订由开发执行，用户不需要复制命令。未来处理同类复核问题的现成入口为 `python3 -m mt1.ta_revision --parent ... --feedback ... --root ...`；仍须完成质量记录并由消费者校验，不能把“模型生成了”当作人工/独立验收通过。
