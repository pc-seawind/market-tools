#!/usr/bin/env python3
"""quote_sources.py — 统一行情数据源层 (mootdx / 腾讯财经 / 同花顺热点).

三大数据源, 各有侧重:
  1. mootdx     — 实时盘口 (买卖5档) + 分钟/日K线 + 分时线. TCP 直连通达信.
  2. tencent    — 估值快照 (PE/PB/市值/换手/涨跌停价). HTTP 公开, 无需 token.
  3. ths_concept — 同花顺概念板块指数 (K线/成分股). 通过 akshare 间接调用.

对外暴露 4 个高层函数 + 1 个 CLI:
  realtime_quotes(codes)       → 实时价格 + 盘口 (mootdx primary, tencent fallback)
  valuation_snapshot(codes)    → PE/PB/市值/换手/涨跌停 (tencent)
  daily_bars(code, days=20)    → 日K线 (A股: mootdx→tencent; 港股: tencent)
  concept_index(name, days=5)  → 概念板块近 N 日 K线 (ths via akshare)

CLI:
  python quote_sources.py quote 600519 002475 301308   # 实时行情
  python quote_sources.py val   600519 002475          # 估值快照
  python quote_sources.py daily 00700.HK --days 10     # 日K线 (A/HK通用)
  python quote_sources.py concept AI手机               # 概念K线
  python quote_sources.py bars 600519 --freq 5min --count 20  # 分钟K线

依赖:
  mootdx (pip install mootdx) — 实时行情
  akshare — 同花顺概念板块
  urllib (stdlib) — 腾讯财经
"""

import os
import sys
import json
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

# 清掉环境代理 — 避免 stale SOCKS 截断 akshare/mootdx 连接.
# 所有数据源都是公网 API, 直连即可.
if os.environ.get("QUOTE_SOURCES_USE_PROXY") != "1":
    for _v in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
               "https_proxy", "http_proxy", "all_proxy"):
        os.environ.pop(_v, None)

# ═══════════════════════════════════════════════════════
# 1. mootdx — 实时盘口 + K线
# ═══════════════════════════════════════════════════════

_mootdx_client = None

def _get_mootdx():
    """Lazy-init mootdx client (TCP connection reuse)."""
    global _mootdx_client
    if _mootdx_client is None:
        try:
            from mootdx.quotes import Quotes
            _mootdx_client = Quotes.factory(market='std')
        except ImportError:
            raise ImportError("mootdx not installed. pip install mootdx")
    return _mootdx_client


def _normalize_code_mootdx(code: str) -> str:
    """Convert '600519.SH' / 'sz002475' / '002475.SZ' → '600519'/'002475' (pure 6-digit)."""
    code = code.strip().upper()
    # Strip market suffix
    for suffix in ('.SH', '.SZ', '.BJ'):
        if code.endswith(suffix):
            code = code[:-3]
            break
    # Strip market prefix
    for prefix in ('SH', 'SZ', 'BJ'):
        if code.startswith(prefix) and len(code) > 2:
            code = code[2:]
            break
    return code


def _is_hk_code(code: str) -> bool:
    """判断是否港股代码."""
    code = code.strip().upper()
    return code.endswith('.HK') or code.startswith('HK')


def realtime_quotes(codes: List[str]) -> List[Dict[str, Any]]:
    """获取实时行情 (mootdx for A-shares, tencent for HK + fallback).

    mootdx 仅支持 A 股 (SH/SZ/BJ), 港股走腾讯财经.
    返回 list of dict, 每个含:
      code, name, price, open, high, low, prev_close, volume(手), amount(元),
      bid1..bid5, ask1..ask5, bid_vol1..bid_vol5, ask_vol1..ask_vol5
    """
    a_codes = [c for c in codes if not _is_hk_code(c)]
    hk_codes = [c for c in codes if _is_hk_code(c)]

    results = []

    # A 股: mootdx primary → tencent fallback
    if a_codes:
        try:
            results.extend(_realtime_mootdx(a_codes))
        except Exception as e:
            print(f"[quote_sources] mootdx failed ({e}), falling back to tencent for A-shares", file=sys.stderr)
            results.extend(_realtime_tencent(a_codes))

    # 港股: 只能走腾讯
    if hk_codes:
        results.extend(_realtime_tencent(hk_codes))

    return results


def _realtime_mootdx(codes: List[str]) -> List[Dict[str, Any]]:
    client = _get_mootdx()
    symbols = [_normalize_code_mootdx(c) for c in codes]
    df = client.quotes(symbol=symbols)
    results = []
    for _, row in df.iterrows():
        price = float(row['price'])
        prev = float(row.get('last_close', 0))
        chg_pct = ((price - prev) / prev * 100) if prev > 0 else None
        r = {
            'code': row['code'],
            'name': row.get('name', ''),
            'price': price,
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'prev_close': prev,
            'volume': int(row['vol']),
            'amount': float(row.get('amount', 0)),
            'change_pct': round(chg_pct, 2) if chg_pct is not None else None,
        }
        # 5档盘口
        for i in range(1, 6):
            r[f'bid{i}'] = float(row.get(f'bid{i}', 0))
            r[f'ask{i}'] = float(row.get(f'ask{i}', 0))
            r[f'bid_vol{i}'] = int(row.get(f'bid_vol{i}', 0))
            r[f'ask_vol{i}'] = int(row.get(f'ask_vol{i}', 0))
        results.append(r)
    return results


def bars(code: str, frequency: str = 'daily', count: int = 20) -> List[Dict]:
    """获取K线数据 (mootdx).

    frequency: '5min'|'15min'|'30min'|'60min'|'daily'|'weekly'|'monthly'
    返回 list of dict: [{datetime, open, high, low, close, volume, amount}, ...]
    """
    freq_map = {
        '5min': 0, '15min': 1, '30min': 2, '60min': 3,
        'daily': 9, 'weekly': 10, 'monthly': 11,
    }
    freq_code = freq_map.get(frequency, 9)
    client = _get_mootdx()
    symbol = _normalize_code_mootdx(code)
    df = client.bars(symbol=symbol, frequency=freq_code, offset=count)
    results = []
    for idx, row in df.iterrows():
        results.append({
            'datetime': str(idx),
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close']),
            'volume': int(row['vol']),
            'amount': float(row['amount']),
        })
    return results


# ═══════════════════════════════════════════════════════
# 2. 腾讯财经 — 估值快照
# ═══════════════════════════════════════════════════════

def _tencent_code(code: str) -> str:
    """Convert to tencent format: sh600519 / sz002475 / hk00700."""
    code = code.strip()
    # Already in tencent format
    if code.startswith(('sh', 'sz', 'hk', 'SH', 'SZ', 'HK')) and code[2:].isdigit():
        return code.lower()
    # tushare format: 600519.SH → sh600519
    if '.' in code:
        num, market = code.split('.')
        market = market.upper()
        if market == 'SH':
            return f'sh{num}'
        elif market == 'SZ':
            return f'sz{num}'
        elif market == 'HK':
            return f'hk{num}'
    # Bare number: guess by prefix
    num = code.lstrip('0') if len(code) == 6 else code
    if code.startswith(('6', '9', '5')):
        return f'sh{code}'
    elif code.startswith(('0', '3', '2', '1')):
        return f'sz{code}'
    elif code.startswith('4') or code.startswith('8'):
        return f'bj{code}'
    return f'sh{code}'  # default


def _parse_tencent_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse one line from qt.gtimg.cn response."""
    if '~' not in line:
        return None
    parts = line.split('~')
    if len(parts) < 50:
        return None

    # 判断是否港股 (field[0] 含 "hk")
    is_hk = 'hk' in parts[0].lower()

    # A股字段映射 (88-field 格式, 2026-05 实测):
    # [0]=v_shXXXXXX="1  [1]=name  [2]=code  [3]=price  [4]=prev_close
    # [5]=open  [6]=volume(手)  [33]=high  [34]=low
    # [36]=volume(手)  [37]=amount(万)  [38]=amplitude%
    # [39]=pe_ttm  [43]=turnover_rate%
    # [44]=total_mkt_cap(亿)  [45]=flow_mkt_cap(亿)  [46]=pb
    # [47]=limit_up  [48]=limit_down
    #
    # 港股字段映射 (78-field 格式):
    # [0..5] 同上  [33]=high  [34]=low
    # [39]=pe_ttm  [43]=turnover_rate%
    # [44]=total_mkt_cap(亿)  [45]=flow_mkt_cap(亿)
    # [46]=英文公司名(非PB!)  [47]=pb  [48]=52w_high  [49]=52w_low
    # 港股无涨跌停价
    try:
        result = {
            'code': parts[2],
            'name': parts[1],
            'price': _safe_float(parts[3]),
            'prev_close': _safe_float(parts[4]),
            'open': _safe_float(parts[5]),
            'high': _safe_float(parts[33]),
            'low': _safe_float(parts[34]),
            'volume': _safe_int(parts[36]),  # 手
            'amount': _safe_float(parts[37]),  # 万元
            'change_pct': _safe_float(parts[32]),
            'amplitude': _safe_float(parts[38]),
            'pe_ttm': _safe_float(parts[39]),
            'turnover_rate': _safe_float(parts[43]),
            'total_mkt_cap': _safe_float(parts[44]),  # 亿
            'flow_mkt_cap': _safe_float(parts[45]),   # 亿
        }
        if is_hk:
            result['pb'] = _safe_float(parts[47])
            result['limit_up'] = None  # 港股无涨跌停
            result['limit_down'] = None
        else:
            result['pb'] = _safe_float(parts[46])
            result['limit_up'] = _safe_float(parts[47])
            result['limit_down'] = _safe_float(parts[48])
        return result
    except (IndexError, ValueError):
        return None


def _safe_float(s: str) -> Optional[float]:
    try:
        v = float(s)
        return v if v != 0 else None
    except (ValueError, TypeError):
        return None


def _safe_int(s: str) -> Optional[int]:
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _realtime_tencent(codes: List[str]) -> List[Dict[str, Any]]:
    """Tencent Finance as realtime fallback."""
    tc_codes = [_tencent_code(c) for c in codes]
    url = f'http://qt.gtimg.cn/q={",".join(tc_codes)}'
    req = urllib.request.Request(url)
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode('gbk', errors='replace')
    results = []
    for line in data.strip().split('\n'):
        parsed = _parse_tencent_line(line)
        if parsed:
            results.append(parsed)
    return results


def valuation_snapshot(codes: List[str]) -> List[Dict[str, Any]]:
    """估值快照 — PE(TTM) / PB / 总市值 / 流通市值 / 换手率 / 涨跌停价.

    数据源: 腾讯财经 (免费, 无配额限制, 实时).
    返回 list of dict.
    """
    tc_codes = [_tencent_code(c) for c in codes]
    # 批量请求 (腾讯支持一次最多约 50 只)
    batch_size = 50
    results = []
    for i in range(0, len(tc_codes), batch_size):
        batch = tc_codes[i:i+batch_size]
        url = f'http://qt.gtimg.cn/q={",".join(batch)}'
        req = urllib.request.Request(url)
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            data = resp.read().decode('gbk', errors='replace')
            for line in data.strip().split('\n'):
                parsed = _parse_tencent_line(line)
                if parsed:
                    results.append({
                        'code': parsed['code'],
                        'name': parsed['name'],
                        'price': parsed['price'],
                        'pe_ttm': parsed['pe_ttm'],
                        'pb': parsed['pb'],
                        'total_mkt_cap': parsed['total_mkt_cap'],
                        'flow_mkt_cap': parsed['flow_mkt_cap'],
                        'turnover_rate': parsed['turnover_rate'],
                        'limit_up': parsed['limit_up'],
                        'limit_down': parsed['limit_down'],
                        'change_pct': parsed['change_pct'],
                    })
        except Exception as e:
            print(f"[quote_sources] tencent batch error: {e}", file=sys.stderr)
    return results


# ═══════════════════════════════════════════════════════
# 2b. 腾讯财经 — 港股/A股日K线 (kline API)
# ═══════════════════════════════════════════════════════

def _tencent_daily_kline(code: str, days: int = 20) -> List[Dict]:
    """从腾讯 kline API 获取日K线 (复权).

    支持 A 股和港股. 返回 [{date, open, close, high, low, volume}, ...]
    """
    tc_code = _tencent_code(code)
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc_code},day,,,{days},qfq"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=15)
        import json as _json
        data = _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[quote_sources] tencent kline failed for {tc_code}: {e}", file=sys.stderr)
        return []

    inner = data.get("data", {}).get(tc_code, {})
    day_data = inner.get("qfqday") or inner.get("day")
    if not day_data:
        return []

    results = []
    for bar in day_data:
        # bar = ["2026-05-12", "462.000", "457.200", "469.000", "457.200", "32469707.000"]
        # fields: date, open, close, high, low, volume
        if len(bar) < 6:
            continue
        results.append({
            "date": bar[0],
            "open": float(bar[1]) if bar[1] else 0,
            "close": float(bar[2]) if bar[2] else 0,
            "high": float(bar[3]) if bar[3] else 0,
            "low": float(bar[4]) if bar[4] else 0,
            "volume": int(float(bar[5])) if bar[5] else 0,
        })
    return results


def daily_bars(code: str, days: int = 20) -> List[Dict]:
    """统一日K线接口 — 自动选源, 盘中/盘后都能用.

    路由策略:
      A 股: mootdx (daily bars) primary → tencent kline fallback
      港股: tencent kline (唯一实时源)

    返回 [{date, open, high, low, close, volume}, ...] 按日期升序.
    """
    is_hk = _is_hk_code(code)

    if not is_hk:
        # A 股: try mootdx first
        try:
            raw = bars(code, frequency='daily', count=days)
            if raw:
                # Normalize datetime field → date
                results = []
                for r in raw:
                    dt_str = r["datetime"]
                    # mootdx datetime format: "2026-05-15 00:00:00" or "2026-05-15"
                    date_str = dt_str[:10] if len(dt_str) >= 10 else dt_str
                    results.append({
                        "date": date_str,
                        "open": r["open"],
                        "high": r["high"],
                        "low": r["low"],
                        "close": r["close"],
                        "volume": r["volume"],
                    })
                return results
        except Exception as e:
            print(f"[quote_sources] mootdx daily_bars failed for {code}: {e}", file=sys.stderr)

    # Fallback (A股) / Primary (港股): tencent kline
    return _tencent_daily_kline(code, days)


# ═══════════════════════════════════════════════════════
# 3. 同花顺概念板块 (via akshare)
# ═══════════════════════════════════════════════════════

_ak = None

def _lazy_ak():
    global _ak
    if _ak is None:
        import akshare as ak
        _ak = ak
    return _ak


def concept_list() -> List[Dict[str, str]]:
    """获取同花顺全部概念板块列表.

    返回 [{name, code}, ...]
    """
    ak = _lazy_ak()
    df = ak.stock_board_concept_name_ths()
    return [{'name': row['name'], 'code': row['code']} for _, row in df.iterrows()]


def concept_index(name: str, days: int = 5) -> List[Dict]:
    """获取同花顺概念板块指数K线 (近 N 天).

    参数:
      name: 概念名称 (如 'AI手机', '白酒概念')
      days: 获取最近 N 天

    返回 [{日期, 开盘价, 最高价, 最低价, 收盘价, 成交量, 成交额}, ...]
    """
    ak = _lazy_ak()
    end = datetime.now()
    start = end - timedelta(days=days + 10)  # 多取几天以确保有交易日
    start_str = start.strftime('%Y%m%d')
    end_str = end.strftime('%Y%m%d')

    df = ak.stock_board_concept_index_ths(
        symbol=name, start_date=start_str, end_date=end_str
    )
    # 只保留最近 N 条
    df = df.tail(days)
    results = []
    for _, row in df.iterrows():
        results.append({
            'date': str(row['日期']),
            'open': float(row['开盘价']),
            'high': float(row['最高价']),
            'low': float(row['最低价']),
            'close': float(row['收盘价']),
            'volume': int(row['成交量']),
            'amount': float(row['成交额']),
        })
    return results


def concept_hot_ranking(top_n: int = 20) -> List[Dict]:
    """获取今日概念板块涨幅排行 (基于指数K线末日收盘计算).

    注意: 这个函数较慢 (~1分钟), 因为需要逐个拉取概念指数.
    适合每日 cron 使用, 不适合实时交互.

    返回 [{name, code, close, change_pct, volume, amount}, ...] 按 change_pct 降序
    """
    ak = _lazy_ak()
    concepts = concept_list()
    end_str = datetime.now().strftime('%Y%m%d')
    start_str = (datetime.now() - timedelta(days=5)).strftime('%Y%m%d')

    rankings = []
    for c in concepts:
        try:
            df = ak.stock_board_concept_index_ths(
                symbol=c['name'], start_date=start_str, end_date=end_str
            )
            if len(df) >= 2:
                today = df.iloc[-1]
                yesterday = df.iloc[-2]
                change = (today['收盘价'] - yesterday['收盘价']) / yesterday['收盘价'] * 100
                rankings.append({
                    'name': c['name'],
                    'code': c['code'],
                    'close': float(today['收盘价']),
                    'change_pct': round(change, 2),
                    'volume': int(today['成交量']),
                    'amount': float(today['成交额']),
                })
        except Exception:
            continue

    rankings.sort(key=lambda x: x['change_pct'], reverse=True)
    return rankings[:top_n]


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

def _cli():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)

    cmd = sys.argv[1]

    if cmd == 'quote':
        codes = sys.argv[2:]
        if not codes:
            print("usage: quote_sources.py quote <code1> [code2] ...", file=sys.stderr)
            sys.exit(2)
        results = realtime_quotes(codes)
        for r in results:
            chg = r.get('change_pct')
            chg_str = f"{chg:+.2f}%" if chg is not None else "N/A"
            print(f"  {r.get('code','?'):>8} {r.get('name','?'):<8} "
                  f"price={r.get('price','?')} "
                  f"open={r.get('open','?')} high={r.get('high','?')} low={r.get('low','?')} "
                  f"vol={r.get('volume','?')} chg={chg_str}")

    elif cmd == 'val':
        codes = sys.argv[2:]
        if not codes:
            print("usage: quote_sources.py val <code1> [code2] ...", file=sys.stderr)
            sys.exit(2)
        results = valuation_snapshot(codes)
        for r in results:
            print(f"  {r['code']:>8} {r['name']:<8} "
                  f"PE={r['pe_ttm'] or 'N/A':<7} PB={r['pb'] or 'N/A':<6} "
                  f"市值={r['total_mkt_cap'] or 'N/A'}亿 "
                  f"换手={r['turnover_rate'] or 'N/A'}% "
                  f"涨跌={r['change_pct'] or 'N/A'}%")

    elif cmd == 'daily':
        code = sys.argv[2] if len(sys.argv) > 2 else None
        if not code:
            print("usage: quote_sources.py daily <code> [--days 20]", file=sys.stderr)
            sys.exit(2)
        days = 20
        args = sys.argv[3:]
        for i, a in enumerate(args):
            if a == '--days' and i + 1 < len(args):
                days = int(args[i + 1])
        results = daily_bars(code, days)
        print(f"  === {code} daily x{days} ===")
        for r in results:
            print(f"  {r['date']} O={r['open']:.2f} H={r['high']:.2f} "
                  f"L={r['low']:.2f} C={r['close']:.2f} Vol={r['volume']}")

    elif cmd == 'concept':
        name = sys.argv[2] if len(sys.argv) > 2 else None
        if not name:
            print("usage: quote_sources.py concept <概念名>", file=sys.stderr)
            sys.exit(2)
        days = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        results = concept_index(name, days)
        print(f"  === {name} 近{days}日 ===")
        for r in results:
            print(f"  {r['date']} O={r['open']:.2f} H={r['high']:.2f} "
                  f"L={r['low']:.2f} C={r['close']:.2f} Vol={r['volume']}")

    elif cmd == 'bars':
        code = sys.argv[2] if len(sys.argv) > 2 else None
        if not code:
            print("usage: quote_sources.py bars <code> [--freq 5min] [--count 20]", file=sys.stderr)
            sys.exit(2)
        freq = 'daily'
        count = 20
        args = sys.argv[3:]
        for i, a in enumerate(args):
            if a == '--freq' and i + 1 < len(args):
                freq = args[i + 1]
            elif a == '--count' and i + 1 < len(args):
                count = int(args[i + 1])
        results = bars(code, freq, count)
        print(f"  === {code} {freq} x{count} ===")
        for r in results:
            print(f"  {r['datetime']} O={r['open']:.2f} H={r['high']:.2f} "
                  f"L={r['low']:.2f} C={r['close']:.2f} Vol={r['volume']}")

    elif cmd == 'concepts':
        # 列出所有概念板块
        results = concept_list()
        print(f"  同花顺概念板块: {len(results)} 个")
        for r in results[:30]:
            print(f"    {r['code']} {r['name']}")
        if len(results) > 30:
            print(f"    ... ({len(results) - 30} more)")

    elif cmd == 'hot':
        top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        print(f"  计算概念板块涨幅排名 (可能需要 ~1min)...")
        results = concept_hot_ranking(top_n)
        print(f"  === 今日概念板块 TOP{top_n} ===")
        for i, r in enumerate(results, 1):
            print(f"  {i:2d}. {r['name']:<10} {r['change_pct']:+.2f}% "
                  f"close={r['close']:.2f} vol={r['volume']}")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print("Commands: quote | val | concept | bars | concepts | hot", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    _cli()
