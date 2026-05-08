#!/usr/bin/env bash
# migrate_cache.sh — 把 JSON cache 里已有的数据批量导入 Parquet.
#
# 用途: 第一次启用 Parquet 层时, 把过去几天/几周积累的 JSON cache 直接导入
#       Parquet, 避免"只有新数据进 Parquet, 历史还在 JSON 里"的割裂状态.
#
# 工作方式:
#   1. 扫描 ~/.homespace/cache/market-tools/<api>/*.json
#   2. 对每个 JSON 文件, 读取 body, 调用 cache_parquet.append() 写入对应表
#   3. 跳过 negative cache (有 _neg_ttl 字段的错误缓存)
#   4. 跳过 cache_parquet.py 里没定义 schema 的 API (e.g. stock_basic 暂无)
#
# 幂等: 重跑不会破坏数据 (Parquet 层会按主键去重, 相同数据覆盖自己).
#
# Usage:
#   migrate_cache.sh [--dry-run]
#   migrate_cache.sh [--api=daily,hk_daily]      # 只迁移指定 API
#
# Env:
#   TUSHARE_CACHE_DIR        JSON cache 源 (默认 ~/.homespace/cache/market-tools)
#   TUSHARE_PARQUET_DIR      Parquet 目标 (默认 ~/.homespace/data/market-tools)

set -uo pipefail

here="$(dirname "$(readlink -f "$0")")"

exec python3 - "$here" "$@" <<'PY'
import json
import os
import sys

here = sys.argv[1]
raw_args = sys.argv[2:]

dry_run = False
only_apis = None
for arg in raw_args:
    if arg == "--dry-run":
        dry_run = True
    elif arg.startswith("--api="):
        only_apis = set(arg[6:].split(","))
    elif arg in ("-h", "--help"):
        print(__doc__ if __doc__ else "see script header")
        sys.exit(0)
    else:
        sys.stderr.write(f"unknown arg: {arg}\n"); sys.exit(2)

sys.path.insert(0, here)
import cache_parquet as cp

JSON_CACHE = os.environ.get(
    "TUSHARE_CACHE_DIR",
    os.path.expanduser("~/.homespace/cache/market-tools"),
)

if not os.path.exists(JSON_CACHE):
    sys.stderr.write(f"JSON cache 目录不存在: {JSON_CACHE}\n")
    sys.exit(1)

# 扫描 <api>/*.json
total_files = 0
total_rows = 0
skipped_neg = 0
skipped_noschema = 0
errors = 0

print(f"扫描 JSON cache: {JSON_CACHE}")
print(f"目标 Parquet:   {cp.PARQUET_DIR}")
if dry_run:
    print("[DRY RUN] 只模拟, 不实际写入")
if only_apis:
    print(f"只处理 API: {sorted(only_apis)}")
print()

by_api = {}
for api_name in sorted(os.listdir(JSON_CACHE)):
    api_dir = os.path.join(JSON_CACHE, api_name)
    if not os.path.isdir(api_dir): continue
    if only_apis and api_name not in only_apis: continue
    if not cp.enabled_for(api_name):
        skipped_noschema += 1
        print(f"  {api_name:<22} (skip: no parquet schema)")
        continue

    json_files = [f for f in os.listdir(api_dir) if f.endswith(".json")]
    api_rows = 0
    api_files = 0
    for jf in json_files:
        path = os.path.join(api_dir, jf)
        try:
            with open(path, "r", encoding="utf-8") as f:
                body = json.load(f)
        except Exception as e:
            errors += 1
            continue
        # 跳过 negative cache (错误响应)
        if "_neg_ttl" in body or body.get("code", 0) != 0:
            skipped_neg += 1
            continue
        data = body.get("data") or {}
        if not data.get("items"):
            continue
        # 注入 ts_code 如果 body 的 cache key 里有, 但数据里没有
        # 这个信息我们不知道 (cache key 是 hash), 所以只能从 body 读原样.
        # 单股查询的结果 ts_code 可能缺失 — 我们在 daily.sh 里已注入, 重新
        # 拉过的数据应该已包含. 历史 JSON 可能缺, 但不影响已有 ts_code 的.
        rows = cp.body_to_rows(body)
        if not rows:
            continue
        api_rows += len(rows)
        api_files += 1
        if not dry_run:
            cp.append(api_name, rows)

    if api_files > 0:
        by_api[api_name] = (api_files, api_rows)
        total_files += api_files
        total_rows += api_rows

# 输出汇总
print(f"\n✅ 迁移完成{'（DRY RUN）' if dry_run else ''}")
print(f"   处理了 {total_files} 个 JSON 文件")
print(f"   导入了 {total_rows:,} 行数据")
print(f"   跳过 {skipped_neg} 个 negative cache / 空结果")
print(f"   跳过 {skipped_noschema} 个无 schema 的 API")
if errors:
    print(f"   {errors} 个文件读取失败")

print(f"\n各 API 详情:")
for api, (f, r) in sorted(by_api.items()):
    print(f"  {api:<22}  {f:>5} 文件  →  {r:>10,} 行")

print(f"\n下一步: 查看 Parquet 状态")
print(f"  python3 {here}/cache_parquet.py status")
PY
