import type { SectionTopicRef, SummarySection } from "./types";

// Section types substantive enough to appear in a meeting's topic outline.
// Keep in sync with src/config.py SUBSTANTIVE_SECTION_TYPES. "topic" is the
// section type produced by the interview/media summary path (event_kind
// news_clip/press_conference) — without it, interview outlines (and their topic
// labels) render empty even though the API returns tagged sections.
export const SUBSTANTIVE_SECTION_TYPES = new Set([
  "discussion",
  "public_comment",
  "consent_agenda",
  "vote",
  "topic",
]);

// Sections worth listing in the outline, in document order. Non-substantive
// sections (opening/closing/procedural/roll_call) are dropped.
export function buildOutline(
  sections: SummarySection[] | null | undefined
): SummarySection[] {
  return (sections ?? []).filter((s) =>
    SUBSTANTIVE_SECTION_TYPES.has(s.section_type)
  );
}

// Distinct topics across all of a meeting's sections, in first-seen order.
// Used for the person-page meeting bar so a collapsed meeting (or one scrolled
// to an untagged intro) surfaces the topics it covers instead of "untagged".
export function meetingTopics(
  sections: SummarySection[] | null | undefined
): SectionTopicRef[] {
  const seen = new Set<string>();
  const out: SectionTopicRef[] = [];
  for (const s of sections ?? []) {
    for (const t of s.topics ?? []) {
      if (t?.key && !seen.has(t.key)) {
        seen.add(t.key);
        out.push(t);
      }
    }
  }
  return out;
}
