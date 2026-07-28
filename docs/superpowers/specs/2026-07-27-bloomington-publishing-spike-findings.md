# Bloomington / Monroe County Publishing Reality — Spike Findings

**Date:** 2026-07-27 (Phase 0 of `2026-07-27-bloomington-item-centric-civic-coverage-design.md`)
**Method:** live web research; every claim carries a URL; unverified items listed at the end.

## 1. Agenda source — API client, not scraper

Bloomington runs its **own open-source "OnBoard" application** (AGPL,
https://github.com/City-of-Bloomington/OnBoard) behind a Drupal front-end. No vendor
portal (no Granicus/Legistar/CivicClerk/PrimeGov).

- Human-facing listing: https://bloomington.in.gov/council/meetings (Upcoming /
  past-90-days / per-year archive pages).
- **Machine-readable JSON API, verified working:**
  `https://bloomington.in.gov/onboard/meetings?format=json&start=YYYY-MM-DD&end=YYYY-MM-DD`
  → meetings keyed by date/time with `id`, `title`, `location`, ISO-8601
  `start`/`end`, Google Calendar `eventId`/`htmlLink`, and a `files` map keyed by
  type (`Agenda`, `Packet`, `Minutes`, `Memorandum`, notices), each file carrying a
  download `url`, `filename`, `mime_type`, and **`created`/`indexed`/`updated`
  timestamps** (posting lag directly observable). Pulled live for Jan–Dec 2026.
- Council also publishes a Google Calendar (linked from
  https://bloomington.in.gov/council); OnBoard eventIds indicate the JSON is synced
  with it.
- **Agendas are PDFs**: a short Agenda PDF + a full Packet PDF (agenda + legislation
  text + staff memos); **addenda PDFs appear up to meeting day**. Digitally
  generated, not scanned.

**Real example** — Regular Session 2026-07-29: agenda
https://bloomington.in.gov/onboard/meetingFiles/17202/download, packet
https://bloomington.in.gov/onboard/meetingFiles/17203/download. Stable 10-section
template:

1. Roll Call · 2. Agenda Summation · 3. Minutes for Approval (lettered) ·
4. Reports (Council members / Mayor+Clerk+Offices+Boards / Committees / **Public**) ·
5. Appointments to Boards and Commissions · 6. **Legislation for First Readings** ·
7. **Legislation for Second Readings and Resolutions** · 8. Additional Public
Comment · 9. Council Schedule · 10. Adjournment.

Legislation items are lettered under sections 6/7 as
**`Ordinance 2026-16 – Title` / `Resolution 2026-14 – Title`** (format `YYYY-NN`)
with a "Council Sponsor" line.

## 2. Timing

Stated practice: packets go out "the Friday before each Council meeting"
(https://bloomington.in.gov/council). Verified via OnBoard `created` timestamps:
normally 5 days ahead (Fri → Wed), but **2026-07-29's agenda posted Mon 07-27 —
only 2 days ahead** (≈ the Indiana Open Door Law 48h floor). Addenda routinely land
1–2 days out and even the afternoon of the meeting.

**Adapter rule: poll from ~6 days out; keep re-polling through meeting time.**

## 3. Legislation records — a bonus oracle

- Per-item detail pages, joinable by the exact number printed on the agenda:
  `https://bloomington.in.gov/council/legislation/{Type}/{Year}/{YYYY-NN}` — e.g.
  https://bloomington.in.gov/council/legislation/Ordinance/2026/2026-14 contains
  title, synopsis, sponsor, amends-code flag, and **final action with roll call:
  "Final 2026-06-10 pass 7-2 (Asare, Rosenbarger)"**, plus full-text PDF (served
  from OnBoard, e.g. https://bloomington.in.gov/onboard/legislationFiles/5955).
- No JSON found for legislation (`/onboard/legislation?format=json` → 404); the
  OnBoard repo suggests richer routes may exist — unverified.
- These pages are an independent **votes/outcome oracle** for the Phase-3/4
  cross-check and the legislation join.

## 4. Minutes — slow reconcile signal only

Minutes PDFs attach to meetings in OnBoard, **4–7 months late** (verified: Sept 2025
minutes uploaded 2026-02-26; Dec 2025 minutes 2026-04-10; no 2026 Regular Session
minutes posted as of late July 2026). Earlier weak signal: a "Regular Session Memo"
(Memorandum file type) posts ~6–9 days after each meeting. Confirms the design's
minutes-as-Pass-2-reconcile stance; the memo is a faster reconcile input worth
parsing.

## 5. Video — public domain, direct MP4, free transcripts

**CATS** (Community Access Television Services, a Monroe County Public Library
department, https://catstv.net/about.php):

- Archive back to 1980 (https://catstv.net/government.php): Bloomington City Council
  + committees, Plan Commission, County Commissioners, County Council, school
  boards, Ellettsville.
- **Direct progressive MP4 downloads** (not HLS-only) from Azure Blob Storage with a
  predictable naming convention: `https://catstv.blob.core.windows.net/videoarchive/B_CC_YYMMDD.m4v`
  (verified from the raw HTML of https://catstv.net/m.php?q=15953, the 2026-06-03
  council meeting), plus poster JPG.
- **Machine transcripts published per meeting**: `B_CC_YYMMDD_subtitles.vtt` and
  `B_CC_YYMMDD_transcript.txt` on the same container.
- **Licensing: the inverse of C-SPAN.** CATS' policy page states programming
  produced by CATS staff is CATS copyright "**except government meetings which are
  in the public domain**" (https://catstv.net/about.php). No AI/ML clause, no
  restrictive terms-of-use found anywhere on catstv.net. **Phase 3 video sourcing is
  unblocked.**
- The City also uploads full meeting video to its own YouTube channel
  (https://www.youtube.com/@citybloomington, within days) — but YouTube platform ToS
  constrains downloading; **prefer CATS blob MP4s for the pipeline**, YouTube as
  fallback/verification.

## 6. Public comment rules (adapter-encoded facts)

Source: "Rules for Making Public Comment on Agenda & Non-Agenda Items" (adopted
2024-06-05, amended 2025-08-06):
https://bloomington.in.gov/sites/default/files/2025-11/Rules%20for%20Making%20Public%20Comment%20on%20Agenda%20-%20Nov%202025_0.pdf

- Two general (non-agenda) comment periods per Regular Session: "Reports from the
  Public" near the start (20 min cumulative) and "Additional Reports from the
  Public" at the end (25 min). A resident may speak at **one** of the two, once,
  **max 3 minutes**; speakers state their name for the record (useful for
  speaker-ID later).
- Per-item public comment occurs during legislation readings (rules doc + agenda
  footnotes; exact second-reading mechanics are council procedure — see
  unverified).
- No advance sign-up; comment from the podium; statements only.

## 7. Cadence & structure

- **Regular Sessions: first and third Wednesdays, 6:30 pm**, Council Chambers Room
  115 + Zoom — with real-world drift in 2026 (June 3/10, July 22/29) plus Special
  Sessions and a Joint City-County session (June 11). Enumerate from the JSON API,
  don't assume the rule.
- Committees exist as standing/ad-hoc (Fiscal Committee etc.), not a regular
  Committee of the Whole in 2026; treat committee coverage as future config.
- The agenda template (§1) is highly stable — a section-header-anchored parser is
  appropriate.

## 8. Monroe County sanity check (bodies №2/№3)

- **Board of Commissioners** (Thursdays ~10 am): agenda/packet PDFs listed on
  per-year pages at
  https://www.in.gov/counties/monroe/Departments/commissioners/agendas-and-packets/,
  but PDFs live on **opaque SharePoint share links** — must scrape the year page.
  No feed. CATS covers video (same public-domain path).
- **County Council** (bi-weekly Tuesdays): same pattern at
  https://www.in.gov/counties/monroe/Departments/council/county-council-meeting-agenda/.
  CATS covers video.
- Conclusion: the adapter contract must accommodate both **structured-API** (city)
  and **scrape+PDF** (county) implementations — as designed.

## Unverified / manual follow-ups

- Whether OnBoard exposes a legislation/votes JSON API under unadvertised routes
  (read the GitHub repo's route definitions).
- Exact per-item comment mechanics at second readings (procedure, not published
  rules).
- Attribution/terms of the city's YouTube uploads; whether CATS VTT/TXT transcripts
  exist for all meetings or only recent ones.
- Any formal MCPL/CATS paper reproduction policy beyond about.php (nothing
  restrictive found online).
- Whether posting ever slips below 48h (one 2-day observation).
- Long-term stability of Monroe County SharePoint share links (token revocation
  risk).

## Adapter implications (summary)

City = **API client + templated-PDF parser**: enumerate via OnBoard JSON (filter
`title` prefix "Common Council"), download Agenda/Packet PDFs by stable numeric URL,
schedule re-polls off `created` timestamps, parse the 10-section template with
`Ordinance|Resolution YYYY-NN – Title` item lines, join legislation refs to the
detail pages for sponsor/outcome. Minutes → slow reconcile; session memo → fast
weak reconcile. Video/transcripts from CATS blob, license-clean. County =
same contract, dumber fetcher (year-page scrape + SharePoint PDFs).
