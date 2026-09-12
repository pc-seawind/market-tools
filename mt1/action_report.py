"""Local report integration only. No cloud publishing or production edits."""
from pathlib import Path
from .action_loop import read,write,weekly
from .timing_cli import file_hash


def idempotent_write(path, text):
    path=Path(path)
    if path.exists():
        if path.read_text()!=text:raise ValueError('report_path_already_has_different_content')
    else:write(path,text)


def compose(first,second,company,manifest,out):
    m=read(manifest);base=Path(manifest).parent
    result_path=base/m['result']['path']
    if file_hash(result_path)!=m['result']['sha256']:raise ValueError('result_hash_mismatch')
    r=read(result_path)
    part=next(x for x in m['materials'] if x['name']=='daily-third-section.md')
    tech=base/part['blob']
    if file_hash(tech)!=part['sha256']:raise ValueError('technical_report_hash_mismatch')
    # Preserve each of the caller's three sections byte-for-byte, append only
    # the independent technical sub-section to section 3. Never regenerate 1/2.
    text='\n\n'.join(Path(p).read_text() for p in (first,second,company))+'\n\n'+tech.read_text()
    idempotent_write(out,text)
    receipt={'out':str(out),'sha256':file_hash(out),'technical_manifest':str(manifest),
             'manifest_sha256':file_hash(manifest),'source_sections':{str(p):file_hash(p) for p in (first,second,company)},
             'source_kind':'SYNTHETIC_ONLY' if r['synthetic'] else 'real_current_readonly_collection',
             'not_published':True}
    from .action_loop import dump
    idempotent_write(str(out)+'.receipt.json',dump(receipt)+'\n')
    return receipt


def weekly_markdown(w):
    lines=['**技术信号模拟周报｜不是个人盈亏或策略有效性认证**',
           '合成演示，所有行情/成交均非真实。' if w['synthetic'] else '真实数据前向观察；未接实盘。',
           '|版本|本周BUY/SELL|跨周未执行|模拟持仓/退出|有效往返样本|净价格收益|',
           '|---|---|---|---|---|---|']
    for v,r in w['versions'].items():
        lines.append(f"|{v}|{r['BUY']}/{r['SELL']}|{len(r['cross_week_pending'])}|{len(r['positions'])}/{len(r['exits'])}|{r['effective_round_trips']}|{r['net_returns'] or '未成熟/无成交'}|")
        lines+=['',f"**{v}｜未执行与限制**",
                str(r['cross_week_pending']) if r['cross_week_pending'] else '无待执行订单。',
                '取消='+str(r['cancelled'])+'；过期='+str(r['expired'])+'；路径回撤='+str(r['path_drawdowns']),
                '假突破='+str(r['false_breakouts'])+'；退出后反弹/卖飞观察='+str(r['post_exit_rally']),
                '20/40/60信号复盘（价格观察，不是个人盈亏）='+str(r['signal_reviews']),
                '有效性/费用/路径限制='+str(r['gaps']),
                '无交易时逐卡区分硬数据缺口、规则未满足、执行证据不足；不因低置信度封口。']
    lines+=['',w['comparison'],w['next_iteration'],'不自动调参；对新版本只作生效日之后的独立模拟。']
    return '\n'.join(lines)+'\n'


def publish_weekly(root,asof,out):
    from .action_loop import dump
    w=weekly(root,asof);idempotent_write(out,dump(w)+'\n')
    md=str(out)+'.md';idempotent_write(md,weekly_markdown(w))
    return {'json':str(out),'json_sha256':file_hash(out),'markdown':md,'markdown_sha256':file_hash(md),'not_published':True}
