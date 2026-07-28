"use client";

import { useState } from "react";
import Link from "next/link";
import { fetchAgendaItem } from "@/lib/queries";
import {
  groupItemSpeakers,
  itemStateBadge,
  outcomeGlyph,
  outcomeLabel,
  voteBuckets,
} from "@/lib/itemPresentation";
import { legislationUrl } from "@/lib/legislationLink";
import { formatMeetingDate, formatTime } from "@/lib/format";
import { formatMeetingWhen } from "@/lib/upcoming";
import { useApi } from "@/lib/useApi";
import { usePathParam } from "@/lib/usePathParam";
import Breadcrumbs from "@/components/Breadcrumbs";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import NotFound from "@/components/NotFound";
import type { ItemSpeaker, ItemVote } from "@/lib/types";

// One roll-call vote: result headline, then For/Against(/Abstained) tabs with
// per-member names. Tally-only votes (no named records yet) show the result
// string the reconciler wrote, verbatim.
function VotePanel({ vote, meetingId }: { vote: ItemVote; meetingId: string }) {
  const buckets = voteBuckets(vote.records);
  const [active, setActive] = useState(0);

  return (
    <div className="votePanel">
      {vote.description && <p className="voteDescription">{vote.description}</p>}
      <p className="voteResult">{vote.result}</p>
      {vote.records.length > 0 && (
        <>
          <div className="voteTabs" role="tablist">
            {buckets.map((b, i) => (
              <button
                key={b.key}
                type="button"
                role="tab"
                aria-selected={i === active}
                className={`voteTab${i === active ? " voteTab-active" : ""}`}
                onClick={() => setActive(i)}
              >
                {b.label} <span className="voteTabCount">{b.records.length}</span>
              </button>
            ))}
          </div>
          {buckets[active].records.length > 0 ? (
            <ul className="voteMembers">
              {buckets[active].records.map((r, i) => (
                <li key={i}>
                  {r.politician_id ? (
                    <Link href={`/people/${r.politician_id}`}>{r.name}</Link>
                  ) : (
                    r.name ?? "Unrecorded"
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="voteMembersEmpty">None</p>
          )}
        </>
      )}
      {vote.timestamp != null && (
        <Link
          className="itemSeekLink"
          href={`/meetings/${meetingId}?t=${Math.floor(vote.timestamp)}`}
        >
          Watch the vote
        </Link>
      )}
    </div>
  );
}

function SpeakerList({
  speakers,
  meetingId,
}: {
  speakers: ItemSpeaker[];
  meetingId: string;
}) {
  return (
    <ul className="itemSpeakerList">
      {speakers.map((s, i) => (
        <li key={i}>
          {s.politician_id ? (
            <Link href={`/people/${s.politician_id}`}>{s.name}</Link>
          ) : (
            <span>{s.name}</span>
          )}
          {s.role && <span className="itemSpeakerRole"> · {s.role}</span>}
          {/* Click-to-seek: jump the meeting video to where they start speaking. */}
          <Link
            className="itemSpeakerSeek"
            href={`/meetings/${meetingId}?t=${Math.floor(s.first_spoke_seconds)}`}
          >
            {formatTime(s.first_spoke_seconds)}
          </Link>
        </li>
      ))}
    </ul>
  );
}

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
  const glyph = outcomeGlyph(item.outcome);
  const seekHref =
    item.segment_start_seconds != null
      ? `/meetings/${item.meeting.id}?t=${Math.floor(item.segment_start_seconds)}`
      : null;
  // The city's legislation page only exists once a final action is recorded
  // (pending legislation 404s) — link it for happened items only. Upcoming
  // items already link the agenda packet, which carries the draft text.
  const ordinanceHref =
    item.status === "happened" ? legislationUrl(item.legislation_ref) : null;
  const speakerGroups = groupItemSpeakers(item.speakers);
  const hasSpeakers =
    speakerGroups.officials.length > 0 || speakerGroups.others.length > 0;

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

      {item.continued_from && (
        <section className="itemSection">
          <h2>Earlier discussion</h2>
          <p>
            This matter previously appeared as item {item.continued_from.item_number}{" "}
            on the {formatMeetingDate(item.continued_from.meeting_date)} agenda.{" "}
            <Link href={`/items/${item.continued_from.id}`}>
              See what happened there
            </Link>
          </p>
        </section>
      )}

      {item.status === "happened" &&
        (outcome || seekHref || ordinanceHref || item.votes.length > 0) && (
          <section className="itemSection">
            <h2>What happened</h2>
            {outcome && (
              <p className="itemOutcome">
                {outcome}
                {glyph && (
                  <span
                    className={`itemOutcomeGlyph itemOutcomeGlyph-${item.outcome}`}
                    aria-hidden="true"
                  >
                    {" "}
                    {glyph}
                  </span>
                )}
              </p>
            )}
            {item.votes.map((v) => (
              <VotePanel key={v.id} vote={v} meetingId={item.meeting.id} />
            ))}
            {seekHref && (
              <Link className="itemSeekLink" href={seekHref}>
                Watch the discussion
              </Link>
            )}
            {ordinanceHref && (
              <a
                className="itemTextLink"
                href={ordinanceHref}
                target="_blank"
                rel="noreferrer"
              >
                Read the full text of {item.legislation_ref}
              </a>
            )}
          </section>
        )}

      {item.status === "happened" && hasSpeakers && (
        <section className="itemSection">
          <h2>Who spoke on this item</h2>
          {speakerGroups.officials.length > 0 && (
            <>
              <h3 className="itemSpeakerGroup">Council members &amp; officials</h3>
              <SpeakerList
                speakers={speakerGroups.officials}
                meetingId={item.meeting.id}
              />
            </>
          )}
          {speakerGroups.others.length > 0 && (
            <>
              <h3 className="itemSpeakerGroup">Public &amp; staff</h3>
              <SpeakerList
                speakers={speakerGroups.others}
                meetingId={item.meeting.id}
              />
            </>
          )}
        </section>
      )}

      {item.status === "upcoming" && (
        // No dead end before the meeting: say what this page will become.
        <section className="itemSection">
          <h2>After the meeting</h2>
          <p className="itemUpcomingNote">
            Once this meeting happens and the recording is processed, this page
            will show what happened to this item — including the discussion and
            any vote.
          </p>
        </section>
      )}

      <footer className="itemMeta">
        <p>
          {/* Meeting pages only exist once the recording is processed. */}
          {item.meeting.status === "published" ? (
            <Link href={`/meetings/${item.meeting.id}`}>
              {item.meeting.title ?? "Meeting"}
            </Link>
          ) : (
            item.meeting.title ?? "Meeting"
          )}
          {" · "}
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
