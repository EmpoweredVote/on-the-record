# House-floor weekly automation — design (Project 1)

**Date:** 2026-09-11
**Status:** Approved design, ready for implementation planning
**Scope:** Off-Mac weekly pipeline that discovers new US House floor sessions, processes them through the CREC speaker oracle, and publishes each meeting to the database as a **draft** (not live) for later human approval.

## Context

The CREC floor oracle is complete and merged (PRs #80–93). `run_local.py --congressional-record <DATE> house` fetches the Congressional Record for a House floor session, resolves diarized speakers to real House members, links them to an essentials `politician_id`, and skips the local speaker-ID LLM on CREC runs. It is live-validated on real House floor audio. See the `congressional-floor-proceedings` memory note for detail.

Today this runs only from the operator's Mac (CLI, or the local GUI launcher). The goal is to run it weekly, off the Mac, with no local GPU (the GPU already offloads to Modal via `--compute modal`).

This design is **Project 1** of two. Project 2 (a web admin review panel in the ev-accounts repo) is spun out into its own spec. The two are separated by a single interface: **draft meeting rows in the database, carrying a confidence-gate verdict.** Project 1 produces those rows; Project 2 consumes them.

### Established constraints (not re-derived here)

- The driver (download, transcribe, CREC, publish) needs no local GPU. It can run on any host.
- Publishing writes to the ev-accounts/Supabase database. A live publish also triggers a Render deploy hook.
- Auto-publish is intentionally gated (`src/quality.py` confidence coverage). Floor attributions want human review before going live. So the automation target is **processed + queued for review as a draft**, not auto-live.
- Senate floor media is a documented no-go (PR #88 spike). House-only.
- All LLM traffic now runs on OpenRouter (see the `openrouter-llm-migration` memory note). CREC floor runs skip the speaker-ID LLM regardless.

## Non-goals

- Senate floor coverage.
- Auto-live gate policy (high-coverage floor runs going live without a human). Default is always-draft; a human promotes. A gate policy can be added later.
- The web admin review panel (Project 2).
- Interactive per-segment speaker relabeling on the cloud host. That stays a local escape hatch on the operator's Mac.
- Per-meeting summaries on floor auto-runs (deliberately off for now).

## Architecture

Five pieces. Each is independently testable.

### 1. `draft` meeting status

The `meetings.meetings.status` column already exists. The publisher currently always writes `"published"` (`src/publish.py` `_upsert_meeting`, the two `"published"` literals at lines ~281 and ~319).

- Introduce a `draft` status value that the publisher can write instead of `"published"`.
- **Safety-critical companion change (cross-repo, ev-accounts + `web/`):** the meeting-list and meeting-detail read paths must return only `status = 'published'` rows. Without this filter a draft would leak onto the public site. This filter is **in scope for Project 1** and must land before the first draft is written.
- The `draft` row is the exact contract the Project 2 panel reads (meeting fields, speaker rows, segments, and the stored gate verdict).

### 2. `--publish-as-draft` mode on `run_local.py`

A new flag. It:
- routes the publish path to write meeting/speaker/segment rows with `status = 'draft'`, and
- does **not** trigger the Render deploy hook (a draft needs no site rebuild).

The existing publish behavior (immediate `published` + deploy) is unchanged when the flag is absent.

### 3. Discovery + dispatch script: `src/floor_dispatch.py`

The core new module, run by the weekly job. Steps:

1. **List videos.** Run yt-dlp against the US House Clerk channel streams. Parse each session date from the title (`US House Floor Proceedings (Weekday, Month DD, YYYY)`).
2. **Confirm a floor session.** For each candidate date, use `src/govinfo.py` to check that package `CREC-<date>` has `granuleClass = HOUSE`. Skip recess days.
3. **De-duplicate.** Query the database for existing House-floor meeting slugs of **any** status (including `draft`). Skip dates already processed. The database is the source of truth; the cloud runner keeps no local state.
4. **Dispatch.** For each new `(date, video_url)`, run `run_local.py` as a **separate subprocess** with the fixed floor flags plus `--publish-as-draft`. Collect a per-session result (completed / skipped / failed).
5. **Bound the work.** A `--since <date>` and `--max-sessions <n>` guard so the first run does not process a long backlog.

**Subprocess, not `--batch`:** the batch path's per-entry `Namespace` (`run_local.py` `_run_batch`, ~line 1872) has no `congressional_record` field, so `--batch` cannot pass `--congressional-record <date> house` today. One subprocess per session is simpler and isolates failures — one bad session does not stop the rest.

The script is idempotent: safe to re-run; it processes only new sessions.

### 4. The per-session command

```
python run_local.py --input <house-clerk-youtube-url> --date <YYYY-MM-DD> \
  --event-kind other --meeting-type "House Floor" \
  --congressional-record <YYYY-MM-DD> house --compute modal \
  --no-review --publish-as-draft
```

Summaries are off (the existing batch behavior of `skip_summary=True` is the model). No `OPENROUTER_API_KEY` is required for these runs.

### 5. GitHub Actions workflow: `.github/workflows/house-floor-weekly.yml`

- **Triggers:** `schedule` (weekly) plus `workflow_dispatch` (manual).
- **Schedule:** Saturday morning US Eastern, so CREC has published for the whole week (CREC lag ≈ one day). Use `cron: '0 13 * * 6'` (13:00 UTC = 08:00 EST / 09:00 EDT, Saturday).
- **Steps:** checkout, set up Python, install project dependencies, install ffmpeg and yt-dlp, write Modal credentials, run `src/floor_dispatch.py` with the backlog guards.
- A `concurrency` group stops overlapping runs. A job `timeout-minutes` caps runtime.
- **Secrets (GitHub encrypted secrets):**
  - `DATABASE_URL` (must use the IPv4 pooler host — see the `supabase-db-connection` memory note)
  - `GOVINFO_API_KEY`
  - `HF_TOKEN` and/or `PYANNOTE_AI_KEY` (diarization access)
  - `MODAL_TOKEN_ID` + `MODAL_TOKEN_SECRET` (GPU offload)
  - optional `YOUTUBE_COOKIES` (see risk below)
  - **not** `RENDER_DEPLOY_HOOK_URL` — drafts skip the deploy.

### 6. Interim review, until the Project 2 panel

Two small database-backed commands on `run_local.py`, so the operator can review and go live from any machine:

- `--list-drafts`: list `draft` House-floor meetings with their gate coverage score.
- `--promote <slug>`: flip one meeting `draft → published` and trigger the deploy hook.

The Project 2 admin panel later replaces these.

## Key risk and its mitigation

**yt-dlp downloading from a datacenter IP.** YouTube increasingly challenges cloud IPs. This is the single biggest risk to the whole automation, and it applies to any off-Mac host.

- **De-risk first.** Before trusting the schedule, run one manual `workflow_dispatch` and confirm yt-dlp downloads a House Clerk video from a GitHub runner.
- **Fallback.** If the runner IP is blocked, add a `YOUTUBE_COOKIES` secret (a `cookies.txt` from a signed-in browser) and pass yt-dlp `--cookies`. Document the fallback in the workflow.

## Data flow

```
GitHub Actions (Saturday, weekly)
  └─ src/floor_dispatch.py
       ├─ yt-dlp: US House Clerk streams → [(date, video_url), ...]
       ├─ src/govinfo.py: CREC-<date> HOUSE? → keep session dates
       ├─ DB query: existing house-floor slugs (any status) → drop dupes
       └─ for each new session:
            subprocess: run_local.py ... --congressional-record <date> house
                        --compute modal --no-review --publish-as-draft
              ├─ yt-dlp download + clip
              ├─ Modal diarization/transcription
              ├─ CREC oracle: diarized labels → House members → politician_id
              ├─ confidence gate: verdict stored on the meeting
              └─ publish rows with status='draft' (NO deploy hook)

Operator (any machine, any later time)
  └─ run_local.py --list-drafts        # review coverage
  └─ run_local.py --promote <slug>     # draft → published + deploy hook
       (later replaced by the Project 2 admin panel)
```

## Testing

- `floor_dispatch.py`: unit tests with injected yt-dlp listing output, injected govinfo fetch, and an injected DB dedupe set. Assert the correct new `(date, url)` set and the backlog guards. Follow the existing offline-fixture pattern used across `src/govinfo.py` and the CREC modules.
- `--publish-as-draft`: assert the publisher writes `status = 'draft'` and does not call the deploy hook. Assert the default path still writes `published` + deploy.
- `--list-drafts` / `--promote`: assert the DB queries and the status flip; assert `--promote` triggers the deploy hook.
- Web/API filter: assert non-`published` rows are excluded from the meeting-list and meeting-detail read paths.
- End-to-end de-risk: one manual `workflow_dispatch` run on a real recent House-floor date, verifying a `draft` row appears and stays off the public site.

## Open follow-ups (later, not this project)

- Project 2: the web admin review panel (ev-accounts).
- A high-coverage auto-live gate policy for floor runs.
- Summaries on floor drafts, if wanted.
- Senate floor, gated on the date→filename resolver from the PR #88 spike.
