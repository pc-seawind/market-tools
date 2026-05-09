"""sector_health.py — 给任意 ts_code 打"板块健康度"标签.

为什么需要:
  之前的选股工具 (funnel/momentum/screen) 只看个股指标, 不看该股所处
  板块的整体状态. 结果: 陕西煤业 PE 15 + 股息 3% 单看是好股, 但它所
  在的"高股息 / 煤炭" 整体在衰退 (concepts.sh 里倒数 top 3, 板块 1M
  -3.4%), 推荐等于接逃命筹码. -8% 暴跌的根因就在这.

输入:
  ts_code (e.g. "601225.SH")
  daily_data (全市场 daily, 用于算行业 1M 均涨)

输出:
  {
    "heat_score": 0-4 分,   # 越高越热
    "heat_label": "🔥" / "🟡" / "🧊",
    "reason":     "属于 🔥 光通信 (+57.5% 1M)" 或 "所在行业已衰退",
    "concept":    命中的概念名 (或 None),
    "industry":   申万 L1 行业名,
  }

评分逻辑 (取 concept_score 和 industry_score 的 max):
  concept_score:
    所属 concepts_data 里的某主题, 该主题在 concepts.sh 当前排名:
      top 3:   🔥 4 分
      top 7:   🟡 3 分
      7-10:    🟡 2 分
      末 4:    🧊 1 分
    股票若属于多个概念, 取最热那个
    股票若不在任何概念池: concept_score = None (看 industry)

  industry_score (申万 L1 行业 1M 均涨):
    > +15%:   🔥 4 分
    0 to 15:  🟡 3 分
    -5 to 0:  🟡 2 分
    < -5%:    🧊 1 分

  heat_label:
    4:  🔥
    3:  🟡 (中性偏热)
    2:  🟡 (中性偏冷)
    1:  🧊

依赖: 需要 concept 热度数据 (从 concepts_data + 最新 daily 数据算) 和行业数据
     (stock_basic + daily). 提供 build_index() 一次性 pre-compute 所有股票.
"""

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def build_index(daily_latest_map, daily_20d_map, stock_basic_map, concept_ranking):
    """预计算所有股票的板块热度. 避免 per-stock 查询重复.

    Args:
      daily_latest_map: {ts_code: {"close": ...}} 最新收盘
      daily_20d_map:    {ts_code: {"close": ...}} 20 交易日前收盘
      stock_basic_map:  {ts_code: {"name": ..., "industry": ...}}
      concept_ranking:  [(concept_name, avg_r1m, stocks_list), ...] 按 r1m 降序

    Returns:
      {ts_code: {"heat_score", "heat_label", "reason", "concept", "industry"}}
    """
    # 1. 概念 → rank 映射
    total_concepts = len(concept_ranking)
    concept_rank = {}   # {concept_name: rank (0-based)}
    concept_r1m = {}    # {concept_name: avg_r1m}
    stock_concepts = defaultdict(list)  # {ts_code: [concept_name, ...]}
    for i, (cname, r1m, stocks) in enumerate(concept_ranking):
        concept_rank[cname] = i
        concept_r1m[cname] = r1m
        for code, _name in stocks:
            stock_concepts[code].append(cname)

    def concept_score(code):
        """返回 (score 1-4, best_concept_name, r1m) 或 (None, None, None)."""
        concepts = stock_concepts.get(code, [])
        if not concepts:
            return None, None, None
        # 取最热的
        best = min(concepts, key=lambda c: concept_rank.get(c, 999))
        rank = concept_rank[best]
        r1m = concept_r1m[best]
        if rank < 3:
            return 4, best, r1m
        elif rank < 7:
            return 3, best, r1m
        elif rank < total_concepts - 4:
            return 2, best, r1m
        else:
            return 1, best, r1m

    # 2. 行业 → 1M 均涨 (扫全市场, 按 industry 聚合)
    from collections import defaultdict as _dd
    ind_returns = _dd(list)
    for code, b in stock_basic_map.items():
        ind = b.get("industry")
        if not ind: continue
        cur = daily_latest_map.get(code, {}).get("close")
        old = daily_20d_map.get(code, {}).get("close")
        try:
            cur_f = float(cur) if cur else None
            old_f = float(old) if old else None
        except (ValueError, TypeError):
            continue
        if not cur_f or not old_f: continue
        r1m = (cur_f - old_f) / old_f * 100
        ind_returns[ind].append(r1m)

    ind_avg = {ind: sum(rs)/len(rs) for ind, rs in ind_returns.items() if rs}

    def industry_score(code):
        """返回 (score 1-4, industry_name, avg_r1m) 或 (None, None, None)."""
        b = stock_basic_map.get(code, {})
        ind = b.get("industry")
        if not ind or ind not in ind_avg:
            return None, None, None
        r1m = ind_avg[ind]
        if r1m > 15:
            return 4, ind, r1m
        elif r1m > 0:
            return 3, ind, r1m
        elif r1m > -5:
            return 2, ind, r1m
        else:
            return 1, ind, r1m

    # 3. 合并: 每只股取 concept_score 和 industry_score 的 max
    index = {}
    for code in set(list(daily_latest_map.keys()) + list(stock_basic_map.keys())):
        cs, cname, cr1m = concept_score(code)
        is_, iname, ir1m = industry_score(code)

        # 取 max
        scores = [(cs, cname, cr1m, "concept"),
                  (is_, iname, ir1m, "industry")]
        scores = [s for s in scores if s[0] is not None]
        if not scores:
            index[code] = {
                "heat_score": 2, "heat_label": "🟡",
                "reason": "无数据, 保守中性",
                "concept": None, "industry": None, "r1m": None,
            }
            continue

        best = max(scores, key=lambda s: s[0])
        score, name, r1m, kind = best
        if score >= 4:   label = "🔥"
        elif score >= 3: label = "🟡"
        elif score >= 2: label = "🟡"
        else:            label = "🧊"
        r1m_str = f"{r1m:+.1f}%" if r1m is not None else "n/a"
        reason = f"{kind}={name} (1M 均涨 {r1m_str})"
        index[code] = {
            "heat_score": score,
            "heat_label": label,
            "reason": reason,
            "concept": cname,
            "industry": iname,
            "r1m": r1m,
        }

    return index


def load_concept_ranking(here, latest_date, d_20d_date):
    """从 concepts_data + 最新 daily 数据算出 concept ranking.

    Returns: [(concept_name, avg_r1m, stocks), ...] 按 r1m 降序
    """
    import subprocess, csv

    try:
        sys.path.insert(0, here)
        from concepts_data import CONCEPTS
    except ImportError:
        return []

    def tushare(api, **params):
        args = ["python3", f"{here}/tushare.py", api]
        for k, v in params.items():
            if k == "fields": args.append(f"--fields={v}")
            else: args.append(f"{k}={v}")
        args.append("--csv")
        out = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return []
        return list(csv.DictReader(out.stdout.splitlines()))

    daily_latest = {r["ts_code"]: r for r in
                    tushare("daily", trade_date=latest_date,
                            fields="ts_code,close")}
    daily_20d = {r["ts_code"]: r for r in
                 tushare("daily", trade_date=d_20d_date,
                         fields="ts_code,close")}

    ranking = []
    for concept, stocks in CONCEPTS.items():
        returns = []
        for code, _name in stocks:
            cur = daily_latest.get(code, {}).get("close")
            old = daily_20d.get(code, {}).get("close")
            try:
                cur_f = float(cur) if cur else None
                old_f = float(old) if old else None
            except (ValueError, TypeError):
                continue
            if not cur_f or not old_f: continue
            r1m = (cur_f - old_f) / old_f * 100
            returns.append(r1m)
        avg = sum(returns)/len(returns) if returns else None
        ranking.append((concept, avg, stocks))

    # 按 r1m 降序排
    ranking.sort(key=lambda x: -(x[1] if x[1] is not None else -999))
    return ranking


if __name__ == "__main__":
    # CLI: 查某股的板块热度
    import subprocess, csv, datetime
    here = os.path.dirname(os.path.abspath(__file__))
    if len(sys.argv) < 2:
        sys.stderr.write("usage: sector_health.py <ts_code>\n")
        sys.exit(2)
    query_code = sys.argv[1]

    def tushare(api, **params):
        args = ["python3", f"{here}/tushare.py", api]
        for k, v in params.items():
            if k == "fields": args.append(f"--fields={v}")
            else: args.append(f"{k}={v}")
        args.append("--csv")
        out = subprocess.run(args, capture_output=True, text=True, timeout=60)
        return list(csv.DictReader(out.stdout.splitlines())) if out.returncode == 0 else []

    # 算日期
    past60 = (datetime.date.today() - datetime.timedelta(days=60)).strftime("%Y%m%d")
    today = datetime.date.today().strftime("%Y%m%d")
    cal = tushare("trade_cal", exchange="SSE", start_date=past60, end_date=today,
                  fields="cal_date,is_open")
    open_days = sorted([r["cal_date"] for r in cal if r.get("is_open") == "1"],
                       reverse=True)
    latest = open_days[0]
    # T-1 fallback check
    probe = tushare("daily_basic", trade_date=latest, fields="ts_code")
    if len(probe) < 100 and len(open_days) > 1:
        latest = open_days[1]
    d_20d = open_days[20] if len(open_days) > 20 else open_days[-1]
    # 如果用了 T-1, d_20d 也偏移一位
    if latest == open_days[1]:
        d_20d = open_days[21] if len(open_days) > 21 else open_days[-1]

    print(f"data: latest={latest}, d_20d={d_20d}", file=sys.stderr)

    # 拉数据
    dl = {r["ts_code"]: r for r in
          tushare("daily", trade_date=latest, fields="ts_code,close")}
    d20 = {r["ts_code"]: r for r in
           tushare("daily", trade_date=d_20d, fields="ts_code,close")}
    sb = {r["ts_code"]: r for r in
          tushare("stock_basic", list_status="L",
                  fields="ts_code,name,industry")}

    print(f"data: {len(dl)} latest, {len(d20)} 20d, {len(sb)} basic",
          file=sys.stderr)

    ranking = load_concept_ranking(here, latest, d_20d)
    print(f"concepts: {len(ranking)} ranked", file=sys.stderr)

    idx = build_index(dl, d20, sb, ranking)
    result = idx.get(query_code)
    if not result:
        print(f"  {query_code} 无数据")
    else:
        print(f"\n  {query_code} 板块热度评级:")
        print(f"    label:   {result['heat_label']}")
        print(f"    score:   {result['heat_score']}/4")
        print(f"    reason:  {result['reason']}")
        print(f"    concept: {result['concept']}")
        print(f"    industry: {result['industry']}")
