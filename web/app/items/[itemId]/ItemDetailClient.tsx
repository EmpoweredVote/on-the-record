"use client";

import Link from "next/link";
import { fetchAgendaItem } from "@/lib/queries";
import { itemStateBadge, outcomeLabel } from "@/lib/itemPresentation";
import { formatMeetingWhen } from "@/lib/upcoming";
import { useApi } from "@/lib/useApi";
import { usePathParam } from "@/lib/usePathParam";
import Breadcrumbs from "@/components/Breadcrumbs";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import NotFound from "@/components/NotFound";

export default function ItemDetailClient() {
  const itemId = usePathParam(1); // /items/<id> — real URL id, not the build sentinel
  const ready = itemId != null;

  const itemQ = useApi(
    () => (ready ? fetchAgendaItem(itemId) : Promise.resolve(null)),
    [itemId]
  );

  if (!ready || itemQ.loading) {
    return <main className="itemPage"><Loading label="Loading agenda item…" /></main>;
  }
  if (itemQ.error) return <main className="itemPage"><ErrorState /></main>;
  if (!itemQ.data) return <NotFound message="Agenda item not found." />;

  const item = itemQ.data;
  const badge = itemStateBadge(item.status, item.meeting.date);
  const outcome = outcomeLabel(item.outcome);
  const seekHref =
    item.segment_start_seconds != null
      ? `/meetings/${item.meeting.id}?t=${Math.floor(item.segment_start_seconds)}`
      : null;

  return (
    <main className="itemPage">
      <Breadcrumbs
        items={[
          { label: "Upcoming", href: "/upcoming" },
          { label: item.item_number },
        ]}
      />
      <span className={`itemBadge itemBadge-${badge.tone}`}>{badge.label}</span>
      <h1>{item.summary_plain ?? item.title_raw}</h1>
      {item.summary_plain && (
        <p className="itemTitleRaw">
          On the agenda as: <em>{item.title_raw}</em>
        </p>
      )}

      {item.decision_plain && (
        <section className="itemSection">
          <h2>What&apos;s being decided</h2>
          <p>{item.decision_plain}</p>
        </section>
      )}

      {(item.stage || item.public_comment_note) && (
        <section className="itemSection">
          <h2>Where this stands</h2>
          {item.stage && <p className="itemStage">{item.stage}</p>}
          {item.public_comment_note && (
            <p className={item.public_comment ? "itemCommentYes" : "itemCommentNo"}>
              {item.public_comment_note}
            </p>
          )}
        </section>
      )}

      {item.status === "happened" && (outcome || seekHref) && (
        <section className="itemSection">
          <h2>What happened</h2>
          {outcome && <p className="itemOutcome">{outcome}</p>}
          {seekHref && (
            <Link className="itemSeekLink" href={seekHref}>
              Watch the discussion
            </Link>
          )}
        </section>
      )}

      <footer className="itemMeta">
        <p>
          {item.meeting.title ?? "Meeting"} ·{" "}
          {formatMeetingWhen(
            item.meeting.date,
            item.meeting.starts_at,
            item.meeting.timezone
          )}
          {item.meeting.city && <> · {item.meeting.city}</>}
        </p>
        {item.legislation_ref && <p>{item.legislation_ref}</p>}
        <a href={item.source_url} target="_blank" rel="noreferrer">
          Official agenda (PDF)
        </a>
      </footer>
    </main>
  );
}
