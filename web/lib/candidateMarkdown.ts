import type { Candidate } from "./types";
import { groupByLabel } from "./candidates";
import { quoteDeepLink } from "./sourceLink";
import { formatMeetingDate, formatTime } from "./format";

// Render a politician's candidate quotes as the Markdown doc a curator keeps:
// grouped by topic label, ★ marking the live pick, each quote a blockquote with
// an attribution line that deep-links to the source moment, and any note-to-self
// in italics. This is the v1 export — a paste-ready replacement for the manual
// per-politician doc.
export function candidatesToMarkdown(personName: string, cands: Candidate[]): string {
  let out = `# ${personName} — candidate quotes\n\n`;
  for (const [label, group] of groupByLabel(cands)) {
    out += `## ${label}\n\n`;
    for (const c of group) {
      const link = quoteDeepLink(c.source_url, c.playback_kind, c.start_time);
      const ts = formatTime(c.start_time);
      const tsMd = link ? `[${ts}](${link})` : ts;
      out += `${c.starred ? "★ " : ""}> ${c.edit_text.trim()}\n\n`;
      out += `— ${personName}, *${c.meeting_title}* (${formatMeetingDate(c.meeting_date)}) · ${tsMd}\n`;
      const note = c.note.trim();
      if (note) out += `\n_Note: ${note}_\n`;
      out += `\n`;
    }
  }
  return out;
}
