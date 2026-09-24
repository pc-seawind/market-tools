#!/usr/bin/env bash
set -euo pipefail
cd /home/emox/work/projects/market-tools
base=reports/recap-reliability-20260925
out="$base/morning-recovery-verified-2026-09-25.json"
export RECAP_NO_HISTORY=1 EVENING_RECAP_REVERSAL_QUOTA=0
EVENING_RECAP_BUDGET_SECONDS=4 bash evening_recap_data.sh --score-json "$base/score-input.json" --max-picks 1 --out "$out"
cp "$out" "$base/verified-real-partial.json"
EVENING_RECAP_BUDGET_SECONDS=180 bash evening_recap_data.sh --score-json "$base/score-input.json" --max-picks 1 --out "$out"
cp "$out" "$base/verified-real-restored.json"
EVENING_RECAP_BUDGET_SECONDS=30 bash evening_recap_data.sh --score-json "$base/score-input.json" --max-picks 1 --out "$out"
cp "$out" "$base/verified-real-idempotent.json"
