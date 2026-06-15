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

**Deliberative event**
An event where a public body convenes to conduct official business (agenda, votes, roll call). Event kinds: `council`, `school_board`. Anchor: Chamber.

**Electoral event**
An event focused on candidates contesting a race (debates, candidate forums). Event kinds: `debate`, `forum`. Anchor: Race.

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
