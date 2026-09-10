# MT-1.0：历史原始输入管道增量（续办 work_2584297bb8bd6304e99f）

当前工单：`work_0754c4a54bd9c615889b`。不重做或改判原 revision=3；不自行验收通过。

## 边界

- 当前生产判断、63证券投研、计划账本、自选与交易全部不改。
- 不新增 cron，不人工发布，不部署其他 worker。仅 home-ubuntu 的受控取数与离线审计。
- 本轮新增的是原始数据归并和 draft 输入流水线，不是已认证的三通道回测。
- `replay_ready=false`、`metrics=null`、`shadow` 保持。输入可下载、字段可计算、PIT可认证是三件事。

## 可复跑组件

|组件|实现|产物 / 拒绝行为|
|---|---|---|
|源发现|`mt1.cache_union.discover`|扫描默认及环境配置的 JSON/Parquet daily、adj_factor、stk_limit 目录，和下载任务/探测回执引用的原始切片。明确列出扫描根、源文件、非目标/派生数据排除项|
|冻结与归并|`mt1.cache_union.Union`|源文件内容先冻结为 SHA256 命名 blob，再解析；SQLite 保留来源、原始行号、行 hash、缺身份拒绝记录、逐字段冲突列表|
|冲突策略|同上|数值按 Decimal 归一；非空互补字段合并；重叠非空值不一致则隔离整个 API/证券/日期键；没有最后写入者优先。查询辅助字段 end_date 不作为行情事实|
|覆盖与暖启动|`scripts/mt1_cache_union.py`|全量存续主表×交易日日历反连接，分别输出 target/warmup 的逐证券逐日 gzip JSONL；区分缺行、冲突、必需字段缺失；停牌事件只解释缺口，不填0、不自动放行|
|原始输入映射|`mt1.raw_asof`|12股财务/估值/行业/技术输入生成 stock、financial、membership、sector、context 五类 **draft**，逐项记录 lineage 和 blocker；没有伪造 verified envelope|
|公司行动与退市|同上|保留分红原始记录、实施状态及日期字段；缺派息/股票到账日明确拒绝；目标窗口50只退市证券保留最后观察行情，但不得作为最后交易日/结算价证明|
|受控下载|`scripts/mt1_backfill_batches.py`|显式任务 manifest，单批≤500调用，总请求预算，CLI禁隐藏重试，单请求25秒；独立 systemd cgroup + RuntimeMaxSec/MemoryMax|

源快照的 hash 只能证明字节一致，不能证明其在2024年已经公开。没有证券代码的旧 JSON 缓存不能从文件名散列反推证券；拒绝行仍留源和行号，不能算覆盖成功。

## 窗口与选择规则

- 暖启动：2022-12-21—2023-12-29，250个交易日。
- 目标：2024-01-02—2024-09-30，181个交易日；前半年信号与其后的结算延伸仍属于数据开发窗，不按收益选择窗口。
- 全市场 daily / adj_factor / stk_limit / suspend_d 按日取数。
- 财务、估值、行业和暖启动的逐股算子校验选择：冻结L主表中2022-12-01前上市者，按证券代码排序前12股；不是按强度/收益选股。另对目标窗口全部50个退市主表条目取公司行动。
- 每日返回行数低于6000限制仍不等于源历史完备；最终以 universe 反连接检查缺口。L/D/P主表本身的历史完备性仍待认证。

## 原始到 asof 的真实边界

`build_draft` 已串起真实原始文件读取、hash校验、技术特征、当日估值字段映射、财务版本候选索引、行业区间候选、五类字段缺口、公司行动检查以及实际 `recompute` 拒绝输出。

**未完成的是可通过门禁的认证包端到端构建**：

1. 财务 `ann_date/update_flag` 和当前抓取时间不能还原首次披露/修订版本。所有候选只供审查，不写入 financial payload。
2. 申万历史区间不能顶替生产概念池历史。sector 的历史直接ETF行情/资金流/权重、context 的历史评分没有完整来源。
3. 三年估值历史和原始行情/估值PIT版本未认证。技术特征构建成功也不会签发 asof 认证。
4. 用户/提供商自报 `pit_verified=true` 不会被本阶段采纳；本阶段输出的 `bundle.inputs` 明确为空，实际重算返回 blocked。
5. 当前规则仅是回溯应用当前源码；不是2024年生效规则。

这些是具体上游和映射缺口，而非用全量取数成功冒充已完成。三通道20/40/60/PIT/OOS无收益输出；0 final不作为工程失败。

## 复跑

所有输出目录必须全新，禁止覆盖证据。真实下载 manifest 已有 checkpoint，原样重跑只会复用成功结果；不要为重取而删缓存。

```bash
cd /home/emox/work/projects/market-tools
/home/emox/work/homespace/.venv/bin/python -m pytest tests -q

# 系统Python已有pyarrow；homespace测试venv没有pyarrow，不混用。
systemd-run --user --unit=mt1-union-UNIQUE \
  --property=RuntimeMaxSec=2400 --property=MemoryMax=3G \
  --working-directory=/home/emox/work/projects/market-tools \
  /usr/bin/python3 scripts/mt1_cache_union.py --output /tmp/mt1-union-UNIQUE

systemd-run --user --unit=mt1-asof-UNIQUE \
  --property=RuntimeMaxSec=300 --property=MemoryMax=1G \
  --working-directory=/home/emox/work/projects/market-tools \
  /usr/bin/python3 scripts/mt1_raw_asof_audit.py \
    --union /tmp/mt1-union-UNIQUE --output /tmp/mt1-asof-UNIQUE
```

归并中的 `union.sqlite` 可用只读 SQLite 独立查询：

```sql
SELECT api, count(*) FROM panel GROUP BY api;
SELECT api, count(*) FROM panel WHERE conflicts != '[]' GROUP BY api;
SELECT api, code, day, source, row_number, row_hash
FROM provenance WHERE code='000001.SZ' AND day='2024-01-02';
SELECT * FROM sources WHERE id=<source>;
SELECT reason,count(*) FROM rejects GROUP BY reason;
```

自然调度证据不由上述离线脚本制造。四时点到达之后才能查对应真实 dispatch/run/bundle/finalize/message receipt/readback；未到时点不人工触发。详细运行数字与当前自然时点状态见本轮 delivery/evidence。
