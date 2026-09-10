"""Read-only investment review handoff. Unknown historical facts stay unknown."""
import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from .plans import qualification_errors
from .data import atomic_json
from .store import digest


def export(state_dir,out_dir,batch_size=10):
    if not 1<=batch_size<=64:raise ValueError('batch_size must be 1..64')
    db=Path(state_dir)/'plans.db'
    c=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True)
    try:
        rows=c.execute("SELECT after_json FROM events e WHERE entity LIKE 'plan:%' AND version=(SELECT MAX(version) FROM events WHERE entity=e.entity)").fetchall()
    finally:c.close()
    plans=sorted([json.loads(r[0]) for r in rows],key=lambda p:p['plan_id'])
    identity=date.today().isoformat()+'-'+digest(plans)[:16];out=Path(out_dir)/identity;out.mkdir(parents=True,exist_ok=True)
    def write(path,value):
        if path.exists():
            if json.loads(path.read_text())!=value:raise ValueError('immutable handoff file changed: '+str(path))
            return
        atomic_json(path,value)
    items=[]
    for p in plans:
        is_symbol=bool(re.fullmatch(r'(\d{6}\.(SH|SZ|BJ)|\d{5}\.HK|[A-Z][A-Z0-9.-]{0,12})',p['code']))
        gaps=qualification_errors(p,date.today())
        item={'plan_id':p['plan_id'],'expected_version':p['version'],'code':p['code'],
            'name':p.get('name',''),'symbol_valid':is_symbol,'state':p['state'],
            'unheld_direction':p['unheld_direction'],'held_direction':p['held_direction'],
            'gaps':gaps+([] if is_symbol else ['legacy_metadata_not_a_symbol']),
            'unknown_original_fields':[k for k in ('original_date','original_deadline','reference_price') if p.get(k) is None],
            'snapshot':p,'owner':'investment','engineering_audit_only':True}
        labels={'evidence_missing':'缺公司原文来源/日期/claim','reviewer_missing':'尚未投研签审','channel_unknown':'通道待论证','horizon_unknown_reconstruct':'原始日期或期限未知，须原记录证据','risk_boundary_missing':'缺风险边界','risk_boundary_unsubstantiated':'风险边界缺来源','invalidation_missing':'缺逻辑失效条件','milestones_missing':'缺1—3个月验证节点','review_due_missing':'缺下次复核日','dates_invalid':'日期缺失或无效','exit_basis_invalid':'历史退出依据待核验','legacy_metadata_not_a_symbol':'迁移元数据，不是股票，不生成建议'}
        item['gap_labels']=[labels.get(k,k) for k in item['gaps']]
        items.append(item)
    for i in range(0,len(items),batch_size):
        batch=items[i:i+batch_size]
        obj={'batch_id':f'{identity}-{i//batch_size+1:02}','ledger_snapshot_hash':digest(plans),
            'items':batch,'response_template':{'phase':'morning','source_run_id':None,
                'reviewer':None,'reviewed_plan_ids':[],'plan_events':[],
                'review_items':[{'plan_id':p['plan_id'],'expected_version':p['expected_version'],
                    'status':'pending','note':'待投资域核验原文，不据工程缺口审计判定公司逻辑',
                    'evidence':[]} for p in batch]}}
        write(out/f'batch-{i//batch_size+1:02}.json',obj)
    write(out/'items.json',items)
    lines=['**MT-1.0 冷启动逐项缺口｜不是公司投资判断**','',
           f'账本快照 `{identity}`；共 {len(items)} 项。未知原始事实不补造，历史 EXIT 不改回 WATCH。',
           '|标的|当前研究状态|缺口|plan_id / version|','|---|---|---|---|']
    for p in items:
        lines.append(f"|{p['code']} {p['name']}|{p['state']}|{'；'.join(p['gap_labels'])}|{p['plan_id']} / {p['expected_version']}|")
    (out/'gaps.md').write_text('\n'.join(lines)+'\n')
    return {'out_dir':str(out),'items':len(items),'batches':(len(items)+batch_size-1)//batch_size,
            'invalid_symbols':[p['code'] for p in items if not p['symbol_valid']],
            'snapshot_hash':digest(plans)}
