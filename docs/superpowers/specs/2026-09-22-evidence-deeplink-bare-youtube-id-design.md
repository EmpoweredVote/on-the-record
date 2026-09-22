# Evidence transcript lane — deep links from bare YouTube ids

**Status:** Bug fix (2026-09-22). **Repo:** on-the-record only — `src/evidence/pipeline.py`
`_deep_link`, tests. **No schema change; no ev-accounts change; no prod write.**
**Follows:** the transcript lane (`2026-09-21-evidence-transcript-lane-design.md`, PR #252).

## Bug

`_deep_link` builds `base = source.video_url or source.source_url`. For YouTube meetings,
`meetings.meetings.video_url` is **not a URL**: it holds the bare 11-char video id. This is by
design — `publish.resolve_playback` returns `("youtube", <id>)`, publish writes that as
`video_url`, and the web app passes it as `videoId` to the player. Live DB (2026-09-22): all 140
`playback_kind='youtube'` meetings store a bare id; `file`/`audio`/`hls` meetings (17) store full URLs.

Because a bare id contains neither `youtube.com` nor `youtu.be`, `_deep_link` took the non-YouTube
branch and returned e.g. `r1EvtOp10Uk#t=450` — not a URL, and `#t=` is not YouTube's seek param. The
review HTML renders only http(s) links, so those items showed no source link. Measured in the spike
artifacts: Raman 138/191 items, Bass 150/175.

## Fix

In `_deep_link`, when `base` fully matches `[A-Za-z0-9_-]{11}`, expand it to
`https://www.youtube.com/watch?v=<id>` before the timestamp logic. The existing YouTube branch then
yields `…watch?v=<id>&t=<s>s`, and the no-segment fallback yields the plain watch URL. Full URLs and
non-YouTube bases keep their current behavior.

Fixed in `_deep_link` (the only consumer that needs a URL) rather than by changing what
`TranscriptSource.video_url` carries, so the field keeps the same meaning as the DB column.

## Verification

- Regression tests in `tests/test_evidence_pipeline.py`: bare id with a matching segment (id with a
  leading `-`); bare id with no matching segment → plain watch URL.
- Re-derived the deep links for both spike artifacts from live `fetch_transcript_sources`
  (read-only): 138/138 and 150/150 bare links become watch URLs with identical timestamps; the 53/25
  buzzsprout `.mp3` links are unchanged.
- Prod `inform.evidence_items` (read-only, 2026-09-22): 131 rows, all http(s), **0 bare links**, 0
  transcript-lane rows. Nothing to repair.
