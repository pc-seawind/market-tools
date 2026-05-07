"""watchlist_data.py — 关注清单 (持仓 + 观察, 仅 code/name).

本文件由 daily.sh 读取, 做**市场趋势信号检测** (不涉及个人 P&L,
工具不控盘, 成本价和仓位权重是用户自己的事).

分组只是**标签**, 用来组织输出, 不影响信号逻辑 — 所有股票用同一套
市场信号规则 (放量企稳/突破/回调/末期加速/趋势破坏/动能衰竭).

支持 A 股 (sh/sz/bj) + 港股 (hk). daily.sh 会自动识别后缀路由到
对应的 tushare API (A 股 → daily, 港股 → hk_daily).

维护规则:
  - 加减关注直接改文件, 下次 daily.sh 自动读最新
  - 分组可自定义 (基础仓 / 博弈仓-追涨 / 博弈仓-左侧 / 观察池 / ...)
  - 一只股可以重复出现在多个组 (但会去重)
  - ts_code 格式:
      A 股: "000858.SZ", "600519.SH", "831168.BJ"
      港股: "00700.HK" (注意要 5 位数字, 前补 0)

最近更新: 2026-05-08 (用户真实持仓)
"""

# 关注清单 (按主题/风格分组, 仅标签)
# 格式: [(ts_code, name), ...]
WATCHLIST = {
    "基础仓": [
        # 大市值 + 长期持有属性
        ("002594.SZ", "比亚迪"),       # 新能源车龙头
        ("00700.HK",  "腾讯控股"),     # 港股互联网核心
        ("01810.HK",  "小米集团"),     # 消费电子 + AI
        ("03690.HK",  "美团"),         # 本地生活
    ],
    "博弈仓-追涨": [
        # AI 基建链, 主升浪中的强势龙头
        ("300394.SZ", "天孚通信"),     # 光通信 / 英伟达链
        ("300442.SZ", "润泽科技"),     # AIDC / 算力租赁
    ],
    "观察池": [
        # 未买但持续跟踪, 等趋势信号出现时再考虑
        ("688041.SH", "海光信息"),     # AI 芯片, 寒武纪备选
        ("688008.SH", "澜起科技"),     # HBM 中间件龙头
        ("688256.SH", "寒武纪"),       # AI 芯片总龙头
        ("300308.SZ", "中际旭创"),     # 光模块龙头
        ("300502.SZ", "新易盛"),       # 光模块 peer
        ("301308.SZ", "江波龙"),       # 存储周期龙头
        ("600519.SH", "贵州茅台"),     # 白酒标杆
        ("600036.SH", "招商银行"),     # 价值蓝筹
    ],
}

# 跨市场锚点 — 美股 AI 链主要标的 (影响 A+H 股 AI 链估值 / 开盘方向)
US_ANCHORS = [
    ("NVDA",  "NVIDIA"),
    ("TSM",   "台积电"),
    ("MSFT",  "Microsoft"),
    ("META",  "Meta"),
    ("GOOGL", "Google"),
    ("MU",    "Micron"),
]


def all_codes():
    """所有 ts_code (去重). Returns: [(code, name, tier), ...]."""
    seen = set()
    out = []
    for tier, stocks in WATCHLIST.items():
        for code, name in stocks:
            if code not in seen:
                seen.add(code)
                out.append((code, name, tier))
    return out


def has_hk():
    """Check if any HK ticker in the watchlist."""
    for tier, stocks in WATCHLIST.items():
        for code, _ in stocks:
            if code.endswith(".HK"):
                return True
    return False


def groups():
    """返回 {tier: [(code, name), ...]}."""
    return WATCHLIST


if __name__ == "__main__":
    codes = all_codes()
    print(f"关注清单 ({len(codes)} 只, 分 {len(WATCHLIST)} 组):  HK 标的 {'有' if has_hk() else '无'}\n")
    for tier, stocks in WATCHLIST.items():
        print(f"  [{tier}]  {len(stocks)} 只")
        for code, name in stocks:
            tag = " 🇭🇰" if code.endswith(".HK") else ""
            print(f"    {code:<12} {name}{tag}")
        print()
    print(f"美股锚点 ({len(US_ANCHORS)} 只):")
    for code, name in US_ANCHORS:
        print(f"    {code:<6} {name}")
