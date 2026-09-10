#!/usr/bin/env bash
# narrative_prefilter.sh — 三源快讯抓取 + 相关性过滤 + 合并排序 + 写 handoff
#
# 用法: ./narrative_prefilter.sh [--min-relevance N] [--top N]
#
# 输出: narrative_prefilter_candidates.jsonl (覆盖当天)
# 退出码: 0 全成功；1 部分失败；2 全部失败
#
# 设计: 每个源 --fetch 直连 API 抓 (不依赖 Jina Reader), 失败不阻断.
# 三源合并后跨源去重, 按 is_breaking desc + relevance desc 排序.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

TODAY=$(date +%F)
MIN_RELEVANCE=3
TOP=100
FAILED=0
TOTAL_SOURCES=0

OUT_JSONL="$HERE/narrative_prefilter_candidates.jsonl"
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

usage() {
    echo "Usage: $0 [--min-relevance N] [--top N]"
    exit 1
}

while [ $# -gt 0 ]; do
    case "$1" in
        --min-relevance) MIN_RELEVANCE="$2"; shift 2 ;;
        --top) TOP="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "Unknown arg: $1"; usage ;;
    esac
done

# ── 抓一个源 ──
fetch_source() {
    local src="$1"
    local out="$2"
    TOTAL_SOURCES=$((TOTAL_SOURCES + 1))

    python3 cls_telegraph_filter.py \
        --source "$src" \
        --fetch \
        --format json \
        --min-relevance "$MIN_RELEVANCE" \
        --include-dup \
        --top "$TOP" \
        > "$out" 2>/dev/null

    local rc=$?
    if [ $rc -ne 0 ] || [ ! -s "$out" ]; then
        echo "[]" > "$out"
        FAILED=$((FAILED + 1))
        echo "[FAIL] $src" >&2
        return 1
    fi

    python3 -c "import json; d=json.load(open('$out')); assert isinstance(d, list)" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo "[]" > "$out"
        FAILED=$((FAILED + 1))
        echo "[FAIL] $src (invalid JSON)" >&2
        return 1
    fi

    local count
    count=$(python3 -c "import json; print(len(json.load(open('$out'))))")
    echo "[ OK ] $src: $count candidates" >&2
    return 0
}

echo "=== narrative-prefilter $TODAY ===" >&2

fetch_source cls       "$TMPDIR/cls.json"
fetch_source eastmoney "$TMPDIR/em.json"
fetch_source sina      "$TMPDIR/sina.json"

# ── 合并 + 跨源去重 + 排序 + 写 jsonl ──
python3 - "$TMPDIR" "$OUT_JSONL" "$TODAY" << 'PYEOF'
import json, os, re, sys

tmpdir = sys.argv[1]
out_path = sys.argv[2]
today = sys.argv[3]

all_items = []
for fname in ["cls.json", "em.json", "sina.json"]:
    fpath = os.path.join(tmpdir, fname)
    try:
        items = json.load(open(fpath))
    except Exception:
        continue
    if not isinstance(items, list):
        continue
    all_items.extend(items)

print(f"[MERGE] raw: {len(all_items)} items", file=sys.stderr)

if not all_items:
    open(out_path, "w").close()
    print("[RESULT] 0 candidates (all sources empty/failed)")
    sys.exit(0)

# 跨源去重: 标题+正文指纹
def _fingerprint(text):
    nums = set(re.findall(r"\d+\.?\d*", text))
    chars = set(re.findall(r"[\u4e00-\u9fa5A-Za-z]", text))
    return nums, chars

def _jaccard(a, b):
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0

def _is_dup_item(a, b):
    """判断两条快讯是否为同一事件的多源报道。
    任一条件满足即判定重复:
    1. 标题高度相似 (字符 jaccard >= 0.65) 且数字有交集
    2. 标题+正文整体字符相似 >= 0.5 且数字高度重合 (>= 0.6)
    3. 数字完全重合 (>= 0.8) 且核心关键词重合度高
    """
    a_title = a.get("title", "")
    b_title = b.get("title", "")
    a_text = a_title + " " + a.get("body", "")
    b_text = b_title + " " + b.get("body", "")
    a_nums, a_chars = _fingerprint(a_text)
    b_nums, b_chars = _fingerprint(b_text)
    a_tnums, a_tchars = _fingerprint(a_title)
    b_tnums, b_tchars = _fingerprint(b_title)

    title_char_sim = _jaccard(a_tchars, b_tchars)
    title_num_sim = _jaccard(a_tnums, b_tnums)
    full_char_sim = _jaccard(a_chars, b_chars)
    full_num_sim = _jaccard(a_nums, b_nums)

    # 条件1: 标题高度相似
    if title_char_sim >= 0.65 and (len(a_tnums & b_tnums) > 0 or full_num_sim >= 0.3):
        return True
    # 条件2: 正文相似 + 数字高度重合
    if full_char_sim >= 0.45 and full_num_sim >= 0.6 and len(a_nums) >= 2:
        return True
    # 条件3: 数字几乎完全重合 (强时间/数字事件)
    if full_num_sim >= 0.8 and full_char_sim >= 0.35 and len(a_nums) >= 2:
        return True
    return False

unique = []
# 先按 relevance 排，去重时保留高分的
for it in sorted(all_items, key=lambda x: (-x.get("relevance", 0), x.get("source", ""))):
    is_dup = False
    for u in unique:
        if _is_dup_item(it, u):
            is_dup = True
            if it.get("is_breaking") and not u.get("is_breaking"):
                u["is_breaking"] = True
                u["signal_words"] = sorted(set(u.get("signal_words", []) + it.get("signal_words", [])))
            srcs = set(u.get("sources", [u.get("source", "")]))
            srcs.add(it.get("source", ""))
            u["sources"] = sorted(srcs)
            break
    if not is_dup:
        it["sources"] = [it.get("source", "")]
        unique.append(it)

# 排序: is_breaking desc, relevance desc
unique.sort(key=lambda x: (0 if x.get("is_breaking") else 1, -x.get("relevance", 0)))

with open(out_path, "w") as f:
    for it in unique:
        it["prefilter_date"] = today
        f.write(json.dumps(it, ensure_ascii=False) + "\n")

breaking = sum(1 for x in unique if x.get("is_breaking"))
print(f"[RESULT] {len(unique)} candidates ({breaking} breaking) -> {out_path}")
PYEOF

echo "[DONE] $((TOTAL_SOURCES - FAILED))/$TOTAL_SOURCES ok, $FAILED failed" >&2

if [ "$FAILED" -eq "$TOTAL_SOURCES" ]; then
    exit 2
elif [ "$FAILED" -gt 0 ]; then
    exit 1
else
    exit 0
fi
