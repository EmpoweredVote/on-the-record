# Clip window is ingest-time only; published timestamps are source-absolute

## Context

Some sources contain only a small relevant section — e.g. a politician interview inside a long podcast episode. We want to transcribe and summarize just that section without representing the whole episode, while keeping On the Record's core promise: every statement links to the exact moment in the *full* source video.

## Decision

A clip window (`--clip START END`) is treated purely as an **ingest-time compute optimization**, never as a domain artifact:

- ffmpeg cuts the contiguous window out during the existing `normalize_audio` pass (accurate/decode seek, so the cut is frame-exact and the start offset is authoritative). `audio.wav` *is* the clip.
- The pipeline runs **clip-local** (0-based) internally — diarization, transcription, and the review clip players all operate against the 25-minute `audio.wav` unchanged.
- The start offset is **persisted once** on the meeting (`clip_start_seconds` / `clip_end_seconds`; both stored, `NULL` = whole recording). `--resume` reads it from state; a conflicting re-pass hard-errors, mirroring the `--body` pattern.
- The offset is added back **only at output boundaries** — publish DB writes, SRT, and markdown — so all published segment/section timestamps live in the **full source's timeline**.
- The site always plays and links the **full source** (auto-starting at `clip_start`, no end cap), with a detail-page provenance note. No clipped media file is created or hosted — this is a *clip window*, not an *excerpt*.

## Consequences

- A future reader will see `audio.wav` is ~25 min while published timestamps read 1380s+; this offset is deliberate and recorded here.
- The window must be a single contiguous range — the offset mapping is linear, so non-contiguous selections (e.g. excising a mid-interview ad) are unrepresentable. Ads inside the window are simply included. Two separate interviews in one source become two meetings.
- The feature threads through four repos: on-the-record (pipeline), supabase (migration), ev-accounts (API serialization), web (player + note).
