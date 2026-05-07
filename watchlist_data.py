"""watchlist_data.py — 关注清单 (持仓 + 观察, 仅 code/name).

本文件由 daily.sh 读取, 做**市场趋势信号检测** (不涉及个人 P&L,
工具不控盘, 成本价和仓位权重是用户自己的事).

分组只是**标签**, 用来组织输出, 不影响信号逻辑 — 所有股票用同一套
市场信号规则 (放量企稳/突破/回调/末期加速/趋势破坏/动能衰竭).

维护规则:
  - 加减关注直接改文件, 下次 daily.sh 自动读最新
  - 分组可自定义 (基础仓 / 博弈仓-追涨 / 博弈仓-左侧 / 观察池 / ...)
  - 一只股可以重复出现在多个组 (但会去重)

最近更新: 2026-05-07
"""

# 关注清单 (按主题/风格分组, 仅标签)
# 格式: [(ts_code, name), ...]
WATCHLIST = {
    "基础仓": [
        # funnel balanced 筛出 — 资金动向良好 + 估值合理 + 主题清晰
        ("000858.SZ", "五粮液"),
        ("000651.SZ", "格力电器"),
        ("601211.SH", "国泰海通"),
        ("600887.SH", "伊利股份"),
        ("002415.SZ", "海康威视"),
        ("601766.SH", "中国中车"),
        ("601225.SH", "陕西煤业"),
    ],
    "博弈仓-追涨": [
        # momentum balanced 筛出 — 已在趋势里 + 放量 + 高位
        ("688256.SH", "寒武纪"),
        ("688146.SH", "中船特气"),
        ("300408.SZ", "三环集团"),
        ("002081.SZ", "金螳螂"),
    ],
    "博弈仓-左侧": [
        # momentum contrarian 筛出 — 1M 强势 + 短期回调 (左侧博弈点)
        ("300308.SZ", "中际旭创"),
        ("002475.SZ", "立讯精密"),
    ],
    "观察池": [
        # 未买但持续跟踪, 等趋势信号出现时再考虑
        ("688041.SH", "海光信息"),
        ("688008.SH", "澜起科技"),
        ("300502.SZ", "新易盛"),
        ("301308.SZ", "江波龙"),
        ("600519.SH", "贵州茅台"),
        ("600036.SH", "招商银行"),
    ],
}

# 跨市场锚点 — 美股 AI 链主要标的 (影响 A 股 AI 链估值 / 开盘方向)
US_ANCHORS = [
    ("NVDA",  "NVIDIA"),
    ("TSM",   "台积电"),
    ("MSFT",  "Microsoft"),
    ("META",  "Meta"),
    ("GOOGL", "Google"),
    ("MU",    "Micron"),
]


def all_codes():
    """所有 A 股 ts_code (去重)."""
    seen = set()
    out = []
    for tier, stocks in WATCHLIST.items():
        for code, name in stocks:
            if code not in seen:
                seen.add(code)
                out.append((code, name, tier))
    return out


def groups():
    """返回 {tier: [(code, name), ...]}."""
    return WATCHLIST


if __name__ == "__main__":
    print(f"关注清单 ({len(all_codes())} 只 A 股, 分 {len(WATCHLIST)} 组):\n")
    for tier, stocks in WATCHLIST.items():
        print(f"  [{tier}]  {len(stocks)} 只")
        for code, name in stocks:
            print(f"    {code:<12} {name}")
        print()
    print(f"美股锚点 ({len(US_ANCHORS)} 只):")
    for code, name in US_ANCHORS:
        print(f"    {code:<6} {name}")
