#!/usr/bin/env python3
"""Patch only existing report cron prompts, preserving concurrent/user settings."""
import argparse
import json
import shlex
import subprocess
from scripts.deploy_mt11_sidecar import PHASES, BASE

MARK='[RESEARCH_POOL_LIFECYCLE_V1]'
END='[/RESEARCH_POOL_LIFECYCLE_V1]'


def section(phase):
    return f'''{MARK}
【本节修正旧scope研究门禁，优先于旧研究批次描述；时间/群/交易规则不变】
先读 {BASE}/docs/research-pool-lifecycle.md。发现/研究/观察池/重点研究推荐与真实持仓分离；只有正式计划受scope准入门禁，不得再用codes(scope)过滤持仓外research-batch。
既有parallel-cycle产物：research-batch.jsonl=最多10只持仓外研究任务，holdings-review.jsonl=全部已确认持仓复核（含panel缺失者）。读summary.research_coverage、report.md，不以exit0/errors=[]/not_fetched字段推断公司研究是否完成。缺持仓外队列必须披露覆盖故障；批次未签审前绝非推荐。
公司研究由investment执行，完成一项即按字段契约持久记录 research-event --input <真实事件文件>；未研究/源站受阻/部分研究分别pending，不得把未知写成rejected。watch需经营逻辑、原文hash、1—3个月节点、风险、下次复核；recommended另需估值及风险签审。不造BUY、不改参数、不写实盘或第三方自选。
读最新池状态：python3 {BASE}/mt1.py research-status --out <本轮唯一目录>/research-pool.json 。scope以只读投影消费新epoch事件，旧池不导入；不要手改tracking-scope.json或复制旧研究事件。正式计划仍走独立finalize，绑定当前scope_epoch/research_event_id，方法shadow不升级。
日报沿用daily-track，实际核对rows中的research_pool_state及research_event_id；观察池与退出后验证独立标注，不冒充真实持仓或个人盈亏，失败逐项明示。
周六三表：真实持仓复核、持仓外新推荐/原判断核验、退出后表现（缺收益证据写待核验）。周日三表：真实持仓、持仓外机会、池新增/升级/降级/退出/维持变更。必须消费当前research-status + 最新同epoch/同数据日旁路输出，给扫描→发现→队列→实际研究→待核验→入池各层数量/范围/日期；0新推荐合法，但分清未研究、数据受阻、研究不合格，不以9持仓替代持仓外覆盖。附本轮10只任务的实际结论或未完成原因。退出保留历史、原理由/期限及证据，过期只回待核验，不能自动复活。
保留原归档及发布回执规则；工程离线重放不是自然调度，公司研究与自然执行另行验收。不得新增watchlist-decay-weekly或任何调度。
{END}'''


REMOTE=r'''
import json,pathlib,sys,datetime,hashlib,os
payload=json.loads(sys.stdin.read());root=pathlib.Path.home()/'.homespace/cron'
original={n:(root/(n+'.json')).read_bytes() for n in payload}
backup=root/('_research-pool-backup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f'));backup.mkdir()
for n,raw in original.items():
 d=json.loads(raw);assert d['name']==n and d['domain']=='investment'
 (backup/(n+'.json')).write_bytes(raw)
results=[]
for n,section in payload.items():
 raw=original[n];d=json.loads(raw);text=d['prompt'];mark='[RESEARCH_POOL_LIFECYCLE_V1]';end='[/RESEARCH_POOL_LIFECYCLE_V1]'
 if mark in text:
  assert text.count(mark)==text.count(end)==1
  text=text[:text.index(mark)]+section+text[text.index(end)+len(end):]
 else:text+='\n\n'+section
 text=text.replace('最多10项，已移除风险否决且经过tracking-scope门禁','最多10项持仓外研究任务，已移除风险否决；正式计划仍受tracking-scope门禁')
 p=root/(n+'.json');assert p.read_bytes()==raw,'CAS conflict'
 updated={**d,'prompt':text};tmp=backup/(n+'.tmp')
 with tmp.open('w') as f:
  json.dump(updated,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
 assert p.read_bytes()==raw,'CAS conflict before replace'
 os.replace(tmp,p)
 actual=json.loads(p.read_text());assert actual==updated
 assert {k:v for k,v in actual.items() if k!='prompt'}=={k:v for k,v in d.items() if k!='prompt'}
 results.append({'name':n,'before_sha256':hashlib.sha256(raw).hexdigest(),'after_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'non_prompt_fields_unchanged':True,'cron':actual['cron']})
print(json.dumps({'backup':str(backup),'verified':results,'decay_cron_exists':(root/'watchlist-decay-weekly.json').exists()},ensure_ascii=False))
'''


def main():
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args()
    payload={name:section(phase) for name,phase in PHASES.items()}
    if not a.apply: print(json.dumps(payload,ensure_ascii=False,indent=2));return
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','vps','python3 -c '+shlex.quote(REMOTE)],input=json.dumps(payload),text=True,capture_output=True,timeout=45,check=True)
    print(result.stdout)


if __name__=='__main__': main()
