"""Deterministic Chinese A/B/C readback; never manufacture evaluation percentages."""
import argparse
from pathlib import Path
from .ta_research import read, save, digest, now
from .ta_audit import audit
from .ta_review import evaluate


def report(manifest,out):
    mp=Path(manifest);root=mp.parent;out=Path(out);out.mkdir(parents=True,exist_ok=True)
    audited=audit(mp);frozen=read(root/'input.json');by={s['code']:s for s in frozen['stocks']}
    save(out/'audit.json',audited)
    # Actual first follow-up execution: no completed target windows or new evidence.
    followup={s['code']:evaluate(frozen['as_of'],now(),[],{},[],[]) for s in frozen['stocks']}
    save(out/'first-followup.json',followup)
    lines=['# TA-1.0 九股真实 A/B/C 验证（待独立验收）',
           f"run_id：`{audited['run_id']}`；manifest hash：`{audited['manifest_hash']}`。",
           'A 是当日晚报真实原稿，B 为单模型研究，C 为多空初判、交叉质询与裁决；B/C同份冻结输入。A的输入截止/模型不同，非严格匹配实验，不能归因质量增益。',
           'pass只指请求/响应/来源/数值/结构协议审计，绝不代表公司签审或收益有效。未来收益均未成熟；缺因子模型则贡献null。',
           '|股票|协议读回|未解决问题|','|---|---|---|']
    for s in audited['stocks']:lines.append('|'+s['code']+'|'+s['status']+'|'+'；'.join(s['errors'])+'|')
    for s in audited['stocks']:
        code=s['code'];stock=by[code];result=read(root/code/'result.json');calls=result['calls']
        B=calls['B'].get('output') or {};C=calls['C'].get('output') or {}
        lines+=['',f"## {stock['name']} {code}",
                '**A 原流程**：'+ ' '.join(stock['A']['row']),
                '**B 单模型**：'+B.get('adjudication',{}).get('text','输出失败'),
                '**C 多空裁决原文（非已验收建议）**：'+C.get('adjudication',{}).get('text','输出失败'),
                '**C 分歧**：'+'；'.join(c['text'] for c in C.get('disagreements',[])),
                '**已有持仓研究方向**：'+C.get('held_direction','未知'),
                '**未持有者研究方向**：'+C.get('unheld_direction','未知'),
                '**短期目标交易日（确定性日历）**：'+str(stock['target_session']),
                '**短期情景/触发/失效**：'+'；'.join(str(C.get('short_term',{}).get(k)) for k in ('scenario','trigger','invalidation')),
                '**一至三个月命题/下一材料/失效**：'+'；'.join(str(C.get('thesis',{}).get(k)) for k in ('proposition','next_evidence','invalidation')),
                '**下次复核日**：'+str(C.get('next_review_date')),
                '**未解决缺口**：'+'；'.join(C.get('gaps',[])),
                '**可手工复核的证据链**：']
        for e in stock['evidence']:
            lines.append(f"- `{e['evidence_id']}` {e['kind']}；发布上界={e.get('published_at')}；抓取={e['fetched_at']}；raw hash=`{e['raw_sha256']}`；来源：{e['url']}；原字节：`{e['raw_path']}`")
        lines+=['**说明**：精确行情以冻结quote原字段为准；full PDF已归档，模型只读选定摘要。当前财务不是历史PIT证明。']
    u=audited['usage'];lines+=['','## 成本与局限',f"完成调用={u['calls']}；input={u['prompt_tokens']}；output={u['completion_tokens']}；调用耗时之和={u['elapsed_seconds_sum']:.2f}秒（并发，不是墙钟）。美元成本未知。",'A调用成本未知；引用数值门禁通过不等于逐句语义事实通过。没有编造引用准确率、胜率或alpha。初轮20/40/60交易日not_matured；短期目标尚未结束。']
    (out/'nine-stock-report.md').write_text('\n\n'.join(lines)+'\n')
    return audited

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--out',required=True);a=p.parse_args();report(a.manifest,a.out)
