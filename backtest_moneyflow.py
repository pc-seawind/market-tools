"""backtest_moneyflow.py — 大单/超大单替代 ETF share 作为板块资金流信号验证.

背景:
  backtest_score_full.py 显示 share-based fund_flow_score 在 AI 板块
  pearson r = -0.0681 (反向). 假设: ETF 份额变动主要反映散户情绪 (追涨抄底),
  不反映机构进出. 那么"东财行业大单 + 超大单净额"作为 *个股级* 主力净流入的
  *板块加总*, 应该才是真正的"主力资金"信号.

  本脚本测试这个假设: 用 tushare moneyflow_ind_dc 的板块大单数据,
  算同样 5d/20d 累加 → 用同样的 step_20 阶梯打分 → 看 forward returns
  分桶是否符合"主力净流入多 = 后续涨"的设计预期.

数据源:
  tushare moneyflow_ind_dc — 东财板块资金流 (含 行业 + 概念 两类).
  字段: buy_elg_amount (超大单净额) + buy_lg_amount (大单净额) = main_force_net
       buy_md_amount, buy_sm_amount = mid/small
       net_amount = total
       close = BK 指数价位 (用作前瞻收益基准)

测试 BK 列表 (覆盖 ai_chip / cpo / ai_app, 数据深度优先):
  ai_chip:    BK1036.DC 半导体 (行业, 1.5y)
              BK0917.DC 半导体概念 (概念, 9m)
  cpo:        BK0448.DC 通信设备 (行业, 1.5y)
              BK1136.DC 光通信模块 (概念, 9m)
  ai_app:     BK1037.DC 消费电子 (行业, 1.5y)
              BK0800.DC 人工智能 (概念, 9m)
              BK1134.DC 算力概念 (概念, 9m)

测试维度:
  1. main_net (大+超大): 5d/20d 累加 → step_20 阶梯打分 (复刻 sector_score)
  2. main_net 净流入率 (rate): 5d/20d 平均 net_amount_rate (已归一化 %)
  3. small_net (散户): 反指验证 — 散户大量买入应该后续跌
  4. main vs small 对比: 哪个对 T+20 的预测力强

输出:
  - 每个 BK 码的样本数 + score 分布
  - 总体 + 单 BK pearson r (main / small / net_amount_rate)
  - 分桶 forward returns
  - 与 ETF share-based 的 pearson r 对比表

CLI:
  python3 backtest_moneyflow.py
  python3 backtest_moneyflow.py --start 20240101 --end 20260331 --json > out.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_TUSHARE = _HERE / "tushare.py"


# AI 板块 → 东财 BK 映射 (优先深历史)
AI_BK_MAP = [
    {"bk": "BK1036.DC", "name": "半导体",        "kind": "行业", "sub_domain": "ai_chip"},
    {"bk": "BK0917.DC", "name": "半导体概念",    "kind": "概念", "sub_domain": "ai_chip"},
    {"bk": "BK0448.DC", "name": "通信设备",      "kind": "行业", "sub_domain": "cpo"},
    {"bk": "BK1136.DC", "name": "光通信模块",    "kind": "概念", "sub_domain": "cpo"},
    {"bk": "BK1037.DC", "name": "消费电子",      "kind": "行业", "sub_domain": "ai_app"},
    {"bk": "BK0800.DC", "name": "人工智能",      "kind": "概念", "sub_domain": "ai_app"},
    {"bk": "BK1134.DC", "name": "算力概念",      "kind": "概念", "sub_domain": "ai_app"},
]


# ─── tushare fetch ────────────────────────────────────────────────────────

def _tushare_csv(api: str, **params) -> list[dict[str, str]]:
    args = ["python3", str(_TUSHARE), api]
    for k, v in params.items():
        if k == "fields":
            args.append(f"--fields={v}")
        else:
            args.append(f"{k}={v}")
    args.append("--csv")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return []
    if r.returncode != 0 or not r.stdout.strip():
        return []
    return list(csv.DictReader(r.stdout.splitlines()))


def fetch_bk_moneyflow(bk: str) -> list[dict[str, Any]]:
    """Pull all moneyflow_ind_dc rows for one BK; return ASC by date."""
    rows = _tushare_csv("moneyflow_ind_dc", ts_code=bk)
    parsed = []
    for r in rows:
        try:
            parsed.append({
                "date": r["trade_date"],
                "close": float(r["close"]),
                "pct_change": float(r.get("pct_change") or 0),
                "net_amount": float(r.get("net_amount") or 0),
                "net_amount_rate": float(r.get("net_amount_rate") or 0),  # %
                "elg": float(r.get("buy_elg_amount") or 0),
                "elg_rate": float(r.get("buy_elg_amount_rate") or 0),
                "lg": float(r.get("buy_lg_amount") or 0),
                "lg_rate": float(r.get("buy_lg_amount_rate") or 0),
                "md": float(r.get("buy_md_amount") or 0),
                "sm": float(r.get("buy_sm_amount") or 0),
                "sm_rate": float(r.get("buy_sm_amount_rate") or 0),
            })
        except (KeyError, ValueError):
            continue
    parsed.sort(key=lambda x: x["date"])
    return parsed


# ─── 信号打分 ─────────────────────────────────────────────────────────────

def step_20(cny: float) -> float:
    """复刻 sector_score.fund_flow_score 阶梯映射 (单位 CNY 元)."""
    if cny >= 5e8: return 20
    if cny >= 1e8: return 16
    if cny >= 3e7: return 13
    if cny >= 0:   return 10
    if cny >= -3e7: return 7
    if cny >= -1e8: return 4
    if cny >= -5e8: return 2
    return 0


@dataclass
class BkSignal:
    bk: str
    sub_domain: str
    name: str
    date: str
    close: float

    # signals
    main_5d: float          # 5d 主力净额 (elg+lg) 累加
    main_20d: float         # 20d
    sm_5d: float            # 散户净额 5d
    sm_20d: float
    net_5d: float           # 总净额 5d
    net_20d: float
    rate_5d: float          # 5d 平均 net_amount_rate
    rate_20d: float

    # scores (用 step_20 阶梯)
    main_score: float       # step_20(main_5d) + step_20(main_20d), max 40
    sm_score: float         # 反指: 散户买多 = 卖信号
    net_score: float        # net_amount

    # forward returns (BK 指数价位)
    fwd_5d: float | None
    fwd_20d: float | None
    fwd_40d: float | None

    # filled later (relative to AI mean)
    fwd_5d_rel: float | None = None
    fwd_20d_rel: float | None = None
    fwd_40d_rel: float | None = None


def compute_bk_signal(rows: list[dict], idx: int, bk_meta: dict) -> BkSignal | None:
    """对 rows[idx] 那天计算大单信号 + 前瞻收益."""
    if idx < 20:
        return None  # 20d 累加窗口
    today = rows[idx]
    last_close = today["close"]
    if last_close <= 0:
        return None

    win5 = rows[idx - 4: idx + 1]
    win20 = rows[idx - 19: idx + 1]

    main_5d = sum(r["elg"] + r["lg"] for r in win5)
    main_20d = sum(r["elg"] + r["lg"] for r in win20)
    sm_5d = sum(r["sm"] for r in win5)
    sm_20d = sum(r["sm"] for r in win20)
    net_5d = sum(r["net_amount"] for r in win5)
    net_20d = sum(r["net_amount"] for r in win20)
    rate_5d = sum(r["net_amount_rate"] for r in win5) / 5
    rate_20d = sum(r["net_amount_rate"] for r in win20) / 20

    # forward
    def fwd(n: int) -> float | None:
        if idx + n < len(rows) and rows[idx + n]["close"] > 0:
            return (rows[idx + n]["close"] / last_close - 1) * 100
        return None

    return BkSignal(
        bk=bk_meta["bk"], sub_domain=bk_meta["sub_domain"], name=bk_meta["name"],
        date=today["date"], close=last_close,
        main_5d=round(main_5d, 0), main_20d=round(main_20d, 0),
        sm_5d=round(sm_5d, 0), sm_20d=round(sm_20d, 0),
        net_5d=round(net_5d, 0), net_20d=round(net_20d, 0),
        rate_5d=round(rate_5d, 3), rate_20d=round(rate_20d, 3),
        main_score=step_20(main_5d) + step_20(main_20d),
        sm_score=step_20(sm_5d) + step_20(sm_20d),
        net_score=step_20(net_5d) + step_20(net_20d),
        fwd_5d=round(fwd(5), 2) if fwd(5) is not None else None,
        fwd_20d=round(fwd(20), 2) if fwd(20) is not None else None,
        fwd_40d=round(fwd(40), 2) if fwd(40) is not None else None,
    )


# ─── 主流程 ────────────────────────────────────────────────────────────────

def run(start_date: str, end_date: str) -> dict[str, Any]:
    print(f"📥 加载 {len(AI_BK_MAP)} 个 BK moneyflow ...", file=sys.stderr)
    bk_data = {}
    for m in AI_BK_MAP:
        rows = fetch_bk_moneyflow(m["bk"])
        bk_data[m["bk"]] = {"meta": m, "rows": rows}
        if rows:
            print(f"  {m['bk']:<14} {m['name']:<14} {m['kind']:<6} {len(rows)} rows ({rows[0]['date']} ~ {rows[-1]['date']})",
                  file=sys.stderr)

    # walk
    all_signals: list[dict] = []
    per_bk_count: dict[str, int] = {}
    for bk, info in bk_data.items():
        cnt = 0
        for idx in range(len(info["rows"])):
            d = info["rows"][idx]["date"]
            if d < start_date or d > end_date:
                continue
            sig = compute_bk_signal(info["rows"], idx, info["meta"])
            if sig:
                all_signals.append(asdict(sig))
                cnt += 1
        per_bk_count[bk] = cnt

    print(f"\n📊 计算每日 AI 板块均值 (跨所有 BK 的均值) ...", file=sys.stderr)
    by_date: dict[str, dict[str, list[float]]] = {}
    for s in all_signals:
        d = s["date"]
        by_date.setdefault(d, {"5": [], "20": [], "40": []})
        for k, fld in [("5", "fwd_5d"), ("20", "fwd_20d"), ("40", "fwd_40d")]:
            if s[fld] is not None:
                by_date[d][k].append(s[fld])
    bm: dict[str, dict[str, float | None]] = {}
    for d, val in by_date.items():
        bm[d] = {k: (statistics.mean(v) if v else None) for k, v in val.items()}

    for s in all_signals:
        for k, fld_abs, fld_rel in [
            ("5", "fwd_5d", "fwd_5d_rel"),
            ("20", "fwd_20d", "fwd_20d_rel"),
            ("40", "fwd_40d", "fwd_40d_rel"),
        ]:
            base = bm.get(s["date"], {}).get(k)
            if s[fld_abs] is not None and base is not None:
                s[fld_rel] = round(s[fld_abs] - base, 2)
            else:
                s[fld_rel] = None

    return {
        "meta": {"start_date": start_date, "end_date": end_date,
                 "bk_count": len(AI_BK_MAP), "total_signals": len(all_signals)},
        "per_bk_count": per_bk_count,
        "all_signals": all_signals,
    }


# ─── 分析 ──────────────────────────────────────────────────────────────────

def stat(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(statistics.mean(vals), 2),
        "median": round(statistics.median(vals), 2),
        "win_rate": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
    }


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 30 or len(xs) != len(ys):
        return None
    mx = statistics.mean(xs); my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def correlation_table(signals: list[dict]) -> dict[str, Any]:
    """对每个信号字段, 测它和 fwd_5/20/40 的 pearson."""
    out = {}
    sig_fields = ["main_5d", "main_20d", "sm_5d", "sm_20d", "net_5d", "net_20d",
                  "rate_5d", "rate_20d", "main_score", "sm_score", "net_score"]
    fwd_fields = ["fwd_5d", "fwd_20d", "fwd_40d", "fwd_5d_rel", "fwd_20d_rel", "fwd_40d_rel"]
    for sf in sig_fields:
        out[sf] = {}
        for ff in fwd_fields:
            pairs = [(s[sf], s[ff]) for s in signals if s.get(ff) is not None]
            if len(pairs) < 30:
                out[sf][ff] = None
                continue
            xs, ys = zip(*pairs)
            r = pearson(list(xs), list(ys))
            out[sf][ff] = {"n": len(pairs), "r": round(r, 4) if r is not None else None}
    return out


def bucket_by_score(signals: list[dict], score_field: str, fwd_field: str) -> dict[str, Any]:
    """将信号按 score (40 max) 分桶, 看 forward 收益."""
    buckets = {"HIGH": [], "MID": [], "LOW": []}
    for s in signals:
        sc = s[score_field]
        if sc >= 30: buckets["HIGH"].append(s)
        elif sc >= 15: buckets["MID"].append(s)
        else: buckets["LOW"].append(s)
    out = {}
    for b, sub in buckets.items():
        vals = [x[fwd_field] for x in sub if x[fwd_field] is not None]
        out[b] = {"count": len(sub), **stat(vals)}
    return out


def bucket_by_subdomain(signals: list[dict], score_field: str) -> dict[str, Any]:
    out = {}
    by_sd: dict[str, list[dict]] = {}
    for s in signals:
        by_sd.setdefault(s["sub_domain"], []).append(s)
    for sd, sub in by_sd.items():
        out[sd] = {"n": len(sub)}
        for fwd in ("fwd_20d", "fwd_20d_rel"):
            out[sd][fwd] = bucket_by_score(sub, score_field, fwd)
    return out


def per_bk_correlation(signals: list[dict]) -> dict[str, Any]:
    """每个 BK 单独做相关性, 看 BK 之间一致性."""
    out = {}
    by_bk: dict[str, list[dict]] = {}
    for s in signals:
        by_bk.setdefault(s["bk"], []).append(s)
    for bk, sub in by_bk.items():
        if len(sub) < 30:
            continue
        info = {"n": len(sub), "name": sub[0]["name"], "sub_domain": sub[0]["sub_domain"]}
        for sf in ["main_score", "sm_score", "rate_20d"]:
            for ff in ["fwd_20d", "fwd_20d_rel"]:
                pairs = [(x[sf], x[ff]) for x in sub if x.get(ff) is not None]
                if len(pairs) < 30:
                    info[f"{sf}_{ff}_r"] = None
                    continue
                xs, ys = zip(*pairs)
                r = pearson(list(xs), list(ys))
                info[f"{sf}_{ff}_r"] = round(r, 4) if r is not None else None
        out[bk] = info
    return out


# ─── 报告 ──────────────────────────────────────────────────────────────────

def fmt(v) -> str:
    if v is None: return "  N/A"
    return f"{v:+6.2f}%"


def print_report(result: dict[str, Any]):
    sigs = result["all_signals"]
    m = result["meta"]
    print(f"\n{'='*100}")
    print(f"  大单/超大单 替代信号回测  ({m['start_date']} → {m['end_date']})")
    print(f"{'='*100}")
    print(f"  BK 数: {m['bk_count']}, 信号样本数: {m['total_signals']}")

    print(f"\n  per-BK 触发样本数:")
    for bk, n in result["per_bk_count"].items():
        print(f"    {bk:<14} {n} 天")

    # 分桶
    print(f"\n  ━━━ 按 main_score (大+超大单 5d+20d step_20, max 40) 分桶 ━━━")
    for fwd in ("fwd_5d", "fwd_20d", "fwd_40d"):
        bk = bucket_by_score(sigs, "main_score", fwd)
        bk_rel = bucket_by_score(sigs, "main_score", fwd + "_rel")
        for b in ("HIGH", "MID", "LOW"):
            a = bk.get(b, {}); rl = bk_rel.get(b, {})
            print(f"    {fwd:<10} {b:<6}  n={a.get('n','?'):>4}  abs mean={fmt(a.get('mean'))} win={a.get('win_rate','N/A')}%   rel mean={fmt(rl.get('mean'))} win={rl.get('win_rate','N/A')}%")
        print()

    print(f"  ━━━ 按 sm_score (散户净流入 5d+20d step_20) 分桶 — 反指验证 ━━━")
    for fwd in ("fwd_20d",):
        bk = bucket_by_score(sigs, "sm_score", fwd)
        bk_rel = bucket_by_score(sigs, "sm_score", fwd + "_rel")
        for b in ("HIGH", "MID", "LOW"):
            a = bk.get(b, {}); rl = bk_rel.get(b, {})
            print(f"    {fwd:<10} {b:<6}  n={a.get('n','?'):>4}  abs mean={fmt(a.get('mean'))} win={a.get('win_rate','N/A')}%   rel mean={fmt(rl.get('mean'))} win={rl.get('win_rate','N/A')}%")

    # 相关性
    print(f"\n  ━━━ Pearson 相关系数 (信号 vs 前瞻收益) ━━━")
    print(f"  正值 = 信号高→后续涨;  本回测期望: main_*/net_* 应为正, sm_* 为负")
    corr = correlation_table(sigs)
    print(f"  {'信号':<14}{'fwd_5d':>10}{'fwd_20d':>10}{'fwd_40d':>10}{'fwd_5_rel':>10}{'fwd_20_rel':>10}{'fwd_40_rel':>10}")
    for sf in ["main_5d", "main_20d", "main_score", "rate_5d", "rate_20d",
               "net_5d", "net_20d", "net_score", "sm_5d", "sm_20d", "sm_score"]:
        line = f"  {sf:<14}"
        for ff in ["fwd_5d", "fwd_20d", "fwd_40d", "fwd_5d_rel", "fwd_20d_rel", "fwd_40d_rel"]:
            d = corr[sf].get(ff)
            if d is None or d.get("r") is None:
                line += f"{'N/A':>10}"
            else:
                line += f"{d['r']:>10.4f}"
        print(line)

    # per-BK
    print(f"\n  ━━━ per-BK pearson r ━━━")
    pb = per_bk_correlation(sigs)
    print(f"  {'BK':<14}{'name':<14}{'sub_domain':<10}{'n':>5}  main_20d_r  rate_20d_r  sm_20d_r")
    for bk, info in pb.items():
        line = f"  {bk:<14}{info['name']:<14}{info['sub_domain']:<10}{info['n']:>5}"
        for sf in ["main_score", "rate_20d", "sm_score"]:
            r = info.get(f"{sf}_fwd_20d_rel_r")
            line += f"   {r:>8.4f}" if r is not None else "   N/A"
        print(line)

    # by sub_domain
    print(f"\n  ━━━ 按 sub_domain 切片 (main_score 分桶) ━━━")
    sd_bk = bucket_by_subdomain(sigs, "main_score")
    for sd, info in sd_bk.items():
        print(f"\n  · {sd} (n={info['n']}):")
        for fwd in ("fwd_20d", "fwd_20d_rel"):
            row = info.get(fwd, {})
            print(f"    {fwd:<14}", end="")
            for b in ("HIGH", "MID", "LOW"):
                d = row.get(b, {})
                print(f"  {b}: n={d.get('n',0):>3} mean={fmt(d.get('mean'))} win={d.get('win_rate','N/A')}%", end="")
            print()

    # 判定
    print(f"\n{'='*100}\n  判定\n{'='*100}")
    main_r = corr.get("main_score", {}).get("fwd_20d_rel", {})
    sm_r = corr.get("sm_score", {}).get("fwd_20d_rel", {})
    rate_r = corr.get("rate_20d", {}).get("fwd_20d_rel", {})

    if main_r and main_r.get("r") is not None:
        r = main_r["r"]
        print(f"  main_score → fwd_20d_rel pearson r = {r:+.4f}")
        if r > 0.10:
            print(f"     ✅ 主力大单是高质量正向信号 — 应替换 ETF share 当 fund_flow 数据源")
        elif r > 0.03:
            print(f"     ⚠️  主力大单是弱正向信号 — 比 ETF share (-0.07) 强, 但还不够")
        elif r > -0.03:
            print(f"     ⚠️  主力大单也无信号 (噪音区)")
        else:
            print(f"     ❌ 主力大单也反向 — 散户和机构在 AI 板块都被套住, 资金面整体失效")

    if sm_r and sm_r.get("r") is not None:
        r = sm_r["r"]
        print(f"  sm_score → fwd_20d_rel pearson r = {r:+.4f}")
        if r < -0.10:
            print(f"     ✅ 散户净流入显著反指 — 可作为 AVOID 信号源")
        elif r < -0.03:
            print(f"     ⚠️  散户弱反指")

    if rate_r and rate_r.get("r") is not None:
        r = rate_r["r"]
        print(f"  rate_20d (净流入率 %) → fwd_20d_rel pearson r = {r:+.4f}")

    # 跟 ETF share 对比 (硬编码 backtest_score_full 的结果)
    print(f"\n  ⟪ 对比基准: ETF share-based fund_flow (backtest_score_full) ⟫")
    print(f"     pearson r = -0.0681 (反向)")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20240101")
    ap.add_argument("--end", default="20260331")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--full-json", action="store_true")
    args = ap.parse_args()

    result = run(args.start, args.end)

    if args.full_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.json:
        slim = {k: v for k, v in result.items() if k != "all_signals"}
        slim["analyses"] = {
            "correlations": correlation_table(result["all_signals"]),
            "per_bk": per_bk_correlation(result["all_signals"]),
            "main_buckets_t20": bucket_by_score(result["all_signals"], "main_score", "fwd_20d_rel"),
            "by_subdomain": bucket_by_subdomain(result["all_signals"], "main_score"),
        }
        print(json.dumps(slim, ensure_ascii=False, indent=2))
        return

    print_report(result)


if __name__ == "__main__":
    main()
