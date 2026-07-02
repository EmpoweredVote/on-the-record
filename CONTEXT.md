# On the Record — Domain Glossary

Canonical terminology for the On the Record / EV ecosystem.
Keep this file free of implementation details — it is a glossary only.

---

## Core entities

**Government**
A top-level governmental entity in `essentials.governments`. Examples: "City of Los Angeles" (type=LOCAL), "State of California" (type=STATE), "Los Angeles Unified School District". One government contains one or more chambers.

**Chamber**
A deliberative or executive body within a government, in `essentials.chambers`. Examples: "City Council" (within City of Los Angeles), "Board of Supervisors" (within LA County), "Board of Education" (within LAUSD), "Governor" (within State of California). The natural anchor for council and school board events. Has a generated slug used by the roster API.

**District**
A geographic district in `essentials.districts`. Examples: "District 1" (supervisor district), "U.S. Congressional District 29". Used for geographic scoping; distinct from Chamber (a district is a geography, a chamber is a body).

**Office**
A specific seat within a chamber, in `essentials.offices`. Examples: "Councilmember District 3", "Governor". Bridges between chambers and races.

**Election**
An election event in `essentials.elections`. Has a date, type (primary/general), jurisdiction level, and state. Example: "CA 2026 Statewide General".

**Race**
A specific electoral contest within an election, in `essentials.races`. Links an election to a specific office. Example: the Governor race within CA 2026 Statewide General. The natural anchor for debate and forum events.

**Politician**
A person in `essentials.politicians` — incumbents (`is_candidate=false`) and candidates (`is_candidate=true`) share one table and one ID space. Linked from meeting speakers via `politician_id` and `politician_slug`.

**Topic**
An issue topic in `inform.compass_topics`, identified by `topic_key` (e.g. "healthcare", "taxes", "ukraine-support"). The canonical topic spine across the EV ecosystem — used by meetings, quotes, politician stances, and the Compass calibration flow. **`essentials.issues` is a dead table and is not the topic spine.**

---

## Event concepts

**Event** (also: Meeting)
A recording published into `meetings.meetings`. Has an `event_kind`, optional `title`, optional `city`, and optional entity links to a Chamber or Race.

**Event Kind**
Controlled text enum on `meetings.meetings.event_kind`. Deliberative kinds (council, school_board) anchor to a Chamber. Electoral kinds (debate, forum) anchor to a Race. Content/other kinds (news_clip, community_meeting, other) have both anchors optional.

**Meeting label** (`meeting_type` column)
A short human label for a specific event — e.g. "Regular Session", "Candidate Forum", "Debate". Required (it's shown on the site as `{city} {meeting_type} · {date}` and forms the meeting's URL slug). Distinct from [Event Kind](#event-kind): the *kind* is the controlled category (council/forum/…), the *label* is the free-text name of this particular event. The GUI pre-fills a sensible label per kind.

**Deliberative event**
An event where a public body convenes to conduct official business (agenda, votes, roll call). Event kinds: `council`, `school_board`. Anchor: Chamber.

**Electoral event**
An event focused on candidates contesting a race (debates, candidate forums). Event kinds: `debate`, `forum`. Anchor: Race.

**Interview/Media event**
A recording where a journalist, host, or organization interviews one or more identifiable subjects, or where a subject makes a public statement and takes questions. Event kinds: `news_clip` (journalist interviews a subject), `press_conference` (subject makes statement + takes questions). Both `chamber_id` and `race_id` are optional and may both be set (unlike other categories). The universal anchor is the politician(s) present in the transcript via `politician_slug` on speaker segments.

**Event org**
An organization associated with producing or hosting an event, stored in `meetings.event_orgs`. A single event may have multiple orgs (e.g. CBS and Telemundo co-hosting a debate). No role distinction — orgs are a flat list. Displayed as "Produced by X, Y" on the public site.

**Highlights**
The canonical field (formerly `key_decisions`) on `MeetingSummary` for the 3-5 most notable items from an event. For deliberative events: votes and directed actions. For interview/media events: key claims or commitments made by the subject. Neutral enough to cover all event categories.

**Clip window**
A single contiguous time range of a source recording that was transcribed and summarized, used when only part of the source is relevant (e.g. a politician interview inside a longer podcast). The Event still references and plays the *full* source recording — the clip window is provenance describing which slice was processed, not a separate artifact. Published segment and section timestamps stay in the full source's timeline (offset-corrected), never clip-relative. An absent window means the entire recording was processed (the default). Distinct from an *excerpt*: no clipped media file is ever created or hosted.

**Source key**
The normalized identity of a source recording, independent of how its URL was typed. Derived from the platform's own stable id — yt-dlp's `extractor:id` for online video (so `youtube.com/watch?v=X`, `youtu.be/X`, and `…?v=X&t=90s` all resolve to one key), the CATS TV archive id, or the absolute path for a local file. **One source key maps to at most one Event** — grabbing the same video again opens the existing Event rather than creating a duplicate. Distinct from [Clip window](#clip-window), which describes *which slice* of a source was processed, not *which* source it is.

**Body Slug**
*(deprecated)* A loose text reference to `essentials.chambers.slug` formerly stored on `meetings.meetings.body_slug`. Replaced by `chamber_id` UUID FK after backfill. Do not use in new code.

---

## Topic concepts

**Topic key**
The stable text identifier for a compass topic (e.g. `"healthcare"`). The join key between `inform.compass_topics`, `meetings.meeting_topics`, and `essentials.quotes`.

**Predicted topic**
An AI-assigned topic tag on a meeting section, not yet human-verified. Stored in `meetings.meeting_topics` with a confidence score.

**Verified topic**
A topic tag that has been confirmed by a human curator. Promotion from predicted → verified is the job of the deferred curation web app.
