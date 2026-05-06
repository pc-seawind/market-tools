"""watchlist_data.py — 持仓 + 观察清单 (手工维护).

本文件由 daily.sh 读取, 做持仓监控 + 信号触发检测.

维护规则:
  - HOLDINGS 按仓位分层 (基础仓 / 博弈仓-追涨 / 博弈仓-左侧 / 等)
  - 每条: (ts_code, 股票名, 仓位%, 成本价)
    - ts_code: 带后缀如 "000858.SZ"
    - 仓位%: 占总资产的百分比, e.g. 12.0 表示 12%
    - 成本价: 买入均价, 用来计算 P&L 和 止损触发点
  - WATCHLIST 是"未买但想监控"的股票 (没仓位没成本价)
  - 加减持仓后直接改这个文件即可, daily.sh 会自动读最新配置
  - 这只是一个 **示例模板**, 真实持仓需用户自己维护

最近更新: 2026-05-07 (示例配置, 按上次 funnel+momentum 推荐)
"""

# 持仓 (按仓位分层)
# 格式: [(ts_code, name, weight_pct, cost_price), ...]
HOLDINGS = {
    "基础仓": [
        # funnel balanced 选出的, 1-2 年持有, -15% 止损
        ("000858.SZ", "五粮液",     12.0, 130.00),
        ("000651.SZ", "格力电器",   10.0,  38.50),
        ("601211.SH", "国泰海通",    8.0,  18.30),
        ("600887.SH", "伊利股份",    8.0,  27.10),
        ("002415.SZ", "海康威视",    8.0,  35.50),
        ("601766.SH", "中国中车",    7.0,   5.90),
        ("601225.SH", "陕西煤业",    7.0,  25.80),
    ],
    "博弈仓-追涨": [
        # momentum balanced 选出的, 1-3 个月, -10% 严格止损
        ("688256.SH", "寒武纪",      3.0, 1800.00),
        ("688146.SH", "中船特气",    3.0,   75.00),
        ("300408.SZ", "三环集团",    2.0,   82.00),
        ("002081.SZ", "金螳螂",      2.0,    6.40),
    ],
    "博弈仓-左侧": [
        # momentum contrarian, 2-8 周, 短期回调中长期强势
        ("300308.SZ", "中际旭创",    3.0,  870.00),
        ("002475.SZ", "立讯精密",    2.0,   65.50),
    ],
}

# 观察清单 (还没买, 想跟踪动态)
WATCHLIST = [
    ("688041.SH", "海光信息"),       # AI 芯片, 低于寒武纪的备选
    ("688008.SH", "澜起科技"),       # HBM 中间件龙头
    ("300502.SZ", "新易盛"),         # 光模块, 中际旭创的 peer
    ("301308.SZ", "江波龙"),         # 存储周期龙头 (但位置 100%, 不追)
    ("600519.SH", "贵州茅台"),       # 白酒标杆, 参照五粮液
    ("600036.SH", "招商银行"),       # 价值蓝筹对照
]

# 关键跨市场锚点 (美股) — 影响 A 股 AI 链估值
US_ANCHORS = [
    ("NVDA", "NVIDIA"),              # AI 芯片总龙头
    ("TSM",  "台积电"),              # 晶圆代工
    ("MSFT", "Microsoft"),           # Azure capex
    ("META", "Meta"),                # AI 应用 + 资本开支
    ("GOOGL","Google"),              # Tensor / TPU
    ("MU",   "Micron"),              # 存储, 对标江波龙/兆易创新
]


def total_weight():
    """计算总持仓权重 (%)."""
    return sum(
        w for tier in HOLDINGS.values() for _, _, w, _ in tier
    )


def all_holdings():
    """扁平化所有持仓, 带分层标签.
    Returns: [(ts_code, name, tier, weight_pct, cost_price), ...]
    """
    out = []
    for tier, stocks in HOLDINGS.items():
        for code, name, w, cp in stocks:
            out.append((code, name, tier, w, cp))
    return out


if __name__ == "__main__":
    total_w = total_weight()
    print(f"持仓总权重: {total_w:.1f}%  (现金 {100 - total_w:.1f}%)\n")
    for tier, stocks in HOLDINGS.items():
        tier_w = sum(w for _, _, w, _ in stocks)
        print(f"  [{tier}]  权重 {tier_w:.1f}%")
        for code, name, w, cp in stocks:
            print(f"    {code:<12} {name:<10}  {w:>5.1f}%  @ ¥{cp:>8.2f}")
        print()
    print(f"观察清单 ({len(WATCHLIST)} 只):")
    for code, name in WATCHLIST:
        print(f"    {code:<12} {name}")
    print(f"\n美股锚点 ({len(US_ANCHORS)} 只):")
    for code, name in US_ANCHORS:
        print(f"    {code:<6} {name}")
