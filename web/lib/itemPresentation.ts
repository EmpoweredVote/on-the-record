// Citizen-language presentation for agenda items. Neutral voice — never
// editorialize outcomes (spec: provenance-first, rep-citable pages).
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
