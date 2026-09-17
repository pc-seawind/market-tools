#!/usr/bin/env bash
# Deployed consumer: original TA/MT13 plus isolated Huatai consultation; no schedule changes.
# morning/evening FIRST SECOND COMPANY OUT ; weekly ASOF OUT
set -uo pipefail
HERE="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$HERE"
PHASE="${1:?phase required}"; shift
ROOT="${MT13_LEDGER_ROOT:?choose an independently reviewed isolated root}"
# Existing investment agent writes a reviewed batch then reruns this same entry.
# Failure is explicit and isolated; never pretend a failed review was applied.
if [ -n "${TA10_REVIEW_FILE:-}" ]; then
  timeout 30 python3 -m mt1.ta_workflow apply --batch "$TA10_REVIEW_FILE" \
    || printf '%s\n' '{"status":"research_review_writeback_failed","owner":"investment-agent","not_published":true}'
fi
case "$PHASE" in
  morning|evening)
    # Same original report directory + phase is the idempotency boundary, including TA reruns.
    HUATAI_BASE="$(dirname "$(readlink -f "$1")")/huatai-$PHASE"
    python3 -m mt1.huatai_daily start --base "$HUATAI_BASE" --phase "$PHASE" \
      || printf '%s\n' '{"status":"huatai_batch_failed_original_report_continues"}'
    export HUATAI_REPORT_BASE="$HUATAI_BASE"
    timeout 30 python3 -m mt1.action_loop report-cycle --phase "$PHASE" \
      --root "$ROOT" --section-one "$1" --section-two "$2" \
      --company-section "$3" --out "$4" \
      || printf '%s\n' '{"status":"technical_consumer_failed_keep_original_report","not_published":true}'
    ;;
  weekly)
    timeout 30 python3 -m mt1.action_loop report-cycle --phase weekly \
      --root "$ROOT" --asof "$1" --out "$2" \
      || printf '%s\n' '{"status":"technical_weekly_failed_keep_other_sections","not_published":true}'
    ;;
  *) printf '%s\n' 'unsupported phase' >&2; exit 2;;
esac
