# On the Record

**Searchable, speaker-attributed transcripts of public meetings — synced to the original video.**

City council meetings, debates, and candidate forums are public, but what was actually said in them is hard to find, hard to search, and hard to cite. On the Record turns a recording (or just a URL) into a verifiable transcript where every statement is attributed to a named speaker and linked to the exact moment in the source video.

Inspired by [CalMatters' Digital Democracy](https://calmatters.digitaldemocracy.org). Part of [Empowered Vote](https://github.com/EmpoweredVote).

## How it works

```
Video URL or file
      │
      ▼
┌─────────────────────────────────────────────────┐
│ Transcription pipeline (Python)                 │
│ ingest → diarize → transcribe → identify        │
│ speakers → enroll voices → export → publish     │
└─────────────────────────────────────────────────┘
      │                          │
      ▼                          ▼
 transcript.md / .json     Supabase (Postgres)
 subtitles.srt                   │
                                 ▼
                    ┌─────────────────────────┐
                    │ Web app (Next.js)       │
                    │ video + synced          │
                    │ transcript, search,     │
                    │ timestamp deep links    │
                    └─────────────────────────┘
```

**The pipeline** ([docs/pipeline.md](docs/pipeline.md)) diarizes speakers with pyannote, transcribes with Whisper, and identifies *who* each speaker is using three layers: voice profiles enrolled from past meetings, rule-based patterns (roll calls, chair recognition, self-introductions), and a local LLM reading conversational context. Low-confidence speakers are flagged for a guided human review.

**The site** (`web/`) plays the *original* video — YouTube via the official embed API, community-TV archives (e.g. CATS TV) via their direct media files — alongside the transcript:

- The current segment highlights as the video plays; click any timestamp to jump the video there
- Share a link to an exact moment: `/meetings/<id>?t=1837#seg-412`
- Search within a transcript with match navigation
- Meetings without an embeddable source still get a full transcript and a citation link

## Quick start

### Process a meeting

```bash
pip install -r requirements.txt
python run_local.py --input "https://www.youtube.com/watch?v=..." \
  --city Bloomington --date 2026-02-18 --meeting-type "Regular Session"
```

See [docs/pipeline.md](docs/pipeline.md) for prerequisites (Hugging Face token, pyannote model terms), roster-guided speaker identification, the review workflow, and re-running stages.

### Publish it to the site

```bash
# .env.local: SUPABASE_URL, SUPABASE_SECRET_KEY
python run_local.py --publish-meeting <MEETING_ID>   # or add --publish to a pipeline run
```

Publishing is idempotent — re-publish any time speaker names improve.

### Run the site

```bash
cd web
cp .env.local.example .env.local
npm install && npm run dev   # http://localhost:3000
```

## Linking speakers to politicians

Once a speaker is *named*, they're linked to their [essentials](https://github.com/EmpoweredVote/essentials) politician (`politician_id`) so they show up on `/people/<id>`. High-confidence matches link **automatically** during a run; everything else is handled with the commands below. Most accept `--dry-run` (preview, writes nothing); add `--publish-anyway` because re-publishing already-vetted meetings shouldn't be re-blocked by the review gate (most meetings have no `review_status`).

**Auto-linking (automatic).** During processing, any named speaker with a single, unambiguous essentials match is linked automatically and tagged `id_method="auto_linked"` — no action needed. Audit or revert later by looking for that tag.

**Link one person everywhere they appear:**

```bash
# Link "Steve Hilton" across every meeting he's in, fold his voice profile, re-publish
python run_local.py --relink-person "Steve Hilton" --publish-anyway

# Ambiguous name (several essentials matches)? pass the explicit id:
python run_local.py --relink-person "Katie Porter" --to-id <politician_uuid> --publish-anyway

# Preview first, or also rebuild the static site afterward:
python run_local.py --relink-person "Steve Hilton" --dry-run
python run_local.py --relink-person "Steve Hilton" --publish-anyway --deploy
```

**Bulk-link the backlog (scan → edit → apply):**

```bash
# 1. Enumerate every unlinked named speaker into an editable review file (with suggested matches)
python run_local.py --bulk-relink-scan --out review.yaml

# 2. Open review.yaml. Rows with one clear match are pre-set decision: link.
#    For rows marked `review`, pick a candidate id from the listed `candidates`
#    and change decision to `link` (or `skip` for moderators / non-politicians).

# 3. Apply your approved links — relinks, folds voice profiles, re-publishes,
#    auto-resolves debate/forum race associations. Preview, then run for real:
python run_local.py --bulk-relink-apply review.yaml --publish-anyway --dry-run
python run_local.py --bulk-relink-apply review.yaml --publish-anyway
```

**Re-sync everything (e.g. after a schema or derivation change):**

```bash
python run_local.py --republish-all --dry-run   # preview: which published meetings would resync
python run_local.py --republish-all              # re-publish all published meetings, one web deploy
python run_local.py --republish-all --reenroll   # also rebuild the voice-profile DB
```

## Repository layout

```
run_local.py        Pipeline CLI
src/                Pipeline stages (ingest → … → export, publish)
supabase/           Database migrations (civic schema)
web/                Next.js site
docs/pipeline.md    Pipeline operator guide
docs/web-roadmap.md Where this is going: people pages, cross-meeting
                    search, appearances on politician profiles
bench/              Diarization model benchmark harness (Modal)
tests/              pytest suite
```

## Roadmap

People/roster pages, per-person "everywhere they spoke" appearances, cross-meeting full-text search, and an API for embedding appearance cards on [essentials](https://github.com/EmpoweredVote/essentials) politician profiles — details in [docs/web-roadmap.md](docs/web-roadmap.md).
