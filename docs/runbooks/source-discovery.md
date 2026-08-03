# Source discovery — runbook

Spec: `docs/superpowers/specs/2026-08-02-source-discovery-design.md`
Job: `vote.empowered.poll-discovery` (launchd, daily 08:00) →
`scripts/poll_discovery.py` → log at `~/CouncilScribe/discovery/poll.log`

## Daily workflow

1. Open the GUI (`.venv/bin/python -m gui`) → **Discovery** (link in the library toolbar).
2. Red strip = zero-source alarms (races ≤30 days out, still sourcing, no approved
   sources). Each needs an agent gap-filler (below).
3. Skim pending items per race: title, outlet, duration, the classifier's *why*.
   Click through and scrub ~10s when unsure.
4. Actions: **Approve → ingest** (enqueues into the batch pool), **Edit first**
   (prefilled /new form — use for clip windows/guests), **Approve → quote source**
   (marks for race-pipeline pickup), **Reject** (pick the reason — it trains
   nothing automatically yet, but it suppresses re-surfacing and is the flywheel's
   record), **+ Watch this channel** (adds the outlet to the watchlist).

## Manual runs

    .venv/bin/python scripts/poll_discovery.py --dry-run      # no LLM, no writes
    .venv/bin/python scripts/poll_discovery.py --race RACE_ID # force one race now
    .venv/bin/python scripts/poll_discovery.py --print-alarms

## Agent gap-filler (zero-source alarm)

For each alarmed race, run a one-shot deep hunt in a Claude session:

> Find original sources of the candidates' own spoken words for RACE_LABEL
> (election ELECTION_DATE). Tier order: debates/forums, news interviews,
> prepared remarks, candidate-bylined written. Search the open web, local TV
> and newspaper sites, LWV/civic orgs, candidate sites/channels. For each find,
> insert a row into essentials.discovered_sources (discovered_via='agent',
> status='pending', route='ingest' for video/audio or 'quote_source' for text,
> source_key per src/source_key.py, race_id and matched_politician_ids set,
> why = one evidence sentence). Never C-SPAN. Do not ingest anything.

## Outlet packs (rolling seed)

When a state comes inside ~90 days of an election, run an agent to research and
insert 8–15 outlets (`added_via='seed'`): local TV news channels, PBS + NPR
affiliates, LWV state chapter, Clean-Elections/civic-debate orgs, top newspaper
channels. Insert `essentials.source_outlets` rows with the YouTube channel-RSS
feed_url (`https://www.youtube.com/feeds/videos.xml?channel_id=UC…`) and
`state` set. Outlets are active on insert — the per-item triage gate is the guard.

## First-time setup

1. Task-12 harvest: `.venv/bin/python scripts/harvest_outlets.py --apply`
2. **AFTER MERGE TO MAIN** — install the plist (this branch is a worktree; the
   plist's `ProgramArguments` point at the main checkout's
   `scripts/poll_discovery.py`, which doesn't exist there until this branch
   merges. Loading it before that would register a daily job against
   missing/stale code):

   ```bash
   mkdir -p ~/CouncilScribe/discovery
   cp scripts/launchd/vote.empowered.poll-discovery.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/vote.empowered.poll-discovery.plist
   launchctl list | grep poll-discovery
   ```

   Expected: one line containing `vote.empowered.poll-discovery`.
3. First run by hand: `.venv/bin/python scripts/poll_discovery.py`
