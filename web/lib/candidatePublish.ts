import type { Candidate } from "./types";

// The publish-batch handoff the curation page hands to the publish-quotes skill.
// It carries EVERY candidate (not just the starred pick). Topic labels stay
// free-text (reconciled to a compass topic_key at publish); source_url is the
// base meeting url and timestamp_seconds is separate (the script appends &t=Ns).
export interface PublishQuote {
  text: string;
  topic_label: string;
  source_url: string | null;
  timestamp_seconds: number;
  editor_note: string;
  starred: boolean;
}
export interface PublishBatch {
  politician_id: string;
  quotes: PublishQuote[];
}

export function candidatesToPublishBatch(politicianId: string, cands: Candidate[]): PublishBatch {
  return {
    politician_id: politicianId,
    quotes: cands.map((c) => ({
      text: (c.edit_text.trim() || c.orig_text.trim()),
      topic_label: c.label.trim(),
      source_url: c.source_url,
      timestamp_seconds: c.start_time,
      editor_note: c.note.trim(),
      starred: c.starred,
    })),
  };
}

// Candidate ids whose editor note is empty/whitespace — the export gate blocks
// until this is empty.
export function candidatesMissingNotes(cands: Candidate[]): string[] {
  return cands.filter((c) => !c.note.trim()).map((c) => c.id);
}
