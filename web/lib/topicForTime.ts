import type { SummarySection, SectionTopicRef } from "./types";

// Segments carry no topic; topics live on meeting-summary sections, each with a
// [start_time, end_time) window. Map a moment (a segment's start time) to the
// topic of the section that contains it. Returns the section's first topic, or
// null when the moment falls in an untagged / non-substantive section (or a
// section with no time bounds). Pipeline sections don't overlap by construction.
export function topicForTime(
  sections: SummarySection[] | null | undefined,
  seconds: number
): SectionTopicRef | null {
  if (!sections) return null;
  const hit = sections.find(
    (s) =>
      s.start_time != null &&
      s.end_time != null &&
      seconds >= s.start_time &&
      seconds < s.end_time
  );
  if (!hit || !hit.topics || hit.topics.length === 0) return null;
  return hit.topics[0];
}
