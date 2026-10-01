# Narrative 指定日期取价修复交付

工作项：`work_61cc3c6457c57eb9d551`。开发交付，不代表原 topic 验收通过。

## 根因与修复

- `narrative_sector_bench._fetch_daily_snapshot` 缺数据时向前找十天，把前日价格缓存到目标日期。已移除 fallback，并逐行检查 `trade_date`；行业持久缓存改用 `exact-date-v2` 命名空间，旧缓存保留但不再消费。
- `narrative_track._fetch_stock_close` 的 Tushare 路径原本有日期核验；`daily_bars` 路径原本检查 `trade_date`，实际接口输出 `date`，属于字段适配缺陷，并非该路径无条件取最后一根。现在兼容两种字段，但只能匹配指定日期。
- 个股、指数 current 必须精确匹配目标日期。benchmark baseline 也必须与已解析的股票 baseline 日期、open/close 口径一致，不再借前/后日指数。
- benchmark 任一锚点缺失时不 append，不用绝对涨跌伪造 hit/strict；`verify_all` 计入 `fetch_failed`，不计 `verified`。
- 没有增加 CN 日历全局门禁。各市场独立取精确日线；无数据（包括休市）计失败，不区分休市与供应商缺数。HK 的有效当日个股及指数数据可正常验证；测试中的 HK 可用场景是合成数据，不宣称实际 10 月 1 日 HK 开市。

## 历史排除与周报路径

`narrative_perf_exclusions.jsonl` 是新增的 append-only 异常清单：精确 canonical JSON SHA256 标识四条记录，同时保存原行字节 SHA256、原因、事件、代码、日期和证据路径。不按整天/整只股票粗暴排除；未来经独立验证的新观察可另行 append，不重写旧行。本次没有伪造更正价格。

共享读取层 `narrative_perf_quality.filter_perfs` 排除四条异常；清单缺失或损坏时 fail closed。覆盖：

- `narrative_track`: report/doc/event_report，含正文明细、预热基线与幂等集合；
- `narrative_backtest.exact_outcomes`；
- `narrative_radar_review.load_perf_window`；
- `backtest_signal_compare.load_perf`。

已只读核查 VPS 上 `narrative-track-weekly-doc` 配置：实际正文入口为 `python3 narrative_track.py doc --weeks 4`。本次实跑此入口生成 `weekly-4w.md`，开头明确列出全账本排除数量、四条身份、原因及哈希。未修改 cron 配置、时间、持仓或推荐 scope。

**窗口事实**：截至 2026-10-01，4 周 cutoff 为 2026-09-03；异常 baseline 分别是 20260803（三条）和 20260831（一条），因此原来的默认 4 周窗口本来就不会计入这四条。不能声称已污染当周 4 周数值。扩大窗口、单事件、回测原本仍可能读取；现在在窗口过滤前统一排除。52 周合成消费测试明确覆盖这个路径。

## 实际验证

- 原账本 12,220 条 → 可消费 12,216 条，精确排除 4 条；详见 `verification.json`。
- 66 项 narrative 定向测试通过（`tests.txt`），覆盖 snapshot 回退、供应商返回错误日期、daily_bars 两字段、CN/HK 独立可用性、benchmark 缺失、持久旧缓存隔离、各读取入口与新记录不被误排除。
- `protected-before.json` 与 `verification.json` 记录原 perf、原 incident 目录、原纵向 archive、事件、推荐账本及 watchlist 的字节哈希对照；全部保持不变。
- 实跑只读 report/doc；未执行生产写入式 verify，也未请求实时行情。交易日/跨市场路径由合成数据回归验证，不冒充实时供应商端到端验证。
- 全仓测试结果见 `full-tests.txt`，最终完成状态另见交付摘要。

## 边界

- 未追溯排查全部历史价格是否曾被 fallback 污染；只显式排除已确认四条。
- 原 archive/result 的 `verified=4` 作为事故证据保留，不改成虚构的当时成功/失败回执。新的周报审计说明覆盖其统计可用性；纯证据索引仍可以展示原运行事实。
- 未来周六任务尚未触发，未创建/发布新的飞书周报，未声称其已完成；当前实际生成器已验证。
- 原 `verify_all` CLI 的 exit-code 行为不变；调用方必须检查 `fetch_failed`，不能仅据 exit 0 判断数据完整性。

## 全仓回归边界补充

全仓收集到 923 项；180 秒有界运行未完成，输出停在 mature-run 长测试附近，不能声称全仓通过。为避免将无关长测试当作本修复失败，又尝试排除 `test_iteration_mature_run.py` 的 90 秒有界回归，结果保留在 `regression.txt`。本任务要求的所有定向 66 项已经实际完成且通过。

排除 mature-run 的扩展回归也在 90 秒上限结束（exit 124，未出现失败摘要），未完成部分集中在其他 iteration 长流程；不将其计为通过。未完成全仓回归列入交付边界。
