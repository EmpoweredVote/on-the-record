# Texas civic spaces page: data needed to show the CNN & Jubilee town hall

Note only. Nothing here is built. Written 2026-10-21 by the Talarico town-hall ingest session.

## What the source is

- **Event:** "CNN & Jubilee Town Hall with James Talarico", recorded live in Fort Worth, TX on 2026-10-14 (aired on CNN).
- **Official full video:** Jubilee's YouTube channel (channel id `UCJjSDX-jUChzOEyok9XYRJQ`), uploaded 2026-10-18, title "James Talarico Debates Texas Voters: Jubilee x CNN Townhall", https://www.youtube.com/watch?v=VnurpLQd4c0 (83 min).
- **Format:** a town hall, not a debate. Talarico was the only candidate. Ken Paxton (R) was invited and did not appear (moderator Erin Burnett said the invitation stands). About 60 Texas voters (roughly 20 D, 20 I, 20 R) asked questions, then 7 voters debated him one-on-one at a "table for two".
- **On-the-record meeting id (local):** `2026-10-14-cnn-jubilee-town-hall-james-talarico`, event_kind `forum`, event_orgs CNN + Jubilee, race_id `216ead27-9e86-49f2-b21c-af659d114faf` (U.S. Senate Texas 2026). Not yet published to `meetings.*`.

## Data the page would need

1. **Event record** (exists once the meeting is published): title, date, location, producing orgs (CNN, Jubilee), event kind, duration, thumbnail, the YouTube id for the embed (`meetings.video_url` stores the bare id for YouTube), and the race it belongs to.
2. **Who was invited vs. who appeared.** This is the key neutrality field. The page must say plainly that this was a one-candidate event and that the opponent was invited and declined. Today there is no field for "invited, did not appear". It would need one (for example an event-participants list with status `appeared` / `invited_declined` / `not_invited`), so a single-candidate video is never shown as if it were a debate.
3. **Chapters.** Jubilee's own timecodes (Intro, Sit-down, voter questions, 7 debate rounds: abortion, national debt, Christianity, foreign wars, trans rights, elections, Israel, "Earn My Vote"). These map to the repo's existing YouTube-chapter → agenda support. Each chapter needs a start time and a neutral label.
4. **Topic index.** For each chapter or exchange: the topic key(s) from `inform.compass_topics` and a deep link (`&t=<seconds>s`). This lets the page say "jump to his answer on data centers" without paraphrasing him.
5. **Linked stances.** The `essentials.quotes` rows sourced from this video (same politician_id + source URL), so the page can show "positions stated in this video" next to the player and link to the candidate's full stance list.
6. **Neutral context blurb.** One or two sentences per video written to the house neutrality standard (who produced it, format, who was in it). No quality judgments ("strong on substance" stays internal).
7. **Audience privacy.** The voters on stage are private people. The page should not index their names or make them searchable. Show them as "voter (independent)" etc., if at all. Only the transcript inside the player would carry names they said aloud.
8. **Source/licensing status.** A flag that the video is embeddable and that we have cleared any reuse question. CNN co-produced this event; see the licensing flag in the ingest summary.
9. **Balance signal for the race.** Because the page sits on a state space, it should be able to list the other candidates' long-form videos for the same race (or say none exist), so one candidate's town hall does not stand alone without context.
