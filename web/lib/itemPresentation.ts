// Citizen-language presentation for agenda items. Neutral voice — never
// editorialize outcomes (spec: provenance-first, rep-citable pages).
import type { ItemSpeaker, ItemVoteRecord } from "./types";

export function outcomeLabel(outcome: string | null): string | null {
  switch (outcome) {
    case "passed":
      return "Passed";
    case "failed":
      return "Failed";
    case "continued":
      return "Continued to a later meeting";
    case "pulled":
      return "Pulled from the agenda";
    case "no-action":
      return "No action taken";
    default:
      return null;
  }
}

export interface StateBadge {
  label: string;
  tone: "upcoming" | "happened";
}

export function itemStateBadge(
  status: "upcoming" | "happened",
  meetingDate: string
): StateBadge {
  if (status === "happened") {
    // Noon avoids the UTC-midnight-rolls-back-a-day pitfall for date-only input.
    const d = new Date(`${meetingDate}T12:00:00`);
    const formatted = new Intl.DateTimeFormat("en-US", {
      month: "long",
      day: "numeric",
      year: "numeric",
    }).format(d);
    return { label: `From the meeting on ${formatted}`, tone: "happened" };
  }
  return { label: "Coming up", tone: "upcoming" };
}

// The decision banner: the single most important fact on a happened item,
// composed answer-first. Headline prefers the clerk-recorded result verbatim
// ("Failed 4–4" — outcome AND margin) over the bare outcome word; the last
// vote is the dispositive one (reconciler writes motions in order). Tone
// drives the banner tint; the glyph+words always carry the meaning so color
// is never the only signal.
export interface OutcomeHeadline {
  text: string;
  glyph: string | null;
  tone: "passed" | "failed" | "continued" | "neutral";
}

export function outcomeHeadline(
  outcome: string | null,
  votes: { result: string }[]
): OutcomeHeadline | null {
  const dispositive = votes.length > 0 ? votes[votes.length - 1] : null;
  const text = dispositive?.result || outcomeLabel(outcome);
  if (!text) return null;
  const tone =
    outcome === "passed" || outcome === "failed" || outcome === "continued"
      ? outcome
      : "neutral";
  const glyph =
    outcome === "passed" ? "✓" : outcome === "failed" ? "✗" : outcome === "continued" ? "→" : null;
  return { text, glyph, tone };
}

// Vote-record positions bucketed into citizen-facing tabs. The reconciler
// writes clerk-memo vocabulary (Ayes/Nays/Abstain); federal votes use
// yea/nay; be tolerant of both.
export type VoteBucketKey = "for" | "against" | "abstain" | "other";

const FOR_POSITIONS = new Set(["aye", "ayes", "yea", "yes", "for", "y"]);
const AGAINST_POSITIONS = new Set(["nay", "nays", "no", "against", "n"]);
const ABSTAIN_POSITIONS = new Set(["abstain", "abstained", "abstention", "present"]);

export function votePositionBucket(position: string): VoteBucketKey {
  const p = position.trim().toLowerCase();
  if (FOR_POSITIONS.has(p)) return "for";
  if (AGAINST_POSITIONS.has(p)) return "against";
  if (ABSTAIN_POSITIONS.has(p)) return "abstain";
  return "other";
}

export interface VoteBucket {
  key: VoteBucketKey;
  label: string;
  records: ItemVoteRecord[];
}

const BUCKET_LABELS: Record<VoteBucketKey, string> = {
  for: "For",
  against: "Against",
  abstain: "Abstained",
  other: "Other",
};

// Fixed For/Against order (both always shown so "Against 0" is visible, the
// CalMatters pattern); abstain/other tabs appear only when non-empty.
export function voteBuckets(records: ItemVoteRecord[]): VoteBucket[] {
  const byKey: Record<VoteBucketKey, ItemVoteRecord[]> = {
    for: [],
    against: [],
    abstain: [],
    other: [],
  };
  for (const r of records) byKey[votePositionBucket(r.position)].push(r);
  return (Object.keys(byKey) as VoteBucketKey[])
    .filter((k) => k === "for" || k === "against" || byKey[k].length > 0)
    .map((k) => ({ key: k, label: BUCKET_LABELS[k], records: byKey[k] }));
}

// Who-spoke grouping: legislators/officials (linked to a politician record)
// vs. everyone else. "Non-speaker" is a pipeline label for non-speech audio,
// not a person — drop it.
export interface SpeakerGroups {
  officials: ItemSpeaker[];
  others: ItemSpeaker[];
}

export function groupItemSpeakers(speakers: ItemSpeaker[]): SpeakerGroups {
  const officials: ItemSpeaker[] = [];
  const others: ItemSpeaker[] = [];
  for (const s of speakers) {
    if (s.name === "Non-speaker") continue;
    (s.politician_id != null ? officials : others).push(s);
  }
  return { officials, others };
}
