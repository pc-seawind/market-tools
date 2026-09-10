#!/usr/bin/env python3
"""Independent raw JSON/calendar audit of forward artifacts, no production imports."""
import argparse,gzip,hashlib,json
from pathlib import Path
from datetime import datetime,time
from zoneinfo import ZoneInfo


def audit(root,receipt_path):
    root=Path(root);receipt=json.loads(Path(receipt_path).read_text());results=[]
    for item in receipt['cohorts']:
        c=json.loads((root/'cohorts'/(item['cohort_id']+'.json')).read_text())
        key=hashlib.sha256(json.dumps([c['price_asof'],c['method'],c['contract']],ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()[:24]
        assert key==c['cohort_id']
        raw=root/'cohorts'/(key+'-start.json.gz')
        assert hashlib.sha256(raw.read_bytes()).hexdigest()==c['starting_input_sha256']
        with gzip.open(raw,'rt') as f:initial=json.load(f)
        codes={r['code'] for r in initial if r['channels']}
        assert codes=={r['code'] for r in c['members']} and len(codes)==len(c['members'])
        dates=sorted(r['cal_date'] for r in c['calendar_at_registration']['rows'] if r['is_open']=='1')
        def at(d,h):return datetime.combine(datetime.strptime(d,'%Y%m%d').date(),time(h,30 if h==9 else 0),ZoneInfo('Asia/Shanghai'))
        start=next(d for d in dates if at(d,9)>datetime.fromisoformat(c['decision_at']))
        assert start==c['schedule']['entry_date'] and datetime.fromisoformat(c['registered_at'])<at(start,9)
        for h in (20,40,60):assert dates[dates.index(start)+h]==c['schedule']['maturity_dates'][str(h)]
        h=json.loads(Path(item['artifact']).read_text())
        keys={(r['code'],r['horizon']) for r in h['rows']}
        assert keys=={(code,days) for code in codes for days in (20,40,60)} and len(keys)==len(h['rows'])
        for r in h['rows']:
            if r['status']=='not_matured':
                assert at(c['schedule']['maturity_dates'][str(r['horizon'])],15)>datetime.fromisoformat(h['observed_at'])
                assert all(r[k] is None for k in ('gross_return','net_return','benchmark_return','excess_return'))
        for horizon,v in h['aggregates'].items():
            counts={status:sum(r['status']==status and r['horizon']==int(horizon) for r in h['rows']) for status in v['counts']}
            assert counts==v['counts']
            if counts.get('observed_diagnostic',0)!=len(codes):assert v['equal_weight_net_return'] is None
        results.append({'cohort_id':key,'members':len(codes),'rows':len(h['rows']),'schedule':c['schedule'],'aggregates':h['aggregates']})
    return {'audit':'passed','strategy_acceptance':'not_assessed','cohort_count':len(results),'results':results}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root');p.add_argument('receipt');a=p.parse_args();print(json.dumps(audit(a.root,a.receipt),ensure_ascii=False,indent=2))
