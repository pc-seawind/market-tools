# 每日持久材料与周报长期核验接口

对应 work_849ab6618c2d18055563 的追加要求。原投资域 policy 为权威；本模块不代替研究/周报判断，不部署cron，不修改用户池。

## 存储与写入顺序

默认持久目录 `.cron_state/mt1/longitudinal/<job>/<scope_epoch>/<trade_date>/<run_id>/rNNNNNN/`。
每次保存实际材料字节、解析结果`result.json`及版本/时间/hash/失败`manifest.json`。进程锁分配revision，材料和结果fsync；manifest提交后才允许更新latest。重跑追加revision，不覆盖历史。

`daily-track` 自动留存scope输入、实际provider解析日线、各市场原始日历结构及门禁结果，然后才写`--out`。如原`--out`已存在，先保存previous-handoff原字节。不声称保存了供应商原始HTTP响应；行情材料标明parsed_provider_rows、raw_http_body=not_captured。

`thesis-enrich` 自动留存实际thesis输入原文、行情、技术输出、失败manifest，然后才写handoff；非dry-run还在每份YAML改动前保存原文及拟写entry，归档失败不会写该YAML。`--archive-root`覆盖默认位置（测试用隔离临时目录）；不提供禁用生产归档的默认开关。

没有自动清理证据的代码。`.cron_state`是持久工作磁盘而非`/tmp`，但这里没有配置异地主机/NAS定时备份。当前29份抢救材料另封存可核验tar.gz随交付留存；磁盘压力或备份策略仍需运营处理，不自动删除历史。

## 通用归档 CLI（新闻/预测/日报/回执/临时handoff）

```bash
python3 mt1.py archive-evidence --input /absolute/bundle.json
```

bundle 示例（示意schema，不是真实预测证据）：

```json
{
  "job": "morning-report",
  "run_id": "20260911-morning",
  "trade_date": "2026-09-11",
  "scope_epoch": "user-reset-20260911T120928Z",
  "result": {"status": "partial", "failed": ["missing_external_article"]},
  "materials": [
    {"name": "forecast.md", "path": "/absolute/actual-forecast.md", "published_at": "2026-09-11T08:00:00+08:00", "fetched_at": "2026-09-11T08:01:00+08:00"},
    {"name": "report.md", "path": "/absolute/actual-report.md"},
    {"name": "article.html", "path": "/absolute/captured-article.html", "url": "https://example.org/article", "excerpt": "实际使用的摘录"},
    {"name": "restricted-article", "url": "https://example.org/restricted", "reason": "未取得原文"}
  ],
  "events": [{
    "kind": "forecast", "origin_id": "CN-20260911-morning-original",
    "status": "pending", "original_judgment": "实际原稿中的原判断",
    "published_at": "2026-09-11T08:00:00+08:00",
    "target_at": "2026-09-11T09:30:00+08:00",
    "forecast_material": "forecast.md", "verification_target": "当日走势",
    "due_date": "2026-09-11", "next_review_date": "2026-09-12"
  }]
}
```

- 传入实际路径或`content`内容；URL/hash不算正文。不可取得内容标unavailable并列material_gaps，不假装已保存网页。
- 要保存发布回执，将实际provider response/readback作为额外materials；不由本工具模拟发布。
- `kind + origin_id + scope_epoch`生成稳定entity_id。新闻用原始事件ID，推荐用原episode/研究事件ID，不按每日运行时间重建ID。每天持仓自动以代码+epoch保持稳定ID，用户持仓不冒充BUY。
- 原始判断、日期、推荐基准、原始入场/退出理由、验证目标/到期日、forecast发布时间/目标时段/原稿字节hash均不可在相同ID下重写。更新只追加status/观察材料/next_review_date；修订预测使用新origin_id和revises关联原entity_id。
- forecast必须提供原稿实际字节，且声明的发布时间早于目标时段。时间规则校验不等于独立证实其发布时间；操作者仍须提交可核验出处/抓取记录。旧原稿缺发布时间时只做材料归档及provenance pending，不补造forecast。
- 确认/证伪状态要求observation_materials实际存在；模型/操作者判断的真实性由周报审查，并非存档工具自动证明。

## 周报索引

```bash
python3 mt1_job.py --scope /home/emox/work/investment/reference/tracking-scope.json \
  weekly-evidence --asof YYYY-MM-DD --out /absolute/weekly-evidence.json
```

从全部已提交manifest重建稳定ID最新状态、跨周pending、已结项历史、forecast原稿引用、失败运行、材料缺失和hash完整性错误。历史epoch单独标记但不删除，逾期不自动结项，not_matured/blocked继续滚动。索引自身也持久归档后才更新输出。

只提供“已观察到的存档”计数，未接入应执行cron日历，不能据此声称应有/实有覆盖完整。周六集中长期效果/原判断与观察对照；周日消费pending安排后续核验。重大风险仍由daily通知，不等周末。

## 本次真实验证与边界

- 已保存29份既有`/tmp/evening_recap_YYYY-MM-DD.json`、`evening_recap_doc_YYYY-MM-DD.md`、`thesis_enrich_YYYY-MM-DD.json`，原始字节未变。原始epoch/发布时间未验证的明确归为legacy-unclassified；29项provenance pending，不称为29项正式推荐。
- 实际daily-track 9只报价成功；enrich无晚报JSON的9只dry-run成功、written=0；两个handoff均含持久manifest回执。用户scope/watchlist/plans.db/44份thesis前后hash一致。
- 实际weekly索引38 pending：29份旧材料追溯+9持仓持续核验，完整性错误0。没有伪造实际预测原稿或长期收益结论。
- 其他新闻/早晚报正文/发布回执的归档通用接口及生成器规则已提供，线上cron由投资topic接线；未声称所有历史来源原文、未来自然投递或完整周报已验证。
- 前次剩余边界不变：自由文本入场/失效条件自动判断、逐事件推荐基准重建、复权费用收益未实现；真实长期成熟窗口效果没有据本轮短测判通过。
