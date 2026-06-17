#!/usr/bin/env bash
# evening_recap_data.sh — 晚盘复盘"数据采集"一键编排.
#
# 为什么需要这个脚本:
#   晚盘复盘是个两阶段、有数据依赖的流程:
#     1. sector_score.py --all --json   → 41 个板块评分 (Tier 1)
#     2. 从输出里挑 tier1_pass==True 的板块名 (精确字符串)
#     3. 对每个挑出的板块跑 sector_picks.py --sector "<名>" (Tier 2-4 选股)
#   过去把这套编排交给 cron agent 临场拼命令, agent 多次猜错参数
#   (漏 --all / cwd 错 / 板块名错), 触发 set -e 整条中断, 或 16 个
#   板块串行跑满 turn 软超时被砍. 2026-06-10 晚盘复盘连续两次失败.
#
#   本脚本把"确定性的两阶段编排"从 agent 手里拿走, 固化成一个命令.
#   agent 只需 RunDetached 调它一次, 收割时读输出 JSON 写分析文档.
#   agent 不再有机会猜错参数.
#
# Usage:
#   evening_recap_data.sh [--out FILE] [--max-picks N] [--score-json FILE]
#
# Options:
#   --out FILE          结果 JSON 写到 FILE (默认 /tmp/evening_recap_<date>.json)
#                       同时也打到 stdout 末尾的 RESULT_JSON= 行.
#   --max-picks N       最多对前 N 个 Tier1 板块跑 picks (按 total_score 降序,
#                       默认 16 = 全跑). 防止极端日板块过多时超时.
#   --score-json FILE   跳过 score 重算, 直接用现成的 score --all --json 文件
#                       (用于补跑: 当天已算过 score 时复用, 避免重算).
#
# 输出 JSON 结构:
#   {
#     "meta": {"date": "...", "trade_date": "...", "fresh": true/false,
#              "n_sectors": 41, "n_tier1_pass": 16, "n_picks_run": 16,
#              "generated_at": "...", "errors": [...]},
#     "scores": [ <sector_score --all --json 原样> ],
#     "picks":  { "<板块名>": <sector_picks --sector --json 原样 或 {error:...}> }
#   }
#
# 设计纪律:
#   - set -uo pipefail, 但单个 picks 失败用 || 隔离, 不中断整批.
#   - 全程不依赖 cwd, 用脚本自身目录定位 sibling 脚本.
#   - 重 (score --all ~? + picks×N×19s ≈ 几分钟), 调用方必须 RunDetached.
#
# Env:
#   TUSHARE_TOKEN                  required (sector_score / sector_picks 内部用).
#   EVENING_RECAP_SCORE_TIMEOUT    score --all 超时秒数 (默认 3600). 卡死则
#                                  exit 4 而非无限挂起整个采集.

set -uo pipefail

HERE="$(dirname "$(readlink -f "$0")")"
cd "$HERE"

DATE="$(date +%F)"
OUT="/tmp/evening_recap_${DATE}.json"
MAX_PICKS=16
SCORE_JSON=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out)        OUT="$2"; shift 2 ;;
    --max-picks)  MAX_PICKS="$2"; shift 2 ;;
    --score-json) SCORE_JSON="$2"; shift 2 ;;
    -h|--help)    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

WORKDIR="$(mktemp -d /tmp/evening_recap_work.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

SCORE_FILE="$WORKDIR/scores.json"
SCORE_ERR="$WORKDIR/score.err"

echo "[evening_recap_data] start date=$DATE out=$OUT max_picks=$MAX_PICKS" >&2

# ── Stage 0: HTSC sector main-flow cache ─────────────────────────
# Tushare moneyflow_ind_dc may be unavailable/no-permission. In that case
# bk_moneyflow.py now prefers pre-refreshed HTSC/OpenClaw 主力净流入 cache.
# This refresh is bounded by TTL: if fresh, htsc_sector_flow.py skips network.
HTSC_FLOW_REFRESH_TIMEOUT="${HTSC_FLOW_REFRESH_TIMEOUT:-900}"
if [[ -x "$HERE/htsc_sector_flow.py" ]]; then
  echo "[evening_recap_data] refresh HTSC sector-flow cache (TTL guarded)" >&2
  timeout "$HTSC_FLOW_REFRESH_TIMEOUT" python3 "$HERE/htsc_sector_flow.py" refresh-default --max-concepts 13 --ttl-hours 12     > "$WORKDIR/htsc_sector_flow_refresh.json" 2> "$WORKDIR/htsc_sector_flow_refresh.err" ||     echo "[evening_recap_data] WARN: HTSC sector-flow refresh failed/timeout; scoring will use existing cache or neutral fallback" >&2
fi

# ── Stage 1: 板块评分 ────────────────────────────────────────────
if [[ -n "$SCORE_JSON" && -s "$SCORE_JSON" ]]; then
  echo "[evening_recap_data] reuse score json: $SCORE_JSON (skip recompute)" >&2
  cp "$SCORE_JSON" "$SCORE_FILE"
else
  # score --all 逐板块调 tushare, 正常 ~3-6min, 但偶发卡死 (限速/网络).
  # 用 timeout 兜底, 卡死则报错退出而非无限挂起整个采集 turn.
  SCORE_TIMEOUT="${EVENING_RECAP_SCORE_TIMEOUT:-3600}"
  echo "[evening_recap_data] stage 1: sector_score.py --all --json (timeout ${SCORE_TIMEOUT}s)" >&2
  timeout "$SCORE_TIMEOUT" python3 sector_score.py --all --json > "$SCORE_FILE" 2> "$SCORE_ERR"
  rc=$?
  if [[ $rc -eq 124 ]]; then
    echo "[evening_recap_data] FATAL: sector_score.py TIMED OUT after ${SCORE_TIMEOUT}s" >&2
    exit 4
  elif [[ $rc -ne 0 ]]; then
    echo "[evening_recap_data] FATAL: sector_score.py failed (exit $rc):" >&2
    cat "$SCORE_ERR" >&2
    exit 3
  fi
  if [[ ! -s "$SCORE_FILE" ]]; then
    echo "[evening_recap_data] FATAL: sector_score.py produced empty output" >&2
    exit 3
  fi
fi

# ── Stage 2: 挑 Tier1 通过的板块 + 逐个 picks ────────────────────
# 用 python 完成: 解析 scores → 选 tier1_pass 的板块 (按 total_score 降序,
# 取前 MAX_PICKS) → 逐个调 sector_picks --sector --json → 合并成最终 JSON.
python3 - "$SCORE_FILE" "$OUT" "$MAX_PICKS" "$DATE" <<'PY'
import json, subprocess, sys, datetime, os

score_file, out_file, max_picks_s, date = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
max_picks = int(max_picks_s)
here = os.path.dirname(os.path.realpath(__file__)) if "__file__" in dir() else os.getcwd()

errors = []
with open(score_file) as f:
    scores = json.load(f)
if not isinstance(scores, list):
    scores = scores.get("sectors") or scores.get("data") or []

# ── 数据新鲜度校验 (轻量, 单次 tushare 拉沪深300最新一行) ──────────
# score 行里通常没有 trade_date 字段, 所以这里显式探测一次, 写进 meta,
# 这样收割 agent 直接看 meta.fresh, 不用自己再校验.
trade_date = None
try:
    _today = date.replace("-", "")
    _past = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y%m%d")
    cp = subprocess.run(
        ["python3", "tushare.py", "index_daily", "ts_code=000300.SH",
         f"start_date={_past}", f"end_date={_today}",
         "--fields=trade_date", "--csv"],
        cwd=here, capture_output=True, text=True, timeout=60,
    )
    if cp.returncode == 0:
        # csv: 末行(或首数据行)是最新 trade_date; 取所有 8位数字里最大的
        import re
        dates = re.findall(r"\b(\d{8})\b", cp.stdout)
        if dates:
            trade_date = max(dates)
except Exception as e:
    errors.append(f"freshness probe failed: {e}")
    sys.stderr.write(f"[evening_recap_data] freshness probe failed: {e}\n")

# Tier1 通过的板块, 按 total_score 降序
passed = [r for r in scores if r.get("tier1_pass")]
passed.sort(key=lambda r: (r.get("total_score") or 0), reverse=True)
selected = passed[:max_picks]

picks = {}
for r in selected:
    concept = r.get("concept")
    if not concept:
        continue
    try:
        cp = subprocess.run(
            ["python3", "sector_picks.py", "--sector", concept, "--json"],
            cwd=here, capture_output=True, text=True, timeout=180,
        )
        if cp.returncode != 0:
            picks[concept] = {"error": f"picks exit {cp.returncode}", "stderr": cp.stderr[-500:]}
            errors.append(f"picks failed: {concept} (exit {cp.returncode})")
            sys.stderr.write(f"[evening_recap_data]   picks FAIL {concept}: exit {cp.returncode}\n")
            continue
        picks[concept] = json.loads(cp.stdout)
        sys.stderr.write(f"[evening_recap_data]   picks ok   {concept}\n")
    except subprocess.TimeoutExpired:
        picks[concept] = {"error": "picks timeout 180s"}
        errors.append(f"picks timeout: {concept}")
        sys.stderr.write(f"[evening_recap_data]   picks TIMEOUT {concept}\n")
    except json.JSONDecodeError as e:
        picks[concept] = {"error": f"picks json decode: {e}"}
        errors.append(f"picks decode: {concept}")
        sys.stderr.write(f"[evening_recap_data]   picks DECODE-ERR {concept}\n")

result = {
    "meta": {
        "date": date,
        "trade_date": trade_date,
        "fresh": (trade_date == date.replace("-", "")) if trade_date else None,
        "n_sectors": len(scores),
        "n_tier1_pass": len(passed),
        "n_picks_run": len(selected),
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "errors": errors,
    },
    "scores": scores,
    "picks": picks,
}

with open(out_file, "w") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

# stdout 末尾给一个机器可读的指针行, 方便收割 agent 直接 grep
print(f"RESULT_JSON={out_file}")
print(f"SUMMARY n_sectors={len(scores)} n_tier1_pass={len(passed)} "
      f"n_picks_run={len(selected)} errors={len(errors)}")
PY

rc=$?
echo "[evening_recap_data] done rc=$rc out=$OUT" >&2
exit $rc
