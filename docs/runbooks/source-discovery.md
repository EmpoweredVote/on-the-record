# Source discovery — runbook

Spec: `docs/superpowers/specs/2026-08-02-source-discovery-design.md`
Spec v2: `docs/superpowers/specs/2026-08-03-source-discovery-v2-design.md`
Job: `vote.empowered.poll-discovery` (launchd, daily 08:00) →
`scripts/run_scheduled_poll.sh` (automation-checkout clone) →
`scripts/poll_discovery.py --trigger scheduled` → log at
`~/CouncilScribe/discovery/poll.log`

## Daily workflow

1. Open the GUI (`.venv/bin/python -m gui`) → **Discovery** (link in the library toolbar).
2. Check the **last run** pill in the header: it must show a `scheduled` run
   within 36 h, status ok. A red "no scheduled run in 36h" pill or a CRASHED
   run means the scheduler is broken — check
   `~/CouncilScribe/discovery/poll.log` and `launchctl list | grep poll-discovery`.
   The collapsed **Outlet evidence** panel tracks per-outlet approve history
   toward the mode-C auto-ingest bar (≥10 reviewed · ≥90% approved · 0
   identity rejects); it is display-only — no outlet auto-ingests anything.
3. Red strip = zero-source alarms (races ≤30 days out, still sourcing, no approved
   sources). Each needs an agent gap-filler (below).
4. Skim pending items per race: title, outlet, duration, the classifier's *why*.
   Click through and scrub ~10s when unsure.
5. Actions: **Approve → ingest** (enqueues into the batch pool), **Edit first**
   (prefilled /new form — use for clip windows/guests), **Approve → quote source**
   (marks for race-pipeline pickup), **Reject** (pick the reason — it trains
   nothing automatically yet, but it suppresses re-surfacing and is the flywheel's
   record), **+ Watch this channel** (adds the outlet to the watchlist). For web
   items, Approve → ingest first probes the page and trusts the FIRST
   extractable media it finds — pages whose lead embed is a teaser belong in
   Edit-first with a direct link instead.

## Manual runs

    .venv/bin/python scripts/poll_discovery.py --dry-run      # no LLM, no writes
    .venv/bin/python scripts/poll_discovery.py --race RACE_ID # force one race now
    .venv/bin/python scripts/poll_discovery.py --print-alarms

Bot-check/429 waves: each search retries ~3x with backoff (config
DISCOVERY_BACKOFF_*), and after 5 consecutive exhausted searches the sweep
phase aborts loudly for the run (SWEEP ABORT in poll.log; cadence clocks not
reset) — expect the next day's run to pick the sweeps back up.

Items older than `DISCOVERY_MAX_ITEM_AGE_DAYS` (630 — reaches past the previous
general election) are dropped at stage 1 with a per-item `STALE [via] 'title'`
line in poll.log; the count lands in the DONE line and the run record. Only
candidate-named items are counted, so a nonzero number is worth a look.

A malformed feed item (e.g. an undefined entity) fails that OUTLET for the
run — it lands in the failure count and the stale-feed pill, and clears when
the feed does.

## Agent gap-filler (zero-source alarm)

For each alarmed race, run a one-shot deep hunt in a Claude session:

> Find original sources of the candidates' own spoken words for RACE_LABEL
> (election ELECTION_DATE). Tier order: debates/forums, news interviews,
> prepared remarks, candidate-bylined written. Search the open web, local TV
> and newspaper sites, LWV/civic orgs, candidate sites/channels. For each find,
> insert a row into essentials.discovered_sources (discovered_via='agent',
> status='pending', route='ingest' for video/audio or 'quote_source' for text,
> source_key per src/source_key.py, race_id and matched_politician_ids set,
> why = one evidence sentence).
>
> Also check the race's Ballotpedia page and Vote411 for scheduled or recent
> debates/forums; hunt for recordings of past ones, and record upcoming
> events as a note in `why` (advance notice is evidence for the calendar layer).
>
> Never C-SPAN. Do not ingest anything.

## Outlet packs (rolling seed)

When a state comes inside ~90 days of an election, run an agent to research and
insert 8–15 outlets (`added_via='seed'`): local TV news channels, PBS + NPR
affiliates, LWV state chapter, Clean-Elections/civic-debate orgs, top newspaper
channels. For each outlet register BOTH surfaces where they exist:
- YouTube channel RSS (`kind='youtube_channel'`,
  `feed_url='https://www.youtube.com/feeds/videos.xml?channel_id=UC…'`)
- The site's politics-section news feed (`kind='web_rss'`, the RSS/Atom URL —
  station sites are templated per chain: Gray/Nexstar/Scripps/Sinclair all
  expose section feeds; note the chain in `notes`).

ToS check at registration (spec Q8): skim the site's ToS for an explicit
AI/ML-processing bar (C-SPAN-style). If present, do NOT register the outlet and
record the finding in the pack notes. Generic no-scraping boilerplate does not
disqualify: we poll only public syndication endpoints, respect robots.txt
mechanically, and never touch auth/paywalls. Set `state`, and record the ToS
verdict in `notes`. Outlets are active on insert — the per-item triage gate is
the guard.

## Scheduler setup / upgrade

The discovery job runs via `scripts/run_scheduled_poll.sh` from the
automation-checkout clone (`~/CouncilScribe/automation-checkout`) — launchd's
git cannot read `~/Documents`, and the primary checkout's branch is a coin
flip (see the wrapper's header). The wrapper fast-forwards the clone to
origin/main before every run, so **plist changes take effect only after the
branch merges to main.**

1. One-time (already done on this Mac): create the clone per the wrapper's
   FATAL message instructions, and `mkdir -p ~/CouncilScribe/discovery`.
2. Install/refresh the plist (AFTER MERGE TO MAIN):

   ```bash
   cp scripts/launchd/vote.empowered.poll-discovery.plist ~/Library/LaunchAgents/
   launchctl unload ~/Library/LaunchAgents/vote.empowered.poll-discovery.plist 2>/dev/null
   launchctl load ~/Library/LaunchAgents/vote.empowered.poll-discovery.plist
   # Fast-forward the clone FIRST: the wrapper is read from the clone it is
   # about to update, so a wrapper change takes effect one run later unless
   # the clone is already current when the job fires.
   git -C ~/CouncilScribe/automation-checkout fetch origin \
     && git -C ~/CouncilScribe/automation-checkout checkout --detach origin/main
   launchctl kickstart gui/$(id -u)/vote.empowered.poll-discovery
   sleep 30 && tail -20 ~/CouncilScribe/discovery/poll.log
   ```

   Expected: `=== scheduled poll … ===`, a `code: <sha>` line, engine output
   ending in `DONE examined=…`, and a new row in `essentials.source_discovery_runs`
   (`trigger_kind='scheduled'`) visible in the GUI header.
3. The agenda poll's installed plist still passes no script argument — the
   wrapper defaults to `scripts/poll_agendas.py`, so it keeps working
   untouched. To make it explicit, install the now-versioned
   `scripts/launchd/vote.empowered.poll-agendas.plist` (cp + unload/load, same
   as step 2) — and apply the SAME fast-forward-the-clone-first precaution:
   installing it against a stale clone would feed the old wrapper a script
   argument it treats as poll_agendas flags (argparse exit 2) and break a job
   that currently works.

## Eval upkeep

After each triage session:

    .venv/bin/python scripts/harvest_discovery_verdicts.py
    .venv/bin/python scripts/eval_discovery_classifier.py --models haiku

Approved/ingested rows become gold-relevant examples; rejects count as
gold-irrelevant only for relevance reasons (clip-not-original / wrong-person /
tier-5). Re-run the eval and watch recall, precision, and the calibration
block (Brier + buckets). Commit the updated
`tests/fixtures/discovery_eval_real.jsonl`.

Two caveats to keep in mind reading the numbers:
- **Real-set recall is selection-biased.** Every harvestable row was already
  predicted relevant by the classifier (auto_filtered rows never reach triage),
  so real examples cannot measure a genuine miss — only the synthetic file
  carries meaningful negatives. Read the per-fixture breakdown, not just the
  combined row.
- **The fixture is merge-protected**: existing lines win on source_key so hand
  corrections survive re-harvests. To rebuild from the DB (e.g. after harvest
  logic changes), run with `--refresh`.
