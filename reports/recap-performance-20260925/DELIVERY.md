# cron 性能专项：证据、最小优化与边界

工单 work_44a640f764a00aa83f9f，源 topic #3108。基线 2dfa148（包含已验收 45e3ef0/fb936c1）。实现提交 219d81e。仅 market-tools 改动；未 push，不自行标验收通过。

## 结论

40 分钟是真实后台采集超时，不是主日报发布时间，也不是华泰九股耗时。主日报已有独立路径，不需要为了提早发布再做大规模拆分。当前低风险、实测有效的修改是：**已有 exact-query 缓存命中时不再启动 CLI，也不占远端请求启动间隔**。它改善暖路径，**没有解决冷网络全量 38 分钟预算问题，不能声称整条 cron 已从 40 分钟降到某个数字**。

## 9/21—24 只读时间重建（北京时间）

|日期|HTSC refresh wall|41 板块 score + freshness/setup 上界|后台 picks 区间/终态|华泰九股 wall / 尝试 / 成功|consumer 渲染组装 wall|日报发布核验|
|---|---:|---:|---|---|---:|---|
|9/21|215s|73s|18:35:56 至 19:11:08 外层超时；完整 collector 2400s|475.46s / 10 / 8|4.57s|18:53:04，后台尚未结束|
|9/22|223s|106s|18:36:40 至 19:11:11 外层超时；完整 collector 2400s|363.60s / 9 / 9|5.32s|18:47:43，后台尚未结束|
|9/23|336s|114s|18:38:24 至 19:08:54；partial 3/18，尝试 12|576.44s / 12 / 7|8.19s|18:51:31 文档回读完成，非精确首发时点|
|9/24|未知|未知|detached 18:31:09 启动；生成 checkpoint 19:09:11；partial 4/21，尝试 13；实际退出时点未知|524.12s / 10 / 8|9.72s|所查归档无发布时戳；consumer ready 不等于已发布|

9/24 checkpoint 时间以原 meta.generated_at 为准（详见 JSON）；detached 19:10:05 是 harvest 时间，不替代进程退出。score 缺独立结束 trace，表中是到 picks 边界的含 freshness 上界，不能当精确 score 净耗时。9/21、22 的 2400s 来自 timeout 回执，不是 CPU 累计。9/23 的 collector 18:30:54→19:08:54 是 2280s 实际 wall；2280s 同时也是现在两轮自动恢复共享的预算，并非每次必耗 38 分钟。

`historical-analysis.json` 保存每个已观测 picks 的时间边界、各阶段来源和 SHA256；`historical-journal.txt` 是原 systemd 日志。9/21、22 大多数失败板块相邻边界约 180s，成功的英伟达约 156—160s，PCB 58—60s，港股互联网 2s；9/23 英伟达 38s、PCB 178s。相邻日志差包含编排间隙，不伪装逐 RPC wall。9/24 stdout 没逐行时间，只能留未知。四天历史缓存冷热未知。

公司原文研究/TA 独立开始结束 trace 不足，wall 未知。技术资料 asof 为各日 17:41 左右，不是计算用时。consumer 的 4.57—9.72s 是组装/渲染整体，不是模型研究时长。上传 RPC 净耗时未知；consumer 完成至发布核验的间隔包含 agent 编排、上传、回读及其他工作，不能全算上传。

## 两条关键路径

- 后台研究：`mt1_job → pipeline.lock → HTSC refresh → score → picks/最多两轮恢复 → universe_sweep_launch`。锁内确实同步等 recap，阻塞的是该研究入口及后续 sweep，不是独立主日报。
- 主日报：合法行情/既有技术动作 + 公司研究已有证据/明确 pending + 华泰完整结果/逐股失败说明 → consumer → 原发布回读。9/21—23 有日报早于后台终态的证据。公司未签审不能冒充已审。
- 不改任何 cron 时刻/群、交易/评分参数、两轮共享预算、失败隔离、华泰全文和单文档发布。历史多文档回执只作为历史事实，不回退现行单文档实现。

## 重复请求与现有缓存核对

1. `sector_score._fina_cache` 已按股票在本进程复用；不能再把 41 板块成分出现次数当实际财务请求数。score 字段无 ann_date，picks 有 ann_date，Tushare JSON key 含 fields，两者不是同 key。没有擅自删 ann_date 或投影无字段数据。
2. picks 每只 A 股请求 daily、adj_factor、当日 daily_basic、历史 daily_basic、fina_indicator，共 5 类。前三种历史查询中的 daily/adj_factor/历史 PE 没 start/end，只能说请求未限日期；实际 provider 返回范围/行数/上限取决于返回，不能称其必然全量下载。保留原 PE 样本集合、中位数、复权和 vintage。
3. 当日 daily_basic 的四字段与历史 PE 两字段语义不同，不能直接从历史尾行猜当日所有指标。批量 API 理论有价值，但未验证权限、截断完整性、停牌空行、日期/字段一致性，因此本次不改。
4. `tushare.py` 实际读路径是 exact-query JSON；Parquet 是响应后的双写，并非当前 CLI 读取主路（模块注释与实际调用不同）。`mt1.cache_union` 是离线 daily/adj_factor/stk_limit 合并审计，冲突整键隔离，不是可直接替换 picks/财报 vintage 的在线缓存。
5. 当前 CONCEPTS 投影到 9/23 的 18 个 selected：112 股票出现、103 unique，A 股 107/98；9/24 的 21 个 selected：127/116，A 股 122/111。理论同参数重复分别 45/55 个股票 RPC，**不是实际重复 HTTP 数**。9/25 既有小范围恢复 trace 43 逻辑请求、35 distinct hashes、8 run-cache hits、wall envelope 17.116s，证明已验收 run-cache 已在去重。
6. 同一次 collector 所有板块使用同一 signature 和 cache 目录，跨板块同股票可复用。不同输出文件 cache 目录不同，不同日期/输入 signature 不同；不能删除输入隔离强行复用。底层 exact-query cache 本来跨进程，本次直接读取它，不另造数据缓存。

## 本次最小实现

- `recap_rpc.provider_cache_csv`：只读已有 exact-query 成功非空缓存；参数/fields 不变；额外要求文件为本日、非未来且通过原 TTL。昨日 snapshot 不走新增快路，回落原 CLI，不扩 TTL/跨日提升行情。原 CLI 既有缓存策略未改。
- `tushare.csv_text`：抽出原 CLI CSV wire-format，快路/CLI 共享，保留顺序、空值和原始全部行，不自作聪明改 CSV 引号语义。
- cache miss 仍原隔离子进程/45s timeout、最多 2 RPC、至少 0.5s 启动间隔；没有并发扩大。快路通过相同 vintage 检查，future_data 拒绝，失败/空结果不写 run-cache。
- 按现有 run key 文件锁 single-flight，并在锁内重读缓存，避免同 key 同时 miss 重复启动。锁等待计入 elapsed；没把它当跨所有 provider 的全局额度锁。
- RPC trace 增加 params、cache_layer、cli_started、lock_wait_seconds。cli_started 不等于远端 HTTP 请求，仍可能底层缓存命中。

## 可比实测（7 股、每股 5 请求）

独立 systemd `recap-performance-20260925.service`，RuntimeMaxSec=180，exit 0。只读真实已有数据路径，隔离复制原缓存且保留 mtime，行情为 9/24 已完成日；不调用生产 pipeline、不绕交易日门禁。CLI urllib 网络强制拒绝，**真实市场请求 0、华泰请求 0、生产账本/源文写入 0**。没有重跑九股收费。

|同输入条件|优化前 wall|优化后 wall|CLI 启动前→后|provider 请求|覆盖/输出|
|---|---:|---:|---:|---|---|
|真实已有缓存原 mtime（仅 7 财报缓存当日，其他 28 仍回落 CLI）|17.072s|13.569s|35→28|0→0|7/7；全部指标 SHA 相同|
|冻结数据、模拟同日全暖 provider cache、run cache 冷|17.070s|0.308s|35→0|0→0|7/7；全部指标 SHA 相同|
|冻结冷 miss、模拟 10ms provider；非真实冷网络|17.020s|17.024s|35→35（模拟）|35→35（模拟）|7/7；全部指标 SHA 相同|

真实已有路径下降约 20.5%；全暖 fixture 明确证明消除了启动/不必要限速，但不得把 0.308s 外推成真实全日报用时。冷路径没提速。既有上一单 128.943s 冷板块回执不是本次优化的前后结果，不混作本次收益。

输入 manifest 保存原路径、mtime、SHA 和完整返回行数；六种输出 hash 相同。另有全 sector 冻结测试：真实 `sector_picks/evaluate`、固定 score/signals，包含复权因子变化、360 行历史、财报字段，完整 7/7 与单股失败 6/7 两种场景；要求评分、信号、排序、覆盖、失败股票逐项一致。

## 主日报目标与下一步（不冒充已实现）

主日报可先以 **18:30 后 25 分钟内完成发布回读**作为观察目标：9/21—23 已有 23.08、17.73、21.52 分钟样本，华泰 6.1—9.6 分钟，渲染不到 10s；不足以统计 P95，更不保证每次达标。实际需继续测上传/回读与 agent 间隙；本次 stock 缓存加速主要作用于后台，不虚报为主日报 SLA 提升。

最小流程方案是维护已有独立日报 consumer 依赖：只读时效匹配的已完成研究，pending 明示，不 await MT1 recap/sweep 后才发布；无需现在把 pipeline.lock 拆成大重构。后续若要让 sweep 更早启动，可隔离评估在 recap 前 launch，但必须测额度竞争/锁，不在此单未验证实施。

剩余工程：
1. 尚未全量实测优化后的 18—21 板块冷网络耗时，因此不能证明预算内闭环或给后台 P95；这是本交付真实缺口。
2. 未实施批量 daily_basic、兼容字段 union、历史增量及财报修订失效；应先用实际 provider 权限/分页/修订样本测收益与等价性。重叠股票 run-cache 已有，重复造缓存不是下一步。
3. 9/24 分阶段 trace、四日公司研究 wall/上传净耗时及 9/24 发布时戳仍未知；历史不能补造。下一次自然运行需要按阶段带时戳留证，才能收敛全链关键路径。

提交回原 topic 独立验收；不改变前工单 accepted 状态，不宣称研究已签审或所有性能缺口已消除。

## 测试与保护回执

- 定向最终回归：79 passed（`targeted-final.txt`），含缓存/失败/未来数据/TTL/全 sector 等价/线程及跨进程 single-flight、默认两轮入口、预算、原华泰全文和 MT1。
- 扩大到 MT13 action/execution/windows 的首次合跑：171 passed、1 failed。失败为 `test_explicit_cancel_expired_sell_never_autorenews`，报 `out_of_order_no_backfill`；单独复跑及预载 2dfa148 的原 recap_rpc/tushare 再跑均复现，详见 `unrelated-test-{isolated,baseline}.txt`。未修改策略门禁来让测试变绿，也没有称全套通过。
- `originals-unchanged.json` 核对本轮读取的历史 report/recap/华泰 complete/consumer/发布回执及四份 engine-contracts，SHA 均未变。没有调用 production ledger 写接口，没有改用户原有脏文件。
- 额外未完成项：上述既有 MT13 测试失败未在本性能单修复。
