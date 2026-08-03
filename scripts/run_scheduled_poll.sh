#!/bin/bash
# Entry point for the launchd agenda/memo poll. Do not call this directly for
# ad-hoc runs — use scripts/poll_agendas.py from your own checkout for that.
#
# WHY THIS WRAPPER EXISTS: the scheduled job used to run
# scripts/poll_agendas.py straight out of the main working checkout, which
# meant it executed whatever branch happened to be checked out there — with
# several worktrees in play that is a coin flip, and on 2026-08-03 the main
# checkout was sitting on a quotes feature branch 12 commits behind main.
# Civic-data automation must not depend on where a human left their HEAD.
#
# So: a dedicated DETACHED worktree that this script fast-forwards to
# origin/main before every run. Nothing a human does to their branches can
# change what the scheduler executes, and the scheduler always runs shipped
# code. .env.local is symlinked in (it is gitignored, so it exists only in
# the primary checkout) and the venv is shared — an interpreter is not
# branch-specific.
set -euo pipefail

REPO="/Users/chrisandrews/Documents/GitHub/on-the-record"
AUTOMATION_CHECKOUT="$HOME/CouncilScribe/automation-checkout"
PYTHON="$REPO/.venv/bin/python"

echo "=== scheduled poll $(date '+%Y-%m-%d %H:%M:%S %Z') ==="

if [ ! -d "$AUTOMATION_CHECKOUT" ]; then
    echo "FATAL: $AUTOMATION_CHECKOUT is missing. Recreate it with:"
    echo "  git -C $REPO worktree add --detach $AUTOMATION_CHECKOUT origin/main"
    echo "  ln -s $REPO/.env.local $AUTOMATION_CHECKOUT/.env.local"
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

exec "$PYTHON" "$AUTOMATION_CHECKOUT/scripts/poll_agendas.py" "$@"
