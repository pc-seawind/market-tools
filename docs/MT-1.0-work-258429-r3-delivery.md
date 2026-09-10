# MT-1.0 第三轮：历史适配开发与真实缺行证据（不自标验收通过）

工单 `work_2584297bb8bd6304e99f`；2026-09-11 约03:00，北京时间；home-ubuntu。
入口 HEAD=b78db4c；新增开发提交 9505a15。不重做已获认可的四项门禁和5561只当前扫描。

## 一、开发完成的组件（不等于 D1—D7 数据/策略验收完成）

新增 `mt1/historical.py`，纯离线、独立 shadow 入口，不改生产评分/注册表/计划/自选。

- **D1 版本选择与证据约束**：按决策前可用日期选最新报告期的最新版本；未来修订排除；
  可用的最新版本 PIT 未知则阻断，不能退回旧版本装作最新。拒绝无时区、时间倒置、
  重复版本；校验 payload、原始文件、可用时点审查文件的 hash。55条真实财务记录
  仍保持 `pit_verified=false`，全部被拒绝，未制造首次披露/更正链。
- **D2 历史 universe 构建器**：输入 L/D/P 主数据和显式交易日日历，以
  `[list_date, delist_date)` 构造每日成员。缺上市日、退市日或重复代码阻断完整性。
  退市生效日不冒充最后成交日/结算价。当前主数据回溯不自动认证历史主表完整性。
- **D4 缺行反连接**：对每日成员 × daily/adj_factor/stk_limit 做逐项反连接；
  缺行不填0、不推断停牌；另区分停牌源该日缺覆盖、明确事件、无法解释的缺行。
  输出逐项原因及 gzip 完整缺行文件，而非只写任务列表。
- **D5 as-of 适配器**：真实调用 `sector_picks.evaluate` 重算 action/channel/veto，
  不从旧历史 verdict 反推。要求完整 StockMetrics、SectorSignals、板块上下文、成员区间、
  财务快照五个输入 envelope（各含 available_at/observed_at/version_id/PIT证明及hash）。
  拒绝未来输入、当日收盘前的完整日线特征、缺字段、跨输入财务不一致、proxy板块及规则hash不符。
  当前源码版本明确标为**回溯应用当前规则**，不冒充2024年已生效的方法。
  输出不转抄 legacy reason 内未经本项目验证的代理收益文字。
  另实现原始日线/复权因子的纯 as-of 技术特征构建器：250交易日暖启动、逐日对齐、
  复权锚定窗口末日，拒绝未来因子和缺行，不取最新数据兜底。
- **D6 保守成交模拟器**：决策日之后的首个合格交易日原始开盘；有明确停牌事件（含盘中）
  或触及任一涨跌停边界则保守跳过；未知停牌覆盖、缺行情/因子/限制价直接阻断，
  不能跳过未知日后声称找到“第一可成交日”。手续费/滑点显式配置，滑点越界不成交。
  输出 `simulated_fill`，绝非成交回执；价格为原始人民币，因子另存，
  `replay_ready=false`：公司行动现金、复权收益口径和退市结算未齐，不向回测注入伪总收益价格。

**仍未完成的开发/数据边界**：历史估值与板块等全部原始数据自动生成已认证五类特征包的
端到端管道；D1原文版本链、D2主表历史完备性及退市结算、D3历史概念输入、D4全量暖启动
与停牌面板、D6公司行动现金核算。技术特征遇停牌缺行目前也阻断，不暗中前值填充。
输入 `pit_verified` 是审查契约而非自动证明；文件hash只证明内容一致，仍需独立审查。
D7继续等待上游，不计算三通道20/40/60收益，不升级方法。

## 二、本轮新增真实数据证据

复用此前真实下载、冻结并留hash的响应，不重复接口请求/人工发布：

- L=5561、D=339、P=0；在2024-01-02—2024-09-30共181个交易日构造历史成员。
  首日5335只、末日5354只；保留源清单范围，**不声称已认证无幸存者偏差**。
- 969,309个证券—交易日预期组合；与本次冻结三股行情面板反连接，
  968,766个组合缺至少一张表；三股×181=543个组合三张表均有行。
  **这是相对所选冻结面板的缺行，不是宣称其他Parquet缓存或数据提供商不存在这些行。**
  跨所有缓存的归并、校验和全量回填尚未完成；停牌源仅覆盖9/30。
- 55条真实income/fina_indicator生成来源索引并实测PIT拒绝；三股181日切片实测
  250日技术暖启动不足而拒绝。真实完整投研输入包仍缺，recompute 返回 blocked。
- 9/27决策后9/30三股开盘在本模型下给出原价与费用模拟；这是执行算子数据切片验算，
  不是投资信号、实盘交易或三通道回测收益。

证据目录 `.cron_state/mt1/r3-committed-historical/`：universe.json、holes.json.gz、
three-symbol-holes.json、filing-index.json、real-pit-rejections.json、real-feature-rejections.json、
real-recompute-blocked.json、execution-slice.json、natural-chain-inventory.json、summary.json。
提交的 `MT-1.0-work-258429-r3-evidence.json` 留完整文件hash与摘要，便于独立核对。

## 三、自然时点待验（与开发完成分开）

02:56只读查询VPS成功：四cron原时间/domain/cwd/delivery/silent/model保持不变，
自北京时间9/11零点至查询时没有四核心 firing 日志。首轮读取遇到日志MESSAGE=null，
修正只读解析后重查成功；失败和成功记录都保留，没有将失败当“无触发”证据。

|阶段|下一自然时点（北京时间）|当前证据|
|---|---|---|
|早盘|9/11 07:15|未到，dispatch/run/bundle/finalize/provider消息回执/readback待收集|
|晚盘|9/11 18:30|未到，同上|
|周六|9/12 07:15|未到，同上|
|周日|9/13 05:00|未到，同上|

没有等待数天绑住本会话、没有新增cron或人工代跑。现有自然链采集能力保留；到时需原topic/
后续跟进采集实际证据，不承诺本回合结束后自动监听。文档创建不能替代自然消息送达。
非home-ubuntu停止要求保持；新真实审计脚本在其他hostname直接退出。

## 四、投资域待审

63只证券的实质研究归原投资域，64项交接已收（含元数据）。本轮不重复交接、不造BUY，
不把0 final当工程失败。不更改已有投资结论；自选仍dry-run，无交易、部署或第三方写入。

## 五、复跑命令

```bash
cd /home/emox/work/projects/market-tools
/home/emox/work/homespace/.venv/bin/python -m pytest tests -q
# 164 passed（原109 + 新55）
python3 -m compileall -q mt1/historical.py scripts/mt1_historical_audit.py
git diff --check

# 真实缓存审计；输出目录须全新。脚本无网络/生产账本写入。
systemd-run --user --unit=mt1-r3-audit-UNIQUE \
  --property=RuntimeMaxSec=300 --property=MemoryMax=2G \
  --working-directory=/home/emox/work/projects/market-tools \
  /usr/bin/python3 scripts/mt1_historical_audit.py --output /tmp/mt1-r3-audit-UNIQUE

# 独立算子CLI；--input为相应函数具名参数JSON，recompute为bundle本身。
python3 -m mt1.historical universe --input INPUT.json --output NEW.json
# 另支持 holes / price-features / recompute / next-open；拒绝覆盖已有证据。
```

原推荐逻辑仍见 `MT-1.0.md` 和前两版交付；本轮只是历史数据/算子隔离开发，不替换交易日规则。
所有无关未跟踪文件保持原样。整体仍有明确incomplete，由原投资topic独立验收。
