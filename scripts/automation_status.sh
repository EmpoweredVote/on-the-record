#!/bin/bash
# What is scheduled to run on this machine, when it last ran, and what it said.
#
# Answers "what automation do I have and is it healthy?" without needing to
# remember plist paths or log locations. Read-only: inspects, never changes.
#
#   bash scripts/automation_status.sh
set -uo pipefail

BLUE=$'\033[1;34m'; DIM=$'\033[2m'; RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; OFF=$'\033[0m'
REPO="/Users/chrisandrews/Documents/GitHub/on-the-record"
AUTOMATION_CHECKOUT="$HOME/CouncilScribe/automation-checkout"

echo "${BLUE}=== Scheduled jobs (launchd user agents) ===${OFF}"
found=0
for plist in "$HOME/Library/LaunchAgents"/*.plist; do
    [ -e "$plist" ] || continue
    label=$(basename "$plist" .plist)
    found=1
    # `launchctl list <label>` exits non-zero when the job is not loaded.
    if status=$(launchctl list "$label" 2>/dev/null); then
        last_exit=$(echo "$status" | awk -F'= ' '/"LastExitStatus"/ {print $2}' | tr -d ';')
        pid=$(echo "$status" | awk -F'= ' '/"PID"/ {print $2}' | tr -d ';')
        state="loaded"
        [ -n "${pid:-}" ] && state="RUNNING NOW (pid $pid)"
        if [ "${last_exit:-0}" = "0" ]; then
            echo "  ${GREEN}✓${OFF} $label — $state, last exit ${last_exit:-none}"
        else
            echo "  ${RED}✗${OFF} $label — $state, last exit ${RED}${last_exit}${OFF} (failed)"
        fi
    else
        echo "  ${RED}✗${OFF} $label — ${RED}NOT LOADED${OFF} (plist exists but launchd doesn't have it;"
        echo "      load with: launchctl bootstrap gui/\$UID \"$plist\")"
    fi
    # Schedule + log path, straight out of the plist.
    /usr/libexec/PlistBuddy -c "Print :StartCalendarInterval" "$plist" 2>/dev/null \
        | awk '/Hour/{h=$3} /Minute/{m=$3} END{if (h!="") printf "      schedule: daily at %02d:%02d local\n", h, m}'
    logpath=$(/usr/libexec/PlistBuddy -c "Print :StandardOutPath" "$plist" 2>/dev/null)
    if [ -n "${logpath:-}" ] && [ -f "$logpath" ]; then
        echo "      log: $logpath ${DIM}(modified $(date -r "$logpath" '+%Y-%m-%d %H:%M'))${OFF}"
        echo "${DIM}$(tail -n 4 "$logpath" | sed 's/^/        /')${OFF}"
    elif [ -n "${logpath:-}" ]; then
        echo "      log: $logpath ${DIM}(no output yet)${OFF}"
    fi
done
[ "$found" = "0" ] && echo "  (none in ~/Library/LaunchAgents)"

echo
echo "${BLUE}=== Automation checkout (what the scheduler executes) ===${OFF}"
if [ -d "$AUTOMATION_CHECKOUT" ]; then
    echo "  path: $AUTOMATION_CHECKOUT"
    echo "  code: $(git -C "$AUTOMATION_CHECKOUT" log --oneline -1 2>/dev/null)"
    behind=$(git -C "$AUTOMATION_CHECKOUT" rev-list --count HEAD..origin/main 2>/dev/null || echo "?")
    if [ "$behind" = "0" ]; then
        echo "  ${GREEN}✓${OFF} up to date with origin/main ${DIM}(it self-updates before each run)${OFF}"
    else
        echo "  ${DIM}$behind commit(s) behind origin/main — the wrapper fast-forwards at run time${OFF}"
    fi
    [ -L "$AUTOMATION_CHECKOUT/.env.local" ] \
        && echo "  ${GREEN}✓${OFF} .env.local symlinked from the primary checkout" \
        || echo "  ${RED}✗${OFF} .env.local MISSING — the poll will fail on DB/API credentials"
else
    echo "  ${RED}✗ missing${OFF} — recreate (a CLONE, not a worktree: launchd's git"
    echo "      cannot read ~/Documents, see run_scheduled_poll.sh header):"
    echo "      git clone $REPO $AUTOMATION_CHECKOUT"
    echo "      git -C $AUTOMATION_CHECKOUT remote set-url origin git@github.com:EmpoweredVote/on-the-record.git"
    echo "      ln -s $REPO/.env.local $AUTOMATION_CHECKOUT/.env.local"
fi

echo
echo "${BLUE}=== Poll state (what has already been handled) ===${OFF}"
for state in "$HOME/CouncilScribe/agendas/bloomington-city-council/poll_state.json" \
             "$HOME/CouncilScribe/agendas/bloomington-city-council/memo_state.json"; do
    name=$(basename "$state")
    if [ -f "$state" ]; then
        n=$(python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$state" 2>/dev/null || echo "?")
        echo "  $name: $n meeting(s) recorded ${DIM}(modified $(date -r "$state" '+%Y-%m-%d %H:%M'))${OFF}"
    else
        echo "  $name: ${DIM}none yet${OFF}"
    fi
done

echo
echo "${DIM}Notes: launchd runs a missed daily slot when the machine wakes or after"
echo "the next login, so a poll skipped while the Mac was off/asleep still happens"
echo "(once — it does not replay every missed day). Agent jobs need you logged in."
echo "Run a poll by hand any time: launchctl kickstart -k gui/\$UID/<label>${OFF}"
