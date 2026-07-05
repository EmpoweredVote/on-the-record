import type { CSSProperties } from "react";
import type { SummarySection } from "./types";

// Distinct, reasonably spaced hues. Saturation/lightness/alpha are applied in
// CSS (theme-aware, see .skimChip--topic / .skimTurn--topic in globals.css);
// only the hue is chosen here so one topic reads as one color in both themes.
export const TOPIC_HUES = [210, 150, 35, 0, 275, 185, 320, 95, 25, 255, 170, 340];

// Assign each topic_key a hue in first-seen order across the given section
// groups (one group per meeting). Consistent within a page; cycles through the
// palette if a page has more than TOPIC_HUES.length distinct topics.
export function buildTopicHueMap(
  sectionGroups: (SummarySection[] | undefined)[]
): Map<string, number> {
  const map = new Map<string, number>();
  let n = 0;
  for (const sections of sectionGroups) {
    for (const s of sections ?? []) {
      for (const t of s.topics ?? []) {
        if (t?.key && !map.has(t.key)) {
          map.set(t.key, TOPIC_HUES[n % TOPIC_HUES.length]);
          n++;
        }
      }
    }
  }
  return map;
}

// Inline style that feeds a topic's hue to the CSS (--th). Returns undefined
// when there's no hue, so callers can spread it unconditionally.
export function topicHueStyle(hue: number | undefined): CSSProperties | undefined {
  if (hue == null) return undefined;
  return { "--th": String(hue) } as CSSProperties;
}
