#!/usr/bin/env python3
"""cls_telegraph_filter.py — 财联社电报 一手快讯流 → 投资相关候选过滤器.

为什么需要它:
  radar 现状只用 search_baidu/search_tavily 搜固定主题词, 每天命中同一条隔夜旧闻
  (alpha 早被吃掉). 财联社电报是分钟级 A 股公开快讯流, 是公开渠道里最快的一手源.
  但它混了大量宏观噪音 (鸡蛋涨价/世界杯签证/中东冲突...). 这个脚本把 agent 用
  fetch_url_rendered 拿到的电报原文喂进来, 按 investment universe 过滤,
  只留投资相关条目, 输出结构化候选给 radar agent 二次评分 + add 入库.

设计取舍:
  MCP fetch_url_rendered 只能 agent 进程内调用, Python 脚本拿不到网络渲染能力.
  所以本脚本 *不* 自己抓取 — 它从 stdin 读 agent 粘贴进来的电报 markdown,
  做纯文本解析 + 相关性打分 + 噪音过滤 + 30 天指纹去重 (复用 narrative_radar 的指纹).

用法 (radar cron agent 内):
  1. agent 调 fetch_url_rendered("https://www.cls.cn/telegraph") 拿原文
  2. agent 把原文通过 stdin 喂给本脚本:
       echo "<电报原文>" | python3 cls_telegraph_filter.py --since-hours 24
  3. 脚本输出 JSON 候选数组 (按相关性分降序), 每条含:
       {time, title, body, sectors, matched_tickers, matched_keywords,
        signal_words, relevance, is_breaking, dedup_status}
  4. agent 读候选 → 对 relevance≥阈值 且 dedup_status=='new' 的逐条判断 →
     调 narrative_radar.py add 入库 (cmd_add 自带指纹去重二次兜底)

输出分两档 (--breaking-threshold 控制):
  is_breaking=True  → 高优先突发 (重大合同/政策/独家+强信号词), 建议即时推送
  is_breaking=False → 常规, 晚间 radar 汇总
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
UNIVERSE_PATH = os.path.join(HERE, "narrative_universe.yaml")
EVENTS_PATH = os.path.join(HERE, "narrative_events.jsonl")

CN_TZ = dt.timezone(dt.timedelta(hours=8))

# ── 强信号词 (实业增量 / 一手 / 突发) — 命中加权, 是 narrow 叙事的语言指纹 ──
SIGNAL_WORDS = [
    # 实业增量
    "中标", "量产", "投产", "扩产", "新厂", "新工厂", "签约", "签单", "订单",
    "供货合同", "战略合作", "独家", "首发", "首次", "突破", "上调", "提价",
    "涨价", "缺口", "供不应求", "满产", "排产", "交付", "出货",
    # 业绩/预期
    "业绩预告", "业绩超预期", "预增", "扭亏", "创历史", "历史新高", "翻倍",
    # 政策/事件催化
    "发改委", "工信部", "国务院", "央行", "认证", "获批", "中标公告",
    "重大合同", "收购", "增持", "回购", "定增", "中标金额",
]

# ── 纯宏观/无投资意义噪音 — 命中直接降权或丢弃 (除非也命中 universe) ──
NOISE_TAGS = {
    "环球市场情报", "中东冲突", "足球盛宴2026", "文化传媒", "禽畜期货",
    "家禽", "新闻联播", "期货市场情报", "粤港澳大湾区",
}
NOISE_TITLE_PAT = re.compile(
    r"(空袭|枪击|埃博拉|鸡蛋|票房|世界杯|签证|暴雨|新闻联播|会见|论坛全会|"
    r"地震|台风|疫情|球员|比赛|演唱会|电影|足协|预算达|卸任|离任|赦免|"
    r"联储主席|白宫.{0,6}顾问|内政部|外交部|总理|总统会见)"
)

# ── 财联社电报: "HH:MM:SS【标题】正文..." — Jina markdown 里时间戳行内联 ──
CLS_ITEM_RE = re.compile(r"(\d{2}:\d{2}:\d{2})\s*【([^】]+)】\s*(.*?)(?=\n\d{2}:\d{2}:\d{2}\s*【|\Z)", re.S)
CLS_SUBJECT_RE = re.compile(r"\[([^\]]+)\]\(https://www\.cls\.cn/subject/\d+\)")
CLS_STOCK_RE = re.compile(r"\[([^\]]+)\]\(https://www\.cls\.cn/stock\?code=([a-z]{2}\d{6})\)")

# ── 东方财富快讯: "HH:MM\n[**【标题】**正文[点击查看全文]](url)" — Jina markdown ──
# 时间行 (HH:MM) 独立一行, 紧跟一个 markdown 链接包裹的 加粗标题+正文
EM_ITEM_RE = re.compile(
    r"(\d{2}:\d{2})\s*\n+\[\*\*【([^】]+)】\*\*(.*?)(?:\[点击查看全文\])?\]\([^)]*\)",
    re.S,
)


def load_universe_index():
    """返回 (keywords:set, ticker_names:set, ticker_by_name:dict, sector_hints:set)."""
    import yaml
    u = yaml.safe_load(open(UNIVERSE_PATH))
    keywords, ticker_names, ticker_by_name, sector_hints = set(), set(), {}, set()

    def walk(d):
        if isinstance(d, dict):
            for k, v in d.items():
                if k == "keywords" and isinstance(v, list):
                    for x in v:
                        if isinstance(x, str) and len(x) >= 2:
                            keywords.add(x)
                elif k == "tickers" and isinstance(v, list):
                    for t in v:
                        if isinstance(t, dict) and t.get("name"):
                            nm = t["name"].strip()
                            ticker_names.add(nm)
                            ticker_by_name[nm] = t.get("code", "")
                else:
                    walk(v)
        elif isinstance(d, list):
            for x in d:
                walk(x)

    walk(u)
    # 用 subdomain 名里的语义词补充板块提示 (e.g. ai__memory_storage → 存储)
    sector_hints = {
        "存储", "芯片", "半导体", "光模块", "光器件", "CPO", "算力", "AI",
        "PCB", "覆铜板", "液冷", "服务器", "数据中心", "HBM", "封装", "GPU",
        "铜箔", "电子布", "玻纤", "连接器", "光芯片", "激光", "机器人",
        "扫地机", "白电", "家电", "智能家居", "AIPC", "AI手机", "消费电子",
        "电源", "变压器", "电力设备",
    }
    return keywords, ticker_names, ticker_by_name, sector_hints


def _clean_body(body):
    clean = re.sub(r"\[[^\]]*\]\([^)]*\)", "", body)
    clean = re.sub(r"阅[\d.]+[WW万]?|评论\s*\(?\d*\)?|分享\s*\(?\d*\)?|\.\.\.展开|"
                   r"点击查看全文|桌面通知|声音提醒|语音电报|\*\*", "", clean)
    return re.sub(r"\s+", " ", clean).strip()


def parse_cls(raw):
    """财联社电报 (Jina markdown). 返回 [{time,title,body,sectors,stocks}]."""
    items = []
    for m in CLS_ITEM_RE.finditer(raw):
        tm, title, body = m.group(1), m.group(2).strip(), m.group(3)
        sectors = CLS_SUBJECT_RE.findall(body)
        stocks = [(nm, code) for nm, code in CLS_STOCK_RE.findall(body)]
        items.append({
            "time": tm, "title": title, "body": _clean_body(body),
            "sectors": [s for s in sectors if s not in ("加红", "公司", "看盘")],
            "stocks": stocks, "source": "cls",
        })
    return items


def parse_eastmoney(raw):
    """东方财富 7x24 快讯 (Jina markdown). 无板块/个股标签, 只有时间+标题+正文."""
    items = []
    for m in EM_ITEM_RE.finditer(raw):
        tm, title, body = m.group(1), m.group(2).strip(), m.group(3)
        items.append({
            "time": tm, "title": title, "body": _clean_body(body),
            "sectors": [], "stocks": [], "source": "eastmoney",
        })
    return items


def parse_sina(raw):
    """新浪财经 7x24 (JSON API zhibo/feed). 自带 stocks 字段 + tag 分类."""
    items = []
    try:
        data = json.loads(raw)
        feed = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
    except Exception:
        return items
    for it in feed:
        rich = (it.get("rich_text") or "").strip()
        if not rich:
            continue
        # 标题: 若有【】取括号内, 否则取首句
        mt = re.match(r"【([^】]+)】(.*)", rich, re.S)
        if mt:
            title, body = mt.group(1).strip(), mt.group(2).strip()
        else:
            title, body = rich[:40], rich
        # 时间 HH:MM:SS → HH:MM:SS
        ct = it.get("create_time", "")
        tm = ct.split(" ")[1] if " " in ct else ct
        # stocks / tag
        stocks = []
        try:
            ext = json.loads(it.get("ext", "{}"))
            for s in ext.get("stocks", []) or []:
                nm = s.get("name") or s.get("key") or ""
                code = s.get("symbol", "") or s.get("code", "")
                mkt = s.get("market", "")
                # 只保留 A 股 (cn/沪深), 丢掉 commodity/forex/us/uk/fund/worldIndex
                # — 避免"石油/波音/欧元/煤炭"这类商品外汇污染 ticker 命中
                # 只认真实 A 股个股代码 sz/sh/bj+6位; 排除 si(板块指数)/fund/商品/外汇
                if nm and re.match(r"^(sz|sh|bj)\d{6}$", code):
                    stocks.append((nm, code))
        except Exception:
            pass
        tags = [t.get("name", "") for t in it.get("tag", []) if t.get("name")]
        items.append({
            "time": tm, "title": title, "body": _clean_body(body),
            "sectors": tags, "stocks": stocks, "source": "sina",
        })
    return items


PARSERS = {"cls": parse_cls, "eastmoney": parse_eastmoney, "sina": parse_sina}


# 过于宽泛的 universe 关键词 — 它们是 event_type 分类器用的, 不能单独作为'话题锚点'.
# (e.g. '价格上涨' 会命中"燃油价格上涨"这种宏观噪音). 只有 *配合* ticker/真板块 才有意义.
GENERIC_KW = {
    "价格上涨", "上调", "提价", "上涨", "政策", "推进", "战略合作", "增持", "收购",
    "入股", "单价", "ASP", "毛利率", "出货量同比", "半年报", "年报披露", "分析师",
    "买入评级", "上调评级", "卖方", "业绩超预期",
}


def score_item(item, kw, tnames, sector_hints):
    """相关性打分 + 噪音判定. 返回 dict 增量字段."""
    text = item["title"] + " " + item["body"]
    matched_kw = sorted({k for k in kw if k in text})
    # 区分"实质锚点关键词"(具体题材/公司/技术名) vs generic(价格/政策类) — 后者不单独锚定话题
    anchor_kw = [k for k in matched_kw if k not in GENERIC_KW]
    matched_tickers = sorted({nm for nm in tnames if nm in text})
    # 财联社自带的 stock 标签直接是强 ticker 命中
    for nm, code in item["stocks"]:
        matched_tickers.append(f"{nm}({code})")
    matched_tickers = sorted(set(matched_tickers))
    # sina 的结构标签(公司/国际/市场/宏观/央行/观点/焦点/其他)是版面分类, 不是行业, 不算锚点
    GENERIC_TAGS = {"公司", "国际", "市场", "宏观", "央行", "观点", "焦点", "其他", "A股", "港股", "美股"}
    real_sectors = {s for s in item["sectors"] if s not in GENERIC_TAGS}
    matched_sectors = sorted(real_sectors | {h for h in sector_hints if h in text})
    signals = sorted({w for w in SIGNAL_WORDS if w in text})

    # 噪音判定 (两条规则):
    #  (a) 硬噪音: 所有板块标签都在 NOISE_TAGS 且无 ticker 命中 → 直接丢
    #      (避免"鸡蛋价格上涨"这种命中 generic keyword '价格上涨' 但实为农产品的漏网)
    #  (b) 软噪音: 命中噪音标签/标题模式, 且没有任何 universe 强命中
    item_sectors = set(item["sectors"])
    hard_noise = (
        item_sectors and item_sectors <= NOISE_TAGS and not matched_tickers
    )
    soft_noise = (
        (item_sectors & NOISE_TAGS or NOISE_TITLE_PAT.search(item["title"]))
        and not matched_tickers
        and not (matched_sectors and signals)
    )
    # (c) 无 universe 锚点: 既没命中 ticker, 也没命中*实质*关键词/真板块
    #     → 仅靠 generic 词(价格上涨/订单/创历史)命中的是宏观噪音(航空/足协/美联储), 丢.
    no_anchor = (not matched_tickers and not anchor_kw and not matched_sectors)
    is_noise = bool(hard_noise or soft_noise or no_anchor)

    # 滞后复盘/盘点类 (资金流向/调研路线图/牛熊股/盘点/涨跌幅榜) — 是已发生事实的事后
    # 聚合, 不是一手催化, 而且常堆砌一堆 ticker 虚高分数. 标记 is_recap, 给 ticker 贡献降权.
    is_recap = bool(re.search(
        r"(资金流向|资金净流入|资金青睐|主力(净)?(流入|流出|抢筹)|获抢筹|获主力|"
        r"调研路线图|机构调研|牛熊股|涨幅榜|跌幅榜|连板|盘点|复盘|"
        r"一周|本周.{0,6}(行业|个股|板块)|获.{0,4}机构调研|获资金)",
        item["title"] + item["body"][:40]))

    # ticker 贡献封顶: 一条 recap 列 10 只票不该碾压聚焦的单票一手催化.
    # 非 recap 最多算前 4 只 ticker; recap 最多算 2 只 (因其 ticker 是事后归因).
    tk_cap = 2 if is_recap else 4
    tk_contrib = min(len(matched_tickers), tk_cap)
    # relevance: ticker 命中最值钱, 其次 universe 关键词, 板块提示, 信号词
    rel = (
        3 * tk_contrib
        + 2 * len(matched_kw)
        + 1 * len(matched_sectors)
        + 1 * len(signals)
    )
    if is_recap:
        rel = max(1, rel - 3)  # recap 整体降权 (滞后, 已 price-in)

    # 突发判定: 强信号词 ≥2 且 (有 ticker 或重大政策词); recap 永不算突发
    breaking_pol = any(w in text for w in ("发改委", "工信部", "国务院", "央行", "认证", "获批"))
    is_breaking = (not is_recap) and (
        (len(signals) >= 2 and (matched_tickers or breaking_pol))
        or len([t for t in matched_tickers]) >= 2 and not is_recap
    )

    item.update({
        "matched_tickers": matched_tickers,
        "matched_keywords": matched_kw,
        "matched_sectors": matched_sectors,
        "signal_words": signals,
        "relevance": rel,
        "is_noise": bool(is_noise),
        "is_recap": bool(is_recap),
        "is_breaking": bool(is_breaking),
    })
    return item


def dedup_against_library(items, days=30):
    """best-effort 去重: 标记每条 dedup_status (new/dup).

    注: cmd_add 在入库时还有一道 subdomain-gated 指纹去重 (find_duplicate),
    那是权威兜底. 这里只做轻量的 *标题数字指纹 + 字符相似* 预筛, 帮 agent
    在评分前就把'昨天电报已出现过的同一条'标灰, 避免重复评分浪费 token.
    """
    try:
        sys.path.insert(0, HERE)
        from narrative_radar import _num_fingerprint, _ngrams, _norm_title, _jaccard, load_events
        existing = load_events()
        # 只比对近 days 天的库内事件
        cutoff = dt.datetime.now(CN_TZ) - dt.timedelta(days=days)
        recent = []
        for e in existing:
            try:
                e_dt = dt.datetime.fromisoformat(e["ts"].split("#")[0])
                if e_dt >= cutoff:
                    recent.append(e)
            except Exception:
                recent.append(e)
        ex_fps = [(_ngrams(_norm_title(e.get("title", ""))),
                   _num_fingerprint(e.get("title", ""))) for e in recent]
        have = True
    except Exception:
        ex_fps = []
        have = False
        _num_fingerprint = _ngrams = _norm_title = _jaccard = None

    for it in items:
        status = "new"
        if have:
            title = it["title"] + " " + it["body"][:60]
            ng = _ngrams(_norm_title(title))
            nums = _num_fingerprint(title)
            for e_ng, e_nums in ex_fps:
                char_sim = _jaccard(ng, e_ng)
                num_sim = _jaccard(nums, e_nums)
                shared = nums & e_nums
                if char_sim >= 0.55 or (num_sim >= 0.5 and len(shared) >= 2):
                    status = "dup"
                    break
        it["dedup_status"] = status
    return items


def main():
    ap = argparse.ArgumentParser(description="财联社电报 → 投资相关候选过滤器")
    ap.add_argument("--min-relevance", type=int, default=3,
                    help="最低相关性分, 低于则丢弃 (默认 3)")
    ap.add_argument("--top", type=int, default=30, help="最多输出 N 条 (默认 30)")
    ap.add_argument("--include-dup", action="store_true",
                    help="保留与库内重复的条目 (默认只输出 new)")
    ap.add_argument("--format", choices=["json", "md"], default="json")
    ap.add_argument("--dedup-days", type=int, default=30)
    ap.add_argument("--source", choices=list(PARSERS.keys()), default="cls",
                    help="信源类型: cls(财联社) / eastmoney(东财) / sina(新浪7x24 JSON)")
    args = ap.parse_args()

    raw = sys.stdin.read()
    if not raw.strip():
        print("[]" if args.format == "json" else "(stdin 为空, 没有快讯原文)")
        return

    kw, tnames, tbyname, sector_hints = load_universe_index()
    items = PARSERS[args.source](raw)
    items = [score_item(it, kw, tnames, sector_hints) for it in items]
    # 过滤: 非噪音 + 达到相关性阈值
    cand = [it for it in items if not it["is_noise"] and it["relevance"] >= args.min_relevance]
    # 批内跨条去重 (同一批里财联社/东财可能各自重复) — 保留 relevance 最高的一条
    cand = _intra_batch_dedup(cand)
    cand = dedup_against_library(cand, days=args.dedup_days)
    if not args.include_dup:
        # 突发条目 (重大合同/政策一手催化) 即使指纹疑似重复也保留 — 因为本助手没有
        # subdomain 上下文, 指纹去重偏激进, 容易把'某公司签具体合同'误判成同板块旧闻.
        # 权威去重在 cmd_add 入库时做 (subdomain+ticker gated). 这里只对非突发条目去重.
        cand = [it for it in cand if it["dedup_status"] == "new" or it["is_breaking"]]
    # 排序: 突发优先, 再按相关性
    cand.sort(key=lambda x: (x["is_breaking"], x["relevance"]), reverse=True)
    cand = cand[: args.top]

    src_label = {"cls": "财联社电报", "eastmoney": "东财快讯", "sina": "新浪7x24"}[args.source]
    if args.format == "json":
        print(json.dumps(cand, ensure_ascii=False, indent=1))
    else:
        if not cand:
            print(f"今日{src_label}无投资相关新增候选 (全是宏观噪音或已入库重复).")
            return
        print(f"# {src_label}候选 ({len(cand)} 条, 解析 {len(items)} 条原始快讯)\n")
        for it in cand:
            flag = "🔴突发" if it["is_breaking"] else ("📊复盘" if it.get("is_recap") else "·")
            dup = " [疑似已入库]" if it.get("dedup_status") == "dup" else ""
            print(f"## {flag} [{it['time']}] {it['title']}{dup}")
            print(f"- relevance={it['relevance']} | 信号词: {','.join(it['signal_words']) or '—'}")
            if it["matched_tickers"]:
                print(f"- 命中标的: {','.join(it['matched_tickers'])}")
            if it["matched_sectors"]:
                print(f"- 板块: {','.join(it['matched_sectors'])}")
            print(f"- 正文: {it['body'][:160]}")
            print()


def _intra_batch_dedup(cand):
    """批内去重: 同一次抓取里, 标题数字指纹+字符高相似的并成一条 (留 relevance 最高)."""
    try:
        sys.path.insert(0, HERE)
        from narrative_radar import _num_fingerprint, _ngrams, _norm_title, _jaccard
    except Exception:
        return cand
    kept = []
    for it in sorted(cand, key=lambda x: x["relevance"], reverse=True):
        t = it["title"] + " " + it["body"][:60]
        ng, nums = _ngrams(_norm_title(t)), _num_fingerprint(t)
        dup = False
        for k in kept:
            kt = k["title"] + " " + k["body"][:60]
            kng, knums = _ngrams(_norm_title(kt)), _num_fingerprint(kt)
            shared = nums & knums
            if _jaccard(ng, kng) >= 0.55 or (_jaccard(nums, knums) >= 0.5 and len(shared) >= 2):
                dup = True
                break
        if not dup:
            kept.append(it)
    return kept


if __name__ == "__main__":
    main()
