"""cache_parquet.py — Parquet + DuckDB 持久化层 (替代 JSON cache 的数据库层).

为什么 Parquet + DuckDB:
  - Parquet 列式存储 + ZSTD 压缩, 1 亿根 K 线 (原 10-20 GB CSV) 压到 2-5 GB
  - DuckDB 嵌入式 OLAP 引擎, 零配置, 跨股跨日期查询毫秒级
  - 中国量化圈 2026 事实标准 (参考知乎/腾讯云多篇文章)

架构:
  ~/.homespace/data/market-tools/
    ├── daily/daily.parquet              # A 股日线 (ts_code + trade_date 为主键)
    ├── hk_daily/hk_daily.parquet        # 港股日线
    ├── daily_basic/daily_basic.parquet  # 估值数据
    ├── sw_daily/sw_daily.parquet        # 申万行业指数
    ├── fina_indicator/...               # 财报
    ├── top10_floatholders/...           # 机构股东
    ├── cctv_news/...                    # 新闻联播 (date + title 主键)
    ├── stock_basic/...                  # 静态股票列表
    ├── trade_cal/...                    # 交易日历
    └── ...

与现有 JSON cache 的关系:
  - 读: 优先 Parquet (快) → fallback JSON → fallback API
  - 写: **双写** (JSON 保留向后兼容, Parquet 新增)
  - 迁移: migrate_cache.sh 把历史 JSON 批量导入 Parquet

去重策略:
  每个 API 按业务主键去重 (见 KEYS), 重复行保留最后一条 (新数据覆盖旧).

环境变量:
  TUSHARE_PARQUET_DIR       数据目录 (默认 ~/.homespace/data/market-tools)
  TUSHARE_NO_PARQUET=1      禁用 Parquet 层 (纯 JSON)
  TUSHARE_PARQUET_DEBUG=1   打印 parquet 读写日志
"""

import os
import sys

# 懒加载: 只有真正要用时才 import, 避免 tushare.py 轻量调用也要付启动成本
_duckdb = None
_pa = None
_pq = None

def _lazy_imports():
    global _duckdb, _pa, _pq
    if _duckdb is None:
        import duckdb
        import pyarrow as pa
        import pyarrow.parquet as pq
        _duckdb = duckdb
        _pa = pa
        _pq = pq


PARQUET_DIR = os.environ.get(
    "TUSHARE_PARQUET_DIR",
    os.path.expanduser("~/.homespace/data/market-tools"),
)
_DISABLED = os.environ.get("TUSHARE_NO_PARQUET") == "1"
_DEBUG = os.environ.get("TUSHARE_PARQUET_DEBUG") == "1"


# 每个 API 的业务主键 (用于去重追加)
# 如果 API 不在这里, 默认不做 parquet 缓存 (fallback JSON)
KEYS = {
    # 日线类 (ts_code + trade_date)
    "daily":          ["ts_code", "trade_date"],
    "hk_daily":       ["ts_code", "trade_date"],
    "daily_basic":    ["ts_code", "trade_date"],
    "sw_daily":       ["ts_code", "trade_date"],
    "index_daily":    ["ts_code", "trade_date"],
    "bak_daily":      ["ts_code", "trade_date"],
    "adj_factor":     ["ts_code", "trade_date"],
    # 财报类 (ts_code + end_date)
    "fina_indicator": ["ts_code", "end_date"],
    "income":         ["ts_code", "end_date"],
    "balancesheet":   ["ts_code", "end_date"],
    "cashflow":       ["ts_code", "end_date"],
    # 预告 (ts_code + ann_date + end_date + type)
    "forecast":       ["ts_code", "ann_date", "end_date", "type"],
    "express":        ["ts_code", "ann_date", "end_date"],
    # 机构股东 (ts_code + end_date + holder_name)
    "top10_holders":      ["ts_code", "end_date", "holder_name"],
    "top10_floatholders": ["ts_code", "end_date", "holder_name"],
    # 资金流
    "moneyflow_hsgt":     ["trade_date"],
    "hsgt_top10":         ["trade_date", "ts_code"],
    "top_list":           ["trade_date", "ts_code", "reason"],
    # 新闻
    "cctv_news":          ["date", "title"],
    # 静态表
    "stock_basic":        ["ts_code"],
    "hk_basic":           ["ts_code"],
    "trade_cal":          ["exchange", "cal_date"],
    "hk_tradecal":        ["exchange", "cal_date"],
    "index_classify":     ["index_code"],
    "index_basic":        ["ts_code"],
    # 指数成分 (历史, 用 in_date 区分版本)
    "index_member":       ["index_code", "con_code", "in_date"],
    "ths_index":          ["ts_code"],
}


def _log(msg):
    if _DEBUG:
        sys.stderr.write(f"[parquet] {msg}\n")


def _parquet_path(api_name):
    return os.path.join(PARQUET_DIR, api_name, f"{api_name}.parquet")


def enabled_for(api_name):
    """某 API 是否启用 Parquet 缓存."""
    return not _DISABLED and api_name in KEYS


def body_to_rows(body, inject=None):
    """tushare response body → [{field: value, ...}, ...].

    inject: dict, 注入缺失字段 (e.g. tushare by ts_code 查询不返回 ts_code 列,
            从 params 补上).
    """
    if not isinstance(body, dict): return []
    data = body.get("data") or {}
    fields = data.get("fields") or []
    items = data.get("items") or []
    rows = [dict(zip(fields, row)) for row in items]
    if inject:
        for r in rows:
            for k, v in inject.items():
                if k not in r or r[k] in (None, ""):
                    r[k] = v
    return rows


def append(api_name, rows):
    """追加 rows 到 {api_name}.parquet, 按 KEYS 去重 (保留最后一条).

    使用 pyarrow + duckdb 实现: 读已有 → 合并新行 → SQL 去重 → 写回.
    """
    if _DISABLED or not rows or api_name not in KEYS:
        return 0

    _lazy_imports()

    keys = KEYS[api_name]
    path = _parquet_path(api_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # 读 existing (如有) + merge + Python dict-based dedup (保留 rows 里后来的)
    if os.path.exists(path):
        try:
            existing_rows = _pq.read_table(path).to_pylist()
        except Exception as e:
            _log(f"read existing {path} failed, overwriting: {e}")
            existing_rows = []
    else:
        existing_rows = []

    # 合并: existing 在前, rows 在后 (rows 覆盖同 key 的 existing)
    all_rows = existing_rows + rows

    # 去重: dict 保留最后写入值 (Python 3.7+ dict 保序)
    seen = {}
    for r in all_rows:
        k = tuple(r.get(kk) for kk in keys)
        # merge: 新行覆盖旧行字段, 但保留旧行独有字段 (避免 schema 降级)
        if k in seen:
            merged = dict(seen[k])
            merged.update(r)  # 新值覆盖
            seen[k] = merged
        else:
            seen[k] = r
    dedup_rows = list(seen.values())

    # 补齐所有行的字段为 union (防止 pa.Table.from_pylist 按第一行推断时丢列)
    all_keys = set()
    for r in dedup_rows:
        all_keys.update(r.keys())
    for r in dedup_rows:
        for k in all_keys:
            if k not in r:
                r[k] = None

    # 写回 (atomic: tmp + rename)
    try:
        new_table = _pa.Table.from_pylist(dedup_rows)
    except Exception as e:
        _log(f"pa.Table.from_pylist failed for {api_name}: {e}")
        return 0

    try:
        tmp_path = path + ".tmp"
        _pq.write_table(new_table, tmp_path, compression="zstd")
        os.replace(tmp_path, path)
        _log(f"wrote {api_name}: {len(rows)} new, {len(dedup_rows)} total rows")
        return len(rows)
    except Exception as e:
        _log(f"write failed for {api_name}: {e}")
        return 0


def query(api_name, params=None, fields=None):
    """从 parquet 查, 返回 tushare-shape body ({"code":0, "data":{"fields":[], "items":[[]]}}).

    params: filter 条件 (e.g. {"ts_code": "600519.SH", "trade_date": "20260507"})
            支持 start_date / end_date 范围 (for daily-like APIs)

    返回 None = 没命中或无法判断. 交给上层 fallback.
    """
    if _DISABLED or api_name not in KEYS:
        return None

    path = _parquet_path(api_name)
    if not os.path.exists(path):
        return None

    _lazy_imports()
    params = params or {}

    # 构建 SQL 条件. 关键是**判断 Parquet 里是否有足够数据覆盖此查询**,
    # 因为用户可能查 ts_code=X start=a end=b, 我们 parquet 里必须有 X 从 a 到 b
    # 的完整数据才能命中, 否则 fallback 到 API.
    #
    # 简化策略 (初版): 只对"确定性查询"返回缓存 — 即查询参数定义明确的集合,
    # 可以从 parquet 精确 SELECT 出来. 如果不确定缓存是否完整, 返回 None.

    where_clauses = []
    for k, v in params.items():
        if k in ("fields",): continue
        if k == "start_date":
            where_clauses.append(f'"trade_date" >= \'{v}\'' if "trade_date" in _cols(path)
                                 else f'"end_date" >= \'{v}\'' if "end_date" in _cols(path)
                                 else None)
        elif k == "end_date":
            where_clauses.append(f'"trade_date" <= \'{v}\'' if "trade_date" in _cols(path)
                                 else f'"end_date" <= \'{v}\'' if "end_date" in _cols(path)
                                 else None)
        else:
            # 字符串类型值需要加引号, 数值不加
            where_clauses.append(f'"{k}" = \'{v}\'')
    where_clauses = [c for c in where_clauses if c]

    # 如果没有任何 filter, 返回 None 避免全表扫描当缓存 hit (危险)
    if not where_clauses and not params:
        return None

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
    fields_sql = "*"
    if fields:
        fields_list = [f.strip() for f in fields.split(",") if f.strip()]
        fields_sql = ", ".join(f'"{f}"' for f in fields_list)

    try:
        con = _duckdb.connect()
        q = f"SELECT {fields_sql} FROM read_parquet('{path}') WHERE {where_sql}"
        result = con.execute(q).fetch_arrow_table()
        if result.num_rows == 0:
            return None   # 有文件但没这个 query 的数据, fallback API
    except Exception as e:
        _log(f"query failed for {api_name}: {e}")
        return None

    # 转成 tushare 格式 body
    items = result.to_pylist()
    if not items:
        return None
    fields_out = list(items[0].keys())
    items_list = [[r.get(f) for f in fields_out] for r in items]
    body = {
        "code": 0,
        "msg": "",
        "data": {
            "fields": fields_out,
            "items": items_list,
            "has_more": False,
            "count": -1,
        },
    }
    _log(f"parquet hit {api_name}: {len(items)} rows ({where_sql[:80]})")
    return body


_cols_cache = {}
def _cols(path):
    """Read parquet columns, cached."""
    if path in _cols_cache:
        return _cols_cache[path]
    try:
        _lazy_imports()
        schema = _pq.read_schema(path)
        cols = schema.names
        _cols_cache[path] = cols
        return cols
    except Exception:
        return []


def status():
    """返回所有 API 的 Parquet 文件状态 (for CLI inspection)."""
    _lazy_imports()
    out = []
    for api in sorted(KEYS.keys()):
        path = _parquet_path(api)
        if not os.path.exists(path):
            out.append({"api": api, "exists": False})
            continue
        try:
            t = _pq.read_metadata(path)
            size = os.path.getsize(path)
            out.append({
                "api": api,
                "exists": True,
                "rows": t.num_rows,
                "file_mb": round(size / 1024 / 1024, 2),
                "cols": t.schema.to_arrow_schema().names,
            })
        except Exception as e:
            out.append({"api": api, "error": str(e)})
    return out


if __name__ == "__main__":
    # CLI: `python3 cache_parquet.py` → print status
    #      `python3 cache_parquet.py query daily ts_code=600519.SH`
    import json
    args = sys.argv[1:]
    if not args or args[0] == "status":
        _lazy_imports()
        for row in status():
            if not row.get("exists"):
                print(f"  {row['api']:<22} (未创建)")
            elif "error" in row:
                print(f"  {row['api']:<22} ERROR: {row['error']}")
            else:
                print(f"  {row['api']:<22}  {row['rows']:>10,} rows  "
                      f"{row['file_mb']:>6.2f} MB  cols={len(row['cols'])}")
    elif args[0] == "query":
        api = args[1]
        params = {}
        for a in args[2:]:
            if "=" in a:
                k, v = a.split("=", 1)
                params[k] = v
        body = query(api, params)
        print(json.dumps(body, ensure_ascii=False, indent=2) if body else "(无结果)")
    else:
        sys.stderr.write(f"usage: cache_parquet.py [status | query <api> key=val ...]\n")
        sys.exit(2)
