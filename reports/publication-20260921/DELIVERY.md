# 日报单份优先发布修复

- work_id: work_2f810291d3d8361702aa
- 已核实本机 worker feishu_doc_tools.py:51 与 VPS 实际 gateway feishu.py:365 均为 100*1024 bytes；102400含边界，UTF-8计数。
- 既有 report-consumers.sh → action_loop report-cycle → action_integration.cycle → publication_parts，新进程直接加载工作区代码，无须重启服务。早晚包括技术失败路径都产出发布清单。
- ≤限额完整原稿一个part；超限按显式生产者标注类别省略，元数据范围/hash校验，原稿本地留档。当前真实生产者只标注技术材料source_sha256字段，绝不删除其余风险/unknown/执行状态。未有安全标注的旧日志/回执/附录保守保留；不猜测标题，不以机器外观认定可删。仍超限无损分份并记录原因。
- investment部署合同已同步，保留本方学习华泰规范原段，cron时间/群/参数/持仓未改。

## 今日实际发布（未重跑咨询或研究）

输入：/home/emox/work/investment/reference/daily-reports/20260921T071500-morning/consumer-publish/daily.md

100945 bytes，SHA256 `6d2a30ee6191c963243c6af01f3ad1f18888697a9b1838b8235c14ce5b8d8347`。
与旧89464+11481两份按序拼接逐字节一致，新清单一个part，omitted=[]，全文未删。

新文档：https://tcnv6xag1i9w.feishu.cn/docx/K6CUdMql0oABpfxtR96ceo02nIg

旧两份、旧清单、旧原稿均未覆盖。九股data.answer与本地原响应逐字匹配；云端raw_content + 全量分页blocks回读核验，全篇Markdown呈现后仅空白归一逐字符一致（43825非空白字符，包含数字及标点），九股逐篇覆盖，16个Markdown目标链接匹配。
MCP hs_read_doc遇到既有大响应Separator错误，未修改homespace；改用同一飞书只读API完成回读，并保存失败与成功原响应。

## 验证

`uv run --no-project --with pytest --with requests python -m pytest -q tests/test_huatai_daily.py tests/test_mt13_action_loop.py tests/test_ta_r3.py tests/test_ta_r2.py tests/test_ta_research.py`

覆盖102400边界/中文emoji/CRLF、可选语义类别、不可删华泰、无可删附录fallback、真实compose生产者以及cycle带华泰前缀偏移、TA/MT13/华泰回归。首次借用homespace虚拟环境缺requests的3项环境失败已换独立uv环境复测，不修改用户环境。

`uv run --no-project --with markdown-it-py python reports/publication-20260921/verify-readback.py`

本目录保留source-verification.json、publication-parts.json、原稿快照、publish-receipt.json、readback.json（失败）、feishu-raw-content.json、feishu-blocks.json、readback-verification.json、tests.txt。原始云端响应与正文大文件仅本地归档，不混入代码commit。

## 边界与待观察

本轮没有开发超限单doc追加API；没有为未标注旧附录冒险自动分类。下一次定时自然发布尚未发生，不能用今日回放宣称自然触发成功。未push（未获本轮授权）。交付由原topic独立验收。
