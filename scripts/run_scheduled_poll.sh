#!/bin/bash
# Entry point for the launchd scheduled polls (agendas by default; pass a
# scripts/*.py path as the first argument to run another poller, e.g.
# scripts/poll_discovery.py). Do not call this directly for ad-hoc runs — use
# scripts/poll_agendas.py from your own checkout for that.
#
# WHY THIS WRAPPER EXISTS: the scheduled job used to run
# scripts/poll_agendas.py straight out of the main working checkout, which
# meant it executed whatever branch happened to be checked out there — with
# several worktrees in play that is a coin flip, and on 2026-08-03 the main
# checkout was sitting on a quotes feature branch 12 commits behind main.
# Civic-data automation must not depend on where a human left their HEAD.
#
# So: a dedicated STANDALONE CLONE that this script fast-forwards to
# origin/main before every run. Nothing a human does to their branches can
# change what the scheduler executes, and the scheduler always runs shipped
# code. .env.local is symlinked in (it is gitignored, so it exists only in
# the primary checkout) and the venv is shared — an interpreter is not
# branch-specific.
#
# IT MUST BE A CLONE, NOT A GIT WORKTREE. A worktree keeps its metadata in
# the primary repo's .git/worktrees/, i.e. under ~/Documents — and macOS
# privacy protection (TCC) is granted per binary, so /usr/bin/git launched by
# launchd is DENIED there even though the same git works from a Terminal
# shell, which inherits Terminal's grant. Measured 2026-08-03: every git
# command in the launchd context failed with "fatal: not a git repository:
# .../.git/worktrees/automation-checkout" while python in the same job read
# the symlinked .env.local fine. A standalone clone keeps all git metadata in
# ~/CouncilScribe, which launchd can read.
set -euo pipefail

REPO="/Users/chrisandrews/Documents/GitHub/on-the-record"
AUTOMATION_CHECKOUT="$HOME/CouncilScribe/automation-checkout"
PYTHON="$REPO/.venv/bin/python"

echo "=== scheduled poll $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

if [ ! -d "$AUTOMATION_CHECKOUT" ]; then
    echo "FATAL: $AUTOMATION_CHECKOUT is missing. Recreate it with:"
    echo "  git clone $REPO $AUTOMATION_CHECKOUT"
    echo "  git -C $AUTOMATION_CHECKOUT remote set-url origin git@github.com:EmpoweredVote/on-the-record.git"
    echo "  git -C $AUTOMATION_CHECKOUT checkout --detach origin/main"
    echo "  ln -s $REPO/.env.local $AUTOMATION_CHECKOUT/.env.local"
    echo "(a CLONE, not a worktree — launchd's git cannot read ~/Documents; see the header)"
    exit 1
fi

# Update to shipped main. A network failure here is not fatal: running
# yesterday's main beats skipping the poll entirely, so warn and carry on.
if git -C "$AUTOMATION_CHECKOUT" fetch --quiet origin 2>/dev/null; then
    git -C "$AUTOMATION_CHECKOUT" checkout --quiet --detach origin/main
    echo "code: $(git -C "$AUTOMATION_CHECKOUT" log --oneline -1)"
else
    echo "WARNING: git fetch failed (offline?) — running the checkout as-is:"
    echo "code: $(git -C "$AUTOMATION_CHECKOUT" log --oneline -1)"
fi

# First argument may name the script to run (a scripts/*.py path relative to
# the checkout). Without one, default to the agenda poll so plists predating
# this generalization keep working untouched.
TARGET="scripts/poll_agendas.py"
case "${1:-}" in
    scripts/*.py) TARGET="$1"; shift ;;
esac

# launchd truncates output silently when a StandardOutPath's parent dir is
# missing; make every known job log dir exist for the NEXT run (launchd opens
# the log before exec'ing us, so this protects future runs, not this one).
mkdir -p "$HOME/CouncilScribe/agendas" "$HOME/CouncilScribe/discovery"

exec "$PYTHON" "$AUTOMATION_CHECKOUT/$TARGET" "$@"
