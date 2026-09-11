"""Deterministic event-level diagnostics, NOT portfolio performance."""
import csv
import json
import random
from collections import Counter,defaultdict
from statistics import mean
from .history_research import write,sha


def pct(x):
    return 'unknown' if x is None else f'{x*100:.2f}%'


def paired(rows):
    episodes=defaultdict(dict)
    for t in rows:
        if t['group']=='A':episodes[t['episode_id']][t['arm']]=t
    result={}
    for arm in ('structure_failure','structure_failure_atr'):
        pairs=[]
        for eid,arms in episodes.items():
            a,b=arms.get('old_ma60_5'),arms.get(arm)
            if a and b and a['status']==b['status']=='closed' and a['entry_fill']==b['entry_fill']:
                pairs.append(dict(episode_id=eid,code=a['code'],fold=a['fold'],
                    net_delta=b['net_price_return']-a['net_price_return'],
                    drawdown_delta=b['max_drawdown']-a['max_drawdown']))
        codes=sorted({p['code'] for p in pairs}); folds=sorted({p['fold'] for p in pairs})
        # Crossed stock/time resampling, NOT iid events. Fixed analysis seed,
        # not an optimized strategy parameter. Few clusters => descriptive only.
        rng=random.Random(1201); samples=[]
        if pairs:
            for _ in range(2000):
                cw=Counter(rng.choices(codes,k=len(codes)));fw=Counter(rng.choices(folds,k=len(folds)))
                weights=[cw[p['code']]*fw[p['fold']] for p in pairs];n=sum(weights)
                if n:samples.append(sum(w*p['net_delta'] for w,p in zip(weights,pairs))/n)
        samples.sort()
        result[arm]=dict(pair_count=len(pairs),total_episodes=len(episodes),stock_clusters=len(codes),time_clusters=len(folds),
            mean_paired_net_delta=mean(p['net_delta'] for p in pairs) if pairs else None,
            mean_paired_drawdown_delta=mean(p['drawdown_delta'] for p in pairs) if pairs else None,
            descriptive_crossed_cluster_95_interval=[samples[int(.025*(len(samples)-1))],samples[int(.975*(len(samples)-1))]] if samples else None,
            caution='conditional_on_both_closed_same_entry; censored_outcomes_excluded; small correlated clusters; not superiority proof',pairs=pairs)
    return result


def report(out,bundle,result,spec,label):
    base=[t for t in result['trades'] if t['scenario']=='open_price_limit_base_v1' and t['cost_bps']==15]
    pair=paired(base)
    write(out/f'{label}-paired.json',pair)
    # All statuses, costs, execution scenarios remain in machine-readable CSV.
    with (out/f'{label}-summary.csv').open('w') as f:
        fields=['key','sample_count','round_trips','mean_net_price_return','worst_trade_tail_loss','worst_close_drawdown',
                'turnover_one_way_units','false_breakouts','false_breakout_unknown','post_exit_rally_mean','post_exit_rally_unknown','status_counts']
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for key,value in result['summary'].items():w.writerow(dict(key=key,**value))
    coverage=[]; examples=defaultdict(list)
    for p in bundle['panels']:
        counts=Counter();changes=[]
        for i,(b,e) in enumerate(zip(p['bars'],p['evidence'])):
            counts['exchange_sessions']+=1
            counts['price_bars' if b else 'missing_price_bar']+=1
            if e['suspension']:counts['suspension_records']+=1
            if not e['limit']:counts['limit_unknown']+=1
            if b:
                if e['limit']:
                    u,l=float(e['limit']['up_limit']),float(e['limit']['down_limit'])
                    for key,flag in [('open_at_up_limit',b['raw']['open']>=u-.005),('open_at_down_limit',b['raw']['open']<=l+.005),
                                     ('high_touch_up',b['raw']['high']>=u-.005),('low_touch_down',b['raw']['low']<=l+.005)]:
                        if flag:
                            counts[key]+=1
                            if len(examples[key])<3:examples[key].append(dict(code=p['code'],date=b['date'],raw=b['raw'],limit=e['limit'],sources=p['sources']))
                if i and p['bars'][i-1] and p['bars'][i-1]['factor']!=b['factor']:
                    changes.append(dict(date=b['date'],before=p['bars'][i-1]['factor'],after=b['factor']))
            elif len(examples['missing_bar'])<3:
                examples['missing_bar'].append(dict(code=p['code'],date=e['date'],suspension=e['suspension'],sources=p['sources']))
        coverage.append(dict(code=p['code'],start=p['dates'][0],end=p['dates'][-1],counts=dict(counts),
            factor_changes=changes,industry_current_vintage=p['industry_current_vintage'],historical_industry='unknown'))
    for t in base:
        for side in ('entry_fill','exit_fill'):
            e=t.get(side,{})
            if e.get('skipped') and len(examples['delayed_fills'])<5:examples['delayed_fills'].append(dict(code=t['code'],group=t['group'],arm=t['arm'],fill=e))
            if e.get('status')=='filled' and abs(e.get('gap_from_signal_close',0))>.02 and len(examples['gap_fills'])<5:
                examples['gap_fills'].append(dict(code=t['code'],group=t['group'],arm=t['arm'],fill=e))
    write(out/f'{label}-coverage.json',dict(coverage=coverage,execution_examples=dict(examples),
        unknowns=['PIT_universe','historical_industry_membership','auction_queue_and_fill_quantity','broker_actual_cost','cash_dividend_total_return','delisted_population'],
        no_delisted_population_reason='current-vintage survivorship-biased purposive scope; all sampled securities listed before start; not PIT eligibility'))
    lines=['# MT1.2 真实历史择时诊断（'+label+'）','',
        '**只支持当前 vintage 样本内的价格择时诊断，不支持历史选股有效性、组合收益或自动晋级。**','',
        '预先冻结 2021—2025 年 12 只独立 A 股（pilot 为前 3 只），没有按照回报替换或删样本。全部 TREND；非当前持仓池。',
        f"本次有效股票 {len(bundle['panels'])}，采集失败 {len(bundle['failures'])}，股票×OOS 折 {len(result['folds'])}。",
        '120 交易日训练/暖机（不拟合）＋60 purge＋60 test，stride120。交易不得越过 test 末尾；未退出保留，不能强行平仓或删除。',
        '', '## 基准：每侧 15 bps，下一可成交开盘价情景','',
        '|组/臂|信号样本|闭合|状态计数|闭合净事件均值|最差闭合交易|最差闭合收盘回撤|单边换手单位|假突破/未知|卖飞均值/未知|',
        '|---|---:|---:|---|---:|---:|---:|---:|---|---|']
    for key,v in result['summary'].items():
        if key.startswith('open_price_limit_base_v1|15|'):
            lines.append(f"|{'/'.join(key.split('|')[2:])}|{v['sample_count']}|{v['round_trips']}|{v['status_counts']}|{pct(v['mean_net_price_return'])}|{pct(v['worst_trade_tail_loss'])}|{pct(v['worst_close_drawdown'])}|{v['turnover_one_way_units']}|{v['false_breakouts']}/{v['false_breakout_unknown']}|{pct(v['post_exit_rally_mean'])}/{v['post_exit_rally_unknown']}|")
    lines+=['','收益仅是各臂已闭合事件的因子调整价格净收益算术均值，不是资金组合收益、CAGR、真实下单盈亏或股息现金流总回报。',
            '闭合数不同会造成严重删失选择偏差，不能直接比较上表均值认定因果优势。单边换手单位是每笔事件买/卖各 1，不是资金组合换手率。',
            '最差交易是尾部样本观察，不是 VaR。卖飞＝退出后 20 交易日内最大收盘价/退出价−1，负值保留；不足完整 OOS 后续窗口为 unknown。',
            '假突破仅对登记 breakout episode 且完整观察 10 个交易日的闭合样本判断；其余 unknown，不把非突破入场标“成功突破”。',
            '', '## A 同 episode、相同实际入场的配对复核','',
            '|新版退出对旧退出|配对闭合/全部 episode|收益差（百分点）|回撤差（正数较好）|股票/时间簇|描述性双向簇区间（收益差）|',
            '|---|---:|---:|---:|---|---|']
    for arm,v in pair.items():
        ci=v['descriptive_crossed_cluster_95_interval'];ci='unknown' if ci is None else ' ~ '.join(pct(x) for x in ci)
        lines.append(f"|{arm}|{v['pair_count']}/{v['total_episodes']}|{pct(v['mean_paired_net_delta'])}|{pct(v['mean_paired_drawdown_delta'])}|{v['stock_clusters']}/{v['time_clusters']}|{ci}|")
    lines+=['','区间：固定随机种子 1201，2,000 次股票×OOS 折交叉重抽样，不把同股/同时间事件视为独立。仍受少量簇、市场共同冲击、幸存者及双闭合条件影响，不作为显著性或上线证明。',
            'B 比较共同 scope、共同 OOS、相同退出，但信号日期/机会数本来不同；不把不同机会集的闭合均值差宣称为同入场处理效应。',
            '', '## 成本与执行敏感性','',
            '|情景|每侧 bps|组/臂|闭合/信号|闭合均值|最差回撤|','|---|---:|---|---:|---:|---:|']
    for key,v in result['summary'].items():
        scenario,cost,group,arm=key.split('|')
        lines.append(f"|{scenario}|{cost}|{group}/{arm}|{v['round_trips']}/{v['sample_count']}|{pct(v['mean_net_price_return'])}|{pct(v['worst_close_drawdown'])}|")
    lines+=['','## 未闭合暴露（不伪装成已实现收益）','',
            '|组/臂|可标记未闭合数|平均未实现价格标记|最差未实现收盘回撤|','|---|---:|---:|---:|']
    arms=defaultdict(list)
    for t in base:
        if 'unrealized_price_mark' in t:arms[t['group']+'/'+t['arm']].append(t)
    for key,ts in sorted(arms.items()):
        lines.append(f"|{key}|{len(ts)}|{pct(mean(t['unrealized_price_mark'] for t in ts))}|{pct(min(t['unrealized_close_drawdown'] for t in ts))}|")
    lines+=['','未实现标记只扣入场成本，按 test 最后已知收盘估值，没有假设卖出。未与闭合收益混成单一绩效。',
            '', '## 数据覆盖','', '|代码|日历|行情|缺口|停复牌记录日|因子变化次数|当前行业（非历史）|','|---|---:|---:|---:|---:|---:|---|']
    for p in coverage:
        c=p['counts'];lines.append(f"|{p['code']}|{c['exchange_sessions']}|{c.get('price_bars',0)}|{c.get('missing_price_bar',0)}|{c.get('suspension_records',0)}|{len(p['factor_changes'])}|{p['industry_current_vintage']}|")
    lines+=['','逐股、逐折、市场、行业 unknown、波动、趋势阶段及当前行业诊断完整分层见 results.json 的 strata。没有伪造历史行业成员。',
            '', '## 执行证据与未知','',
            '- 独立 historical model，不改 forward `next_fill` 门禁，不把采集时间/事后成交结果写成当年 `known_at`。',
            '- 信号只读当日及此前完成的 OHLCV/宽基；先发信号，再由独立执行器检查下一 session。原始价比当日 vendor 涨跌停价；收益用 raw×factor。',
            '- 基准情景：买入遇涨停开盘、卖出遇跌停开盘跳过；已知停牌（含日内停牌）保守整日跳过。不明缺口/限价/复权立即 unknown，不跳到后面挑有利价格。',
            '- 压力情景：买入当日 high 触涨停或卖出 low 触跌停也整日不成交。这是使用事后行情的悲观成交敏感性，不是开盘前可知规则，也不保证收益一定更低。',
            '- open_at 是交易所 session 时间标签，不是订单成交证明；daily 正成交量只证明当日有交易，不能证明开盘队列/成交容量。小额可成交开盘价是明确模型假设。',
            '- CN T+1 按交易所 session 索引；跳空用真实下一 open，不按昨日收盘或日内止损线假成交。量化成交数量、冲击、税费时变均未核验。',
            '- 缺行情不会插值，也不会压缩掉缺失交易日：信号状态重暖机120；已有持仓跨停牌后因不足暖机标 unknown，保守但会损失覆盖。',
            '- 当前样本上市日期须早于开始日。没有历史退市总体、历史 ST/行业成员或历史指数成分 PIT 资格证明；当日限价使用供应商历史实际限价而非统一假设10%。',
            '- 费用15/30/50 bps每侧是冻结模型情景，不是券商核验费率。港股独立可用性检查见 hk-feasibility.json / hk-alternative.json；不在本 CN 结果中凑数。',
            '', '## 复跑与结论边界','',
            f"`python -m mt1.history_research --out {out} {'--pilot ' if label=='pilot' else ''}--offline`",
            '读取冻结合同和实际 HTTP 字节 checkpoint，校验 hash，重新生成全部信号与交易。首次采集去掉 --offline 并继承已有 TUSHARE_TOKEN（不得打印）。',
            '这些结果可以支持是否继续 shadow 研究，不能证明历史选股有效、未来可获利、资金组合超额或实盘可交易。没有调参、没有晋级、没有下单。',
            '', '## 来源','',
            '- Tushare 实际 HTTPS 响应：raw/*.json；请求参数、离线获取时间与 SHA256：raw/*.meta.json。',
            '- [涨跌停数据](https://tushare.pro/document/2?doc_id=183)、[停复牌数据](https://tushare.pro/document/2?doc_id=214)、[复权因子](https://tushare.pro/document/2?doc_id=28)。',
            '- 研究合同文件 SHA256：`'+sha(out/'frozen-contract.json')+'`。']
    (out/f'{label}-report.md').write_text('\n'.join(lines)+'\n')
