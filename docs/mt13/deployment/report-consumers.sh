#!/usr/bin/env bash
# Installation template only. No publish, no network, no schedule changes.
# morning/evening FIRST SECOND COMPANY OUT ; weekly ASOF OUT
set -uo pipefail
HERE="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$HERE"
PHASE="${1:?phase required}"; shift
ROOT="${MT13_LEDGER_ROOT:?choose an independently reviewed isolated root}"
case "$PHASE" in
  morning|evening)
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
