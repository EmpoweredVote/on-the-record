#!/usr/bin/env bash
# Regenerate summaries + topics and re-publish all 14 meetings to Supabase.
# Run from the repo root: bash republish_all.sh
# Logs each meeting to logs/republish_<meeting_id>.log

set -euo pipefail
cd "$(dirname "$0")"

LOG_DIR="logs/republish"
mkdir -p "$LOG_DIR"

MEETINGS=(
  "2026-02-04-council"
  "2026-02-18-council"
  "2026-02-25-council"
  "2026-03-23-lwv-candidate-forum---house-61-and-county-commissioner"
  "2026-03-30-lwv-candidate-forum---county-clerk-and-prosecutor"
  "2026-03-31-special-session"
  "2026-04-03-lwv-brown-county-candidate-forum-auditor"
  "2026-04-14-primary-candidate-forum"
  "2026-04-23-governor's-debate-(nexstar)"
  "2026-05-06-la-mayoral-debate-(nbcla)"
  "2026-05-07-governor-debate-(nbcla-and-telemundo)"
  "2026-05-15-governor-debate-(cbs-and-sf-examiner)"
  "2026-06-01-lwv-candidate-forum-davis-county-ut"
  "2026-06-09-regular-session"
)

TOTAL=${#MEETINGS[@]}
PASSED=0
FAILED=0
FAILED_LIST=()

echo "=== republish_all.sh — $(date) ==="
echo "Processing $TOTAL meetings..."
echo ""

for i in "${!MEETINGS[@]}"; do
  meeting="${MEETINGS[$i]}"
  num=$((i + 1))
  log="$LOG_DIR/${meeting}.log"

  echo "[$num/$TOTAL] $meeting"

  if PYTHONUNBUFFERED=1 .venv/bin/python run_local.py --publish-meeting "$meeting" \
      > "$log" 2>&1; then
    echo "  ✅ done  (log: $log)"
    PASSED=$((PASSED + 1))
  else
    echo "  ❌ FAILED — see $log"
    FAILED=$((FAILED + 1))
    FAILED_LIST+=("$meeting")
  fi
  echo ""
done

echo "=== Done: $PASSED passed, $FAILED failed ==="
if [ ${#FAILED_LIST[@]} -gt 0 ]; then
  echo "Failed meetings:"
  for m in "${FAILED_LIST[@]}"; do
    echo "  - $m"
  done
  exit 1
fi
