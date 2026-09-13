"""Event statistics, not portfolio returns. Unknowns and common cash slots retained."""
from collections import Counter
from statistics import mean, median


def stats(rows, field='net'):
    vals=[r[field] for r in rows if r.get(field) is not None]
    dd=[r['drawdown'] for r in rows if r.get('drawdown') is not None]
    return dict(total=len(rows),known=len(vals),unknown=len(rows)-len(vals),
                mean=mean(vals) if vals else None,median=median(vals) if vals else None,
                positive_share=sum(v>0 for v in vals)/len(vals) if vals else None,
                full_denominator_mean=mean(vals) if vals and len(vals)==len(rows) else None,
                drawdown_known=len(dd),mean_path_drawdown=mean(dd) if dd else None,
                worst_path_drawdown=min(dd) if dd else None,
                status_counts=dict(Counter(r.get('status','unknown') for r in rows)))


def summarize(rows):
    tables={}
    for category in ('fundamental','technical_price','technical_execution','technical_capital'):
        source=[r for r in rows if r['category']==category]
        for period in ('all','2021','2022','2023','2024','2025','2024-2025'):
            subset=[r for r in source if period=='all' or (r['date'][:4] in ('2024','2025') if period=='2024-2025' else r['date'][:4]==period)]
            for h in (20,40,60):
                for cost in (1,2,3):
                    rs=[r for r in subset if r['horizon']==h and r['cost_multiplier']==cost]
                    arms={a:[r for r in rs if r['arm']==a] for a in ('old','new')}
                    old={r['identity']:r for r in arms['old']};new={r['identity']:r for r in arms['new']}
                    pairs=[]
                    for key in sorted(set(old)|set(new)):
                        a,b=old.get(key),new.get(key)
                        pairs.append(dict(net=b['net']-a['net'] if a and b and a.get('net') is not None and b.get('net') is not None else None,
                                          drawdown=b['drawdown']-a['drawdown'] if a and b and a.get('drawdown') is not None and b.get('drawdown') is not None else None,
                                          status='paired' if a and b else 'missing_arm'))
                    tables[f'{category}|{period}|{h}|{cost}']={a:{'opportunities':stats(arms[a]),'selected_events':stats([r for r in arms[a] if r.get('selected')]),'gross_selected':stats([r for r in arms[a] if r.get('selected')],'gross')} for a in arms}
                    tables[f'{category}|{period}|{h}|{cost}']['delta_common_opportunity']=stats(pairs)
    return tables


def pct(x): return 'unknown' if x is None else f'{100*x:.4f}%'


def render(result):
    lines=['# MT14 新旧规则真实历史诊断','',
           '状态：'+result['status']+'。非严格未知 OOS，不自动晋级。',
           '样本是冻结的12只A股、3只港股，与当前9持仓交集为0；基本面仅12只A股。',
           '信号价格观察采用下一交易日收盘为入点，20/40/60为其后的交易日数；不是可成交证明。',
           '日级执行估计使用独立历史资格证据和下一时段开盘，保留MT13退出；不是实时observed报价、实盘或个人收益。',
           '费用为CN/HK单边15/25bps及2倍、3倍场景。均值均为已知子集；未知不填0。',
           '事件/现金机会不是资金组合：不年化，不称组合最大回撤，重叠事件不是独立样本。','',
           '|类别/窗口|旧/新选择数|旧/新有值数|旧均值/中位数|新均值/中位数|旧/新正收益占比|旧/新最差事件路径回撤|共同机会已知/总数|机会均值差 新−旧|',
           '|---|---|---|---|---|---|---|---|---|']
    for cat in ('fundamental','technical_price','technical_execution','technical_capital'):
        for h in (20,40,60):
            t=result['summary'][f'{cat}|all|{h}|1'];a=t['old']['selected_events'];b=t['new']['selected_events'];d=t['delta_common_opportunity']
            lines.append(f"|{cat}/{h}|{a['total']}/{b['total']}|{a['known']}/{b['known']}|{pct(a['mean'])}/{pct(a['median'])}|{pct(b['mean'])}/{pct(b['median'])}|{pct(a['positive_share'])}/{pct(b['positive_share'])}|{pct(a['worst_path_drawdown'])}/{pct(b['worst_path_drawdown'])}|{d['known']}/{d['total']}|{pct(d['mean'])}|")
    lines+=['','## 年度/固定后段及费用压力（60日）','',
            '|类别/时段/费用倍数|旧事件净均值|新事件净均值|共同机会差|共同机会已知/总数|','|---|---|---|---|---|']
    for key,t in result['summary'].items():
        cat,period,h,cost=key.split('|')
        if h!='60' or (cost!='1' and period!='all'):continue
        a=t['old']['selected_events'];b=t['new']['selected_events'];d=t['delta_common_opportunity']
        lines.append(f"|{cat}/{period}/{cost}|{pct(a['mean'])}|{pct(b['mean'])}|{pct(d['mean'])}|{d['known']}/{d['total']}|")
    lines+=['','## 研究测量限制']+['- '+s for s in result['limitations']]
    lines+=['','## 工程未完成项']+['- '+s for s in result['incomplete']]
    return '\n'.join(lines)+'\n'
