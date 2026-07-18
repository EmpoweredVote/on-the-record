# Senate floor media — spike findings (Phase 5)

**Date:** 2026-07-18
**Type:** Spike (time-boxed feasibility investigation, not a build)
**Question:** Can we download **Senate floor proceedings** media for the CREC oracle, the way House floor already works via House Clerk YouTube + yt-dlp?
**Verdict:** **Senate floor is NOT reliably downloadable today** by a simple date→URL. Committee hearings are partially supported. Details + options below.

## What was tested

- `yt-dlp --list-extractors` → yt-dlp **already ships** `senate.gov:isvp`, `senate.gov`, and `CSpanCongress` extractors (v2026.06.09).
- Inspected the `senate.gov:isvp` extractor: it is **committee-oriented** — keyed on a `comm=<code>` map (`judiciary`, `commerce`, `intel`, `srs`, …) that resolves to Akamai HLS `master.m3u8`.
- Read the real Senate floor webcast player (`senate.gov/isvp/?type=arch&comm=srs&filename=stv<MMDDYY>`): the floor stream uses `comm=srs` (Senate Recording Studio) and a date-coded `filename=stv<MMDDYY>` (e.g. `stv090622`). The player JS builds the **same 4 candidate HLS URLs** the extractor does, plus a `type=arch` host `ussenate-f.akamaihd.net`, and tries each until one loads.
- Probed the CDN directly for a **known in-session date** (2026-07-16 — 110 Senate CREC turns confirm the Senate met): all 4 candidate `stv071626` HLS URLs return **404 / 508**.
- Searched the Senate floor pages (`/floor/index.htm`, `/floor/2026.htm`, `/legislative/floor_activity_pail.htm`, the archive links) for a **date→filename video index**: none found — floor activity is published as **text** (like the Congressional Record), not an indexed video archive with discoverable filenames.
- `CSpanCongress` (`c-span.org/congress/?chamber=senate&date=…`), the usual Senate-floor archive fallback: **currently broken** in yt-dlp (`Unable to extract player config` — C-SPAN changed their page).

## Findings

1. **The Senate floor archive exists** (Akamai HLS, "browse by date" per senate.gov), but the archived-floor **stream URL is resolved client-side** and the **date→filename mapping is not publicly indexed**. Guessing `stv<MMDDYY>` for recent dates 404s on the CDN. Unlike House Clerk YouTube (deterministic, discoverable video) or Senate committees (filename printed on the hearing page), a Senate floor **date alone does not yield a downloadable URL**.
2. **yt-dlp's `senate.gov:isvp` extractor works for committee hearings** (the `comm=` codes + filename come from the committee's own ISVP embed), but is **insufficient for the floor archive** — its static URL templates don't match the current floor-archive stream, and it has no date→filename resolver.
3. **C-SPAN** is the de-facto Senate-floor archive, but its yt-dlp extractor is **broken right now**, and C-SPAN's own production carries **licensing constraints** (flagged in the original oracle design — prefer government sources).

## Conclusion

The floor-proceedings oracle is **House-first by necessity**, not just by choice:
- **House floor**: ✅ works today (House Clerk YouTube, deterministic, captions).
- **Senate floor**: ⛔ blocked on **filename discovery** — the media is public and HLS-served, but there's no simple date→URL path, and the two plausible routes (senate.gov floor archive, C-SPAN) each need real work.

## Options for a future phase (if Senate floor is pursued)

Rough effort, highest-value first:

1. **Reverse-engineer the Senate floor archive's date→filename resolver (medium).** The "browse by date" archive UI must call an internal endpoint that maps a session date to its `stv…`/archive filename. This spike found the *player* but not the *archive index/API*. Next step: drive the live archive UI with browser dev-tools (network tab) to capture the archive-list request, then build a `senate_floor` resolver (date → filename → the existing ISVP HLS templates, which the player confirms are correct once the filename is right). If found, Senate floor becomes as clean as House.
2. **Fix/patch the C-SPAN `CSpanCongress` extractor (medium) + accept licensing.** Reliable floor-by-date archive, but C-SPAN production is licensed — conflicts with the "government sources" principle; treat as a fallback only.
3. **Do Senate *committee* hearings instead (small, but different content + transcript problem).** yt-dlp ISVP already resolves committee ISVP URLs. But committees need **CHRG** transcripts (delayed months-to-years — the availability gap noted in Phase 1), not CREC, so this is a separate ingestion path, not a drop-in for the floor oracle.

## Recommendation

**Defer Senate floor.** Ship House-floor coverage (fully working end-to-end as of the live E2E) and keep Senate floor as a documented follow-up gated on option 1 (the archive date→filename resolver). If Senate content is wanted sooner, option 1 is the right investment; option 2/3 are fallbacks with real caveats. No code change lands from this spike — it's a go/no-go investigation, and the recommendation is **no-go for now**, House-only.
