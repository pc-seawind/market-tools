"""Small read-only real-data replay. No discovery recompute or production writes."""
import argparse
import gzip
import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from mt1.daily_tracking import track
from mt1.scope import load
from mt1.research_pool import research_queues, review_queue, report


def sha(raw): return hashlib.sha256(raw).hexdigest()


def run(scope_path, manifest_path, source, out):
    dest=Path(out);dest.mkdir(parents=True,exist_ok=False)
    scope_path,manifest_path,source=Path(scope_path),Path(manifest_path),Path(source)
    original=scope_path.read_bytes();scope=load(scope_path)
    manifest_raw=manifest_path.read_bytes();m=json.loads(manifest_raw)
    if m['scope_epoch']!=scope['epoch']:raise ValueError('daily archive wrong epoch')
    materials={}
    for item in m['materials']:
        if item['status']!='archived':continue
        p=manifest_path.parent/item['blob'];raw=p.read_bytes()
        if sha(raw)!=item['sha256']:raise ValueError('daily archive hash mismatch')
        materials[item['name']]=raw
    result_raw=(manifest_path.parent/m['result']['path']).read_bytes()
    if sha(result_raw)!=m['result']['sha256']:raise ValueError('daily archive result mismatch')
    prior=json.loads(result_raw);inputs=json.loads(materials['provider-inputs.json'])
    if materials['scope.json']!=original:raise ValueError('scope changed since archived daily, require explicit different-cohort review')
    gates={market:{**v,'allowed':False,'reason':'manual archived calendar replay, not live gate',
                   'original_allowed':v['allowed']} for market,v in prior['markets'].items()}
    result=track(str(scope_path),fetch=lambda c:inputs[c]['provider_parsed_bars'],gates=gates)
    for row in result['rows']:
        if row['code'] not in inputs:continue
        row['consumed_at']=row['fetched_at'];row['fetched_at']=inputs[row['code']]['fetched_at']
        row['source']='hash-verified archived '+str(manifest_path)
        result['inputs'][row['code']]['fetched_at']=inputs[row['code']]['fetched_at']
    result['execution']={'mode':'manual_offline_cross_market_consumer_replay_not_natural_execution',
                         'host':socket.gethostname(),'source_manifest':str(manifest_path.resolve()),
                         'source_manifest_sha256':sha(manifest_raw),'production_event_writes':0}
    raw_summary=(source/'summary.json').read_bytes();summary=json.loads(raw_summary)
    raw_funnel=(source/'funnel.json.gz').read_bytes()
    if sha(raw_funnel)!=summary['frozen_hashes']['funnel.json.gz']:raise ValueError('funnel changed')
    records=json.loads(gzip.decompress(raw_funnel))
    ranked=sorted([r for r in records if r.get('channels')],key=lambda r:r['research_rank'])
    cohort_raw=(source/'forward-cohort.json').read_bytes();cohort=json.loads(cohort_raw)
    if set(cohort['codes'])!={r['code'] for r in ranked} or cohort['decision_close']!=summary['asof']:raise ValueError('cohort changed')
    backlog,batch,held=research_queues(ranked,records,scope);reviews=review_queue(scope,records)
    coverage={'mode':'offline_queue_replay_manual_verification_not_natural_execution','asof':summary['asof'],
              'universe':len(records),'discovered':len(ranked),'outside_backlog':len(backlog),'outside_batch':len(batch),
              'holdings_review':len(held),'due_review_batch':len(reviews),'completed_company_research':0,'new_admissions':0,
              'scope_epoch':scope['epoch'],'source_funnel_sha256':sha(raw_funnel),'source_cohort_sha256':sha(cohort_raw)}
    (dest/'daily-consumer.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    (dest/'coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2))
    for name,rows in [('research-batch',batch),('holdings-review',held),('research-reviews',reviews)]:
        (dest/(name+'.jsonl')).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    lines=['**跨市场 daily 实际消费｜离线手工验收**','数据源为已归档 CN/HK 原始解析行；不是新行情抓取或自然调度回执。',
           '|代码|市场|expected date|实际报价日|收盘价|状态|','|---|---|---|---|---|---|']
    for r in result['rows']:
        lines.append(f"|{r['code']}|{r['market']}|{r['market_gate']['expected_date']}|{r['quote_date']}|{r['close']}|{r['status']}|")
    lines += ['费用/复权未核验的行情列表不当收益核算；新绩效核验器需独立事件基准和公司行动覆盖。',report(scope,coverage)]
    (dest/'report.md').write_text('\n'.join(lines)+'\n')
    # Freeze actual CN + HK archived source bytes, not a URL-only claim.
    (dest/'daily-source-manifest.json').write_bytes(manifest_raw)
    (dest/'daily-source-result.json').write_bytes(result_raw)
    (dest/'provider-inputs.json').write_bytes(materials['provider-inputs.json'])
    (dest/'scope-input.json').write_bytes(original)
    if scope_path.read_bytes()!=original:raise ValueError('scope changed during replay')
    receipt={'mode':result['execution']['mode'],'host':socket.gethostname(),'scope_unchanged_sha256':sha(original),
             'rows':len(result['rows']),'failed':len(result['failed']),'market_counts':{m:sum(r['market']==m for r in result['rows']) for m in ('CN','HK','US')},
             'files':{p.name:sha(p.read_bytes()) for p in dest.iterdir() if p.is_file()}}
    (dest/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    return receipt


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--scope',required=True);ap.add_argument('--daily-manifest',required=True)
    ap.add_argument('--queue-source',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    print(json.dumps(run(a.scope,a.daily_manifest,a.queue_source,a.out),ensure_ascii=False))
