#!/usr/bin/env python3
"""Patch only four existing prompts, backup + CAS + readback. No schedule mutation."""
import argparse
import json
import subprocess

PHASES={'morning-market-brief':'morning','evening-market-recap':'evening',
        'weekend-saturday-recap':'saturday','weekend-sunday-preview':'sunday'}
BASE='/home/emox/work/projects/market-tools'


def prompt(phase):
    if phase in ('morning', 'evening'):
        return daily_prompt(phase)
    collection=' --collect' if phase=='evening' else ''
    return f'''执行 MT-1.0 中期研究（1—3个月），本消息替代旧报告编排；不改变已有生产评分。
先读 /home/emox/work/investment/reference/medium-term-recommendation-policy.md 与 {BASE}/docs/MT-1.0.md。后者是工程能力现状，前者的“首批待开发”是历史清单，不把它当代码验收结果。

【唯一数据编排入口】
通过 RunDetached 运行以下命令（不要拼 sector_score/sector_picks 管道）：
python3 {BASE}/mt1_job.py run {phase}{collection}
没有 RunDetached 则用 systemd-run --user --unit=mt1-{phase}-$(date +%s) --property=RuntimeMaxSec=7200 --working-directory={BASE} /usr/bin/python3 {BASE}/mt1_job.py run {phase}{collection}。
任务在独立cgroup运行；读journal与 .cron_state/mt1/runs/<北京时间日期>-{phase}/manifest.json 收割，终端给 RESULT_JSON。被打断重跑同一命令续跑失败阶段；同日新增证据需新run-id。不能只看exit0，必须读report.json的gates/errors及阶段状态。脚本缺失/凭证失败明确报部署异常，不降级成临场拼CLI。

【门禁与数据】
CN/HK/US分别核验日历及expected_date；closed不生成该市场常规介入结论，日历缺失fail-closed。周六/周日方法研究不被休市拦截。早盘只复用上一完成交易日的晚盘数据；缺失报缺口，不拿当天重算冒充。港美股采用当地17点保守完成时刻，半日市精确收盘尚未接入。
原始raw_recap仅机器候选，不是最终BUY。quality_value为新因子shadow池，全量采集由独立systemd sweep任务自动续批并最多重试2次；列 sweeps/<expected_date>/summary.json 的attempted_unique/usable/unavailable/pending及日期，未闭环不得声称已全量筛完，不直接改生产评分或同步自选。

【agent负责证据，不负责确定性编排】
读取plans及对应thesis与上次复核记录；未持有/已有持仓分别研究。验证财务/流动性/重大事件/技术/估值硬约束，再分别审查VALUE修复路径、TREND业绩订单景气、REVERSAL经营或供需改善。证据须真实来源日期与URL；1—3个月节点、价格条件、风险边界、失效条件和复核日必填，缺失则WATCH/待复核。板块转冷/资金转负/单日破均线仅复核，不自动退出；HOLD不重置原始日期/参考价/期限；退出重入必须新episode。未确认持仓成本不算个人盈亏，不写金额股数或仓位比例。

【研究后唯一记录入口】
按 docs/MT-1.0.md 的schema写一个本轮 review-bundle JSON，再调用 python3 {BASE}/mt1_job.py finalize --input <绝对路径>。
review-bundle 必须填 source_run_id=<本轮真实run_id>。此命令做计划事件/方法事件逐项幂等写入、版本冲突检查、差异、审查覆盖、记录与自选dry-run。只有实质变化才写事件；无变化只填reviewed_plan_ids及研究证据。不另手工拼rec_log add，不重复写旧rec日志；MT-1.0以SQLite事件账本为权威，旧rec保留只读历史。
原始watchlist_sync from-recap已硬禁用。最终同步工具是 mt1.py watchlist，默认dry-run；本轮不要传--execute。真实自选成功写入本次尚未验收，不假称同步成功。

【真实链路留证，不以0 final卡死】
每次run自动保存chains/<run_id>/run-*（进程/worker/HEAD/manifest/report hash），finalize自动保存实际bundle、结果与Markdown hash。
冷启动可运行 python3 {BASE}/mt1.py coldstart-export --out {BASE}/.cron_state/mt1/coldstart，选一个尚未完成的batch（10项）审查。不能核验的使用response_template里的review_items(status=pending)，reviewed_plan_ids留空；真实待复核WATCH也可形成报告，不凑final或公司判断。_bootstrap_state是迁移元数据不是股票，单独列数据问题。
发布前按 docs/MT-1.0-operations.md 保存实际create_doc/发消息工具返回及readback，用 evidence --kind delivery 关联本轮run_id与Markdown hash；没有回执标未送达，不伪造文件。自然任务完成后执行 evidence --kind collect-dispatch --run-id <run_id> 收集gateway调度日志；未关联则明确待验。最后 evidence --kind inventory 查缺哪一环。
当前四任务绑定home-ubuntu；若实际worker不是home-ubuntu则停止并报未部署，不去其他worker自行补装。不传watchlist --execute。

【节奏】
本轮phase={phase}。morning查隔夜与到期条件；evening更新当日证据；saturday全量复盘活跃及退出记录；sunday先读最近周六记录，再联网查3—5个可靠来源，可选0—2个方法，不凑数。联网由现有检索工具完成，保存原文与sha256到research.sources；失败标not_verified，不能假称查完。方法candidate→shadow→validated→active，缺PIT/真实通道/样本外证据不得升级。20/40/60回测目前只有信号带复放和数据门禁，真实三通道历史复刻未完成，不能报策略收益已验证。

【发布】
run的初步记录不是已完成审查，finalize记录才是本轮审查产物。先记录再沿用飞书文档发布（domain=investment，topic_label={'周报' if phase in ('saturday','sunday') else '日报'}）；工具不可用直接当前消息交付，不伪造链接。第一屏表格：标的｜未持有者方向｜已有持仓方向｜变化理由｜下次验证点；只详述变化，未审查项明确未审查，无变化不凑推荐。禁#标题、禁止邀请回复句。列已生效/试运行/未完成。'''



def daily_prompt(phase):
    return f"""[MT_DAILY_POLICY_BACKEND_V1]
先读 /home/emox/work/investment/reference/daily-report-policy.md 与 /home/emox/work/investment/reference/tracking-scope.json；本节优先于旧MT报告定位。
早盘前瞻/晚盘总结固定三部分：三地行情播报、目标交易时段预测及外部观点对照、荐股行动条件与已推荐行情跟踪附表。第一屏市场摘要，不是研究批次；MT后台未完成不能阻断日报。不新增cron、不自动交易、不写自选。
持仓事实、活跃候选与原始扫描严格分开；旧计划/诊断不能回灌。未知成本及原推荐基准保持unknown，原EXIT不因用户清池改成BUY/HOLD。
只读行情入口：python3 {BASE}/mt1_job.py --scope /home/emox/work/investment/reference/tracking-scope.json daily-track --out /tmp/mt-daily-{phase}-<本轮唯一ID>.json
CN/HK/US独立日历/asof，休市显示最近完成收盘及实际日期，失败/partial逐只明示。此产物非完整日报，指数、外部原文与走势条件由投资域核验补全，不把shadow作为新推荐。
MT研究仅取已完成时效合格产物；需要后台启动时使用独立cgroup，不为日报重复运行。保留原投递/归档规则和实际正文hash与回执；未收到回执不得声称已送达。
[/MT_DAILY_POLICY_BACKEND_V1]"""

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');a=ap.parse_args()
    payload={name:prompt(phase) for name,phase in PHASES.items()}
    if not a.apply:
        print(json.dumps(payload,ensure_ascii=False,indent=2));return
    remote=r'''
import json,sys,pathlib,datetime,hashlib,os
incoming=json.loads(sys.stdin.read());root=pathlib.Path.home()/'.homespace/cron'
backup=root/('_mt1-engine-backup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f'))
backup.mkdir(); result=[]
# Read all targets before any write; never create a missing report cron.
original={n:(root/(n+'.json')).read_bytes() for n in incoming}
for n,raw in original.items():
 d=json.loads(raw)
 assert d['domain']=='investment' and d['name']==n
 (backup/(n+'.json')).write_bytes(raw)
for n,text in incoming.items():
 p=root/(n+'.json');raw=original[n];d=json.loads(raw)
 assert p.read_bytes()==raw,'concurrent cron update; abort'
 if n in ('morning-market-brief','evening-market-recap'):
  mark='[MT_DAILY_POLICY_BACKEND_V1]';end='[/MT_DAILY_POLICY_BACKEND_V1]'
  old=d['prompt']
  if mark in old:
   assert old.count(mark)==old.count(end)==1
   start=old.index(mark);stop=old.index(end)+len(end);text=old[:start]+text+old[stop:]
  else:text=old+'\n\n'+text
 updated={**d,'prompt':text}
 temp=backup/(n+'.tmp');temp.write_text(json.dumps(updated,ensure_ascii=False,indent=2)+'\n');os.replace(temp,p)
 after=json.loads(p.read_text())
 assert {k:v for k,v in after.items() if k!='prompt'}=={k:v for k,v in d.items() if k!='prompt'}
 assert after['prompt']==text
 result.append({'name':n,'prompt_sha256':hashlib.sha256(text.encode()).hexdigest(),'other_fields_unchanged':True})
print(json.dumps({'backup':str(backup),'verified':result},ensure_ascii=False))
'''
    import shlex
    cp=subprocess.run(['ssh','-o','ConnectTimeout=10','vps','python3 -c '+shlex.quote(remote)],
        input=json.dumps(payload,ensure_ascii=False),text=True,capture_output=True,timeout=45,check=True)
    print(cp.stdout)


if __name__=='__main__': main()
