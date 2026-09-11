#!/usr/bin/env python3
"""Only append/replace our marked section to EXISTING four crons; backup/CAS/readback."""
import argparse,json,subprocess,shlex
PHASES={'morning-market-brief':'morning','evening-market-recap':'evening','weekend-saturday-recap':'saturday','weekend-sunday-preview':'sunday'}
MARK='[MT11_SHADOW_SIDECAR_V2]'
END='[/MT11_SHADOW_SIDECAR_V2]'
BASE='/home/emox/work/projects/market-tools'


def section(phase):
    return f'''{MARK}
【MT-1.1修订：本节优先于旧quality_value池描述】
先读daily-report-policy.md与tracking-scope.json，日报纠偏优先；后台研究不是日报前置依赖，不覆盖原cron修订。旧quality_value池仅原始shadow对照，不是用户候选。确需后台研究时用独立cgroup运行：
systemd-run --user --unit=mt11-{phase}-$(date +%s) --property=RuntimeMaxSec=3900 --working-directory={BASE} /usr/bin/python3 {BASE}/mt1_job.py parallel-cycle {phase}
本入口采集/复用当日panel、计算三通道与诊断、读取 .cron_state/mt1/parallel-review-inbox.json（若存在）并绑定只读plans.db，输出RESULT_JSON。收割journal后读cycle.json/report.md/bound-review.md。单次请求预算250、内部3600秒、独立cgroup3900秒硬限。财报sweep未完成或数据缺失明确报依赖，不用旧日替换、不把pending改成pass。
仅研究附录用research-batch.jsonl（最多10项，已移除风险否决且经过tracking-scope门禁），日报第一屏必须三地市场摘要，discovery-pool.jsonl仅宽泛发现留痕；列risk×timing交叉统计，trigger不是可买池。near_MA20_low_volume仅均线附近缩量诊断，不得写成已确认回调承接。未齐写研究未完成，齐备写仅待方法与签审批准；shadow永不final。
公司原文研究由investment完成：按docs/MT-1.1-parallel-channels.md协议准备真实研究包。reviewed_at/decision_at必须带时区和实际时间；行情price_asof可为昨交易日，隔夜证据按decision_at校验，不倒填日期。partial只表示已接入部分证据。
持仓/原期限从bound-review.json绑定；unknown保持unknown、原EXIT不自动重入、期限不自动延长。旁路观察文件不是实际交易或最终计划事件，后续真实签审仍走既有finalize门禁；不传watchlist --execute。
保留原运行/投递回执和collect-dispatch留证；MT11本次为shadow接入，不宣称策略收益已验证。parallel-cycle已自动注册稳定cohort并收割20/40/60交易日观察，读forward-harvest.json及其不可变artifact；未成熟必须not_matured且收益null，成熟后遇停牌/退市/公司行动或来源未知必须blocked，不丢失败股票凑均值。费用是冻结的双边各10bps情景、CSI300价格基准、退出后现金不再投资，不是实盘或收益验证。自动收割功能已实现与当前窗口未成熟分开说明。旧2024缺档独立incomplete。
{END}'''

REMOTE=r'''
import json,pathlib,sys,datetime,hashlib,os
payload=json.loads(sys.stdin.read());root=pathlib.Path.home()/'.homespace/cron'
original={n:(root/(n+'.json')).read_bytes() for n in payload}
backup=root/('_mt11-sidecar-backup-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f'));backup.mkdir()
for n,raw in original.items():
 d=json.loads(raw);assert d['name']==n and d['domain']=='investment'
 (backup/(n+'.json')).write_bytes(raw)
results=[]
for n,section in payload.items():
 raw=original[n];d=json.loads(raw);text=d['prompt'];mark='[MT11_SHADOW_SIDECAR_V2]';end='[/MT11_SHADOW_SIDECAR_V2]'
 if mark in text:
  assert text.count(mark)==text.count(end)==1
  start=text.index(mark);stop=text.index(end)+len(end);text=text[:start]+section+text[stop:]
 else:text=text+'\n\n'+section
 p=root/(n+'.json');assert p.read_bytes()==raw,'CAS conflict'
 updated={**d,'prompt':text};tmp=backup/(n+'.tmp');tmp.write_text(json.dumps(updated,ensure_ascii=False,indent=2)+'\n');os.replace(tmp,p)
 actual=json.loads(p.read_text());assert actual==updated
 assert {k:v for k,v in actual.items() if k!='prompt'}=={k:v for k,v in d.items() if k!='prompt'}
 results.append({'name':n,'before_sha256':hashlib.sha256(raw).hexdigest(),'after_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'non_prompt_fields_unchanged':True})
print(json.dumps({'backup':str(backup),'verified':results},ensure_ascii=False))
'''

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args()
    payload={n:section(phase) for n,phase in PHASES.items()}
    if not a.apply:print(json.dumps(payload,ensure_ascii=False,indent=2))
    else:
        cp=subprocess.run(['ssh','-o','ConnectTimeout=10','vps','python3 -c '+shlex.quote(REMOTE)],input=json.dumps(payload),text=True,capture_output=True,timeout=45,check=True)
        print(cp.stdout)
