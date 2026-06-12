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
