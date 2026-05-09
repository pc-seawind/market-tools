#!/usr/bin/env python3
"""fina_sync.py — 批量预热全市场 fina_indicator 到 Parquet.

为什么需要:
  grading.py 的基本面维度要读 ROE / 净利 YoY 等数据, 这些在 tushare
  的 fina_indicator API, 按 ts_code per-stock 查. 选股工具每次都实时
  拉 300 只候选 × 1 次调用 = 300 次 call, 慢且浪费配额.

  解决: 一次性批量预热所有 A 股最近 2 年的 fina_indicator 到 Parquet,
  每次选股时从 Parquet SQL 查 (毫秒级), 每月或每季运行一次 sync.

Usage:
  python3 fina_sync.py                       # 拉市值 top 1000 A 股
  python3 fina_sync.py --limit=5500          # 全市场
  python3 fina_sync.py --limit=300 --codes=600519.SH,000858.SZ  # 指定股

Env:
  TUSHARE_TOKEN required. tushare fina_indicator 免费配额足够.
"""

import csv, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).parent
TUSHARE = str(HERE / "tushare.py")

DEFAULT_LIMIT = 1000   # 市值前 1000 覆盖 95% 选股候选

START_DATE = "20240101"   # 近 5-6 期报告
FIELDS = "ts_code,end_date,roe,roa,netprofit_yoy,or_yoy,grossprofit_margin,netprofit_margin,debt_to_assets"


def tushare(api, timeout=30, **params):
    args = ["python3", TUSHARE, api]
    for k, v in params.items():
        if k == "fields": args.append(f"--fields={v}")
        else: args.append(f"{k}={v}")
    args.append("--csv")
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    return list(csv.DictReader(out.stdout.splitlines()))


def main():
    limit = DEFAULT_LIMIT
    explicit_codes = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="): limit = int(a.split("=", 1)[1])
        elif a.startswith("--codes="): explicit_codes = a.split("=", 1)[1].split(",")
        elif a in ("-h", "--help"):
            print(__doc__); sys.exit(0)

    # 1. 拉所有 A 股列表 (附带日成交拉的 daily_basic)
    if explicit_codes:
        codes_to_sync = explicit_codes
    else:
        import datetime
        today = datetime.date.today().strftime("%Y%m%d")
        past15 = (datetime.date.today() - datetime.timedelta(days=15)).strftime("%Y%m%d")
        cal = tushare("trade_cal", exchange="SSE", start_date=past15, end_date=today,
                      fields="cal_date,is_open")
        open_days = sorted([r["cal_date"] for r in (cal or []) if r.get("is_open") == "1"],
                           reverse=True)
        latest = open_days[0] if open_days else today
        db = tushare("daily_basic", trade_date=latest,
                     fields="ts_code,total_mv")
        if not db:
            # T-1
            if len(open_days) > 1:
                latest = open_days[1]
                db = tushare("daily_basic", trade_date=latest,
                             fields="ts_code,total_mv")
        if not db:
            sys.stderr.write("ERROR: daily_basic 无数据, 无法定位市值 top\n")
            sys.exit(1)
        # 按市值排序取 top N
        db_sorted = sorted(db, key=lambda r: -float(r.get("total_mv") or 0))
        codes_to_sync = [r["ts_code"] for r in db_sorted[:limit]]

    print(f"将 sync {len(codes_to_sync)} 只 A 股 fina_indicator (近 2 年)")
    print(f"start_date={START_DATE}, fields={FIELDS[:60]}...")
    print()

    t0 = time.time()
    ok = skipped = fail = 0
    for i, code in enumerate(codes_to_sync, 1):
        out = subprocess.run(
            ["python3", TUSHARE, "fina_indicator",
             f"ts_code={code}", f"start_date={START_DATE}",
             f"--fields={FIELDS}", "--csv"],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode == 0:
            rows = len(out.stdout.strip().split("\n")) - 1  # minus header
            if rows > 0:
                ok += 1
            else:
                skipped += 1
        else:
            fail += 1

        # 每 50 只报告一次
        if i % 50 == 0 or i == len(codes_to_sync):
            elapsed = time.time() - t0
            rate = i / elapsed
            eta = (len(codes_to_sync) - i) / rate if rate > 0 else 0
            sys.stderr.write(
                f"  [{i:>4}/{len(codes_to_sync)}] "
                f"ok={ok} skipped={skipped} fail={fail}  "
                f"{rate:.1f} 只/s  ETA {eta:.0f}s\n"
            )

    total_t = time.time() - t0
    print(f"\n✅ sync 完成: {ok} 写入, {skipped} 空数据, {fail} 失败. 耗时 {total_t:.0f}s")
    print(f"   现在 parquet 里有约 {ok * 5} 行 fina_indicator 数据 (每股 ~5 期)")


if __name__ == "__main__":
    main()
