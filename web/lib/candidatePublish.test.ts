import { describe, it, expect } from "vitest";
import { candidatesToPublishBatch, candidatesMissingNotes } from "./candidatePublish";
import type { Candidate } from "./types";

function cand(over: Partial<Candidate>): Candidate {
  return {
    id: "1", politician_id: "p1", meeting_id: "m", meeting_title: "Council",
    meeting_date: "2026-04-15", segment_id: 3, start_time: 640,
    source_url: "https://www.youtube.com/watch?v=abc", playback_kind: "youtube",
    orig_text: "orig verbatim", edit_text: "edited text",
    label: "housing insurance", note: "why it matters", starred: true,
    created_at: 0, ...over,
  };
}

describe("candidatesToPublishBatch", () => {
  it("emits politician_id and one entry per candidate with base url + timestamp", () => {
    const batch = candidatesToPublishBatch("p1", [cand({})]);
    expect(batch.politician_id).toBe("p1");
    expect(batch.quotes).toHaveLength(1);
    const q = batch.quotes[0];
    expect(q.text).toBe("edited text");
    expect(q.topic_label).toBe("housing insurance");
    expect(q.source_url).toBe("https://www.youtube.com/watch?v=abc"); // base, no &t=
    expect(q.timestamp_seconds).toBe(640);
    expect(q.editor_note).toBe("why it matters");
    expect(q.starred).toBe(true);
  });

  it("falls back to verbatim text when edit_text is blank, and trims", () => {
    const batch = candidatesToPublishBatch("p1", [cand({ edit_text: "   ", orig_text: " raw " })]);
    expect(batch.quotes[0].text).toBe("raw");
  });

  it("includes ALL candidates, starred or not", () => {
    const batch = candidatesToPublishBatch("p1", [
      cand({ id: "a", starred: true }),
      cand({ id: "b", starred: false, note: "second" }),
    ]);
    expect(batch.quotes).toHaveLength(2);
  });
});

describe("candidatesMissingNotes", () => {
  it("returns ids of candidates whose note is empty/whitespace", () => {
    const missing = candidatesMissingNotes([
      cand({ id: "a", note: "ok" }),
      cand({ id: "b", note: "   " }),
      cand({ id: "c", note: "" }),
    ]);
    expect(missing).toEqual(["b", "c"]);
  });
});
