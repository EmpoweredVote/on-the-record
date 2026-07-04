import { describe, it, expect } from "vitest";
import { candidatesToMarkdown } from "./candidateMarkdown";
import type { Candidate } from "./types";

function cand(over: Partial<Candidate>): Candidate {
  return {
    id: "1",
    politician_id: "p",
    meeting_id: "m",
    meeting_title: "Riverside City Council",
    meeting_date: "2026-04-15",
    segment_id: 3,
    start_time: 640,
    source_url: "https://www.youtube.com/watch?v=abc",
    playback_kind: "youtube",
    orig_text: "orig",
    edit_text: "The wildfire insurance situation is pushing families out.",
    label: "housing insurance",
    note: "",
    starred: true,
    created_at: 0,
    ...over,
  };
}

describe("candidatesToMarkdown", () => {
  it("titles the doc with the person's name", () => {
    expect(candidatesToMarkdown("Maria Delgado", [])).toContain("# Maria Delgado — candidate quotes");
  });

  it("groups under a label heading with a ★ + blockquote + deep-linked attribution", () => {
    const md = candidatesToMarkdown("Maria Delgado", [cand({})]);
    expect(md).toContain("## housing insurance");
    expect(md).toContain("★ > The wildfire insurance situation is pushing families out.");
    expect(md).toContain("— Maria Delgado, *Riverside City Council* (Apr 15, 2026) · [10:40](https://www.youtube.com/watch?v=abc&t=640s)");
  });

  it("omits the ★ for unstarred quotes and includes notes when present", () => {
    const md = candidatesToMarkdown("X", [cand({ starred: false, note: "not in compass yet" })]);
    expect(md).not.toContain("★ >");
    expect(md).toContain("> The wildfire insurance situation");
    expect(md).toContain("_Note: not in compass yet_");
  });

  it("uses a plain timestamp when there is no source url", () => {
    const md = candidatesToMarkdown("X", [cand({ source_url: null, note: "" })]);
    expect(md).toContain("· 10:40\n");
    expect(md).not.toContain("](");
  });

  it("trims edited text and emits label groups in alphabetical order", () => {
    const md = candidatesToMarkdown("X", [
      cand({ id: "a", label: "housing", edit_text: "  padded  ", note: "" }),
      cand({ id: "b", label: "budget", note: "" }),
    ]);
    expect(md).toContain("> padded\n");
    expect(md.indexOf("## budget")).toBeLessThan(md.indexOf("## housing"));
  });
});
