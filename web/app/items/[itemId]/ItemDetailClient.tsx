"use client";

import Link from "next/link";
import { fetchAgendaItem } from "@/lib/queries";
import {
  groupItemSpeakers,
  itemStateBadge,
  outcomeHeadline,
  voteBuckets,
} from "@/lib/itemPresentation";
import { legislationUrl } from "@/lib/legislationLink";
import { formatDuration, formatMeetingDate, formatTime } from "@/lib/format";
import { formatMeetingWhen } from "@/lib/upcoming";
import { useApi } from "@/lib/useApi";
import { usePathParam } from "@/lib/usePathParam";
import Breadcrumbs from "@/components/Breadcrumbs";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import NotFound from "@/components/NotFound";
import type { ItemSpeaker, ItemVote } from "@/lib/types";

// One recorded vote: the clerk's motion sentence, then a division list —
// For and Against side by side (both always shown, so "Against — 0" reads as
// unanimity), Abstained/Other only when occupied. No tabs: every name is on
// the page, in reading order, zero clicks.
function VoteDivision({ vote, meetingId }: { vote: ItemVote; meetingId: string }) {
  const buckets = voteBuckets(vote.records);
  return (
    <div className="voteBlock">
      {vote.description && <p className="voteMotion">{vote.description}</p>}
      {vote.records.length > 0 && (
        <div className="voteDivision">
          {buckets.map((b) => (
            <div key={b.key} className={`voteSide voteSide-${b.key}`}>
              <h3 className="voteSideHeading">
                {b.label} <span className="voteSideCount">{b.records.length}</span>
              </h3>
              {b.records.length > 0 ? (
                <ul className="voteSideNames">
                  {b.records.map((r, i) => (
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
                <p className="voteSideEmpty">No one</p>
              )}
            </div>
          ))}
        </div>
      )}
      {vote.timestamp != null && (
        <Link
          className="itemActionLink"
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
            aria-label={`Watch ${s.name} at ${formatTime(s.first_spoke_seconds)}`}
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
  const happened = item.status === "happened";
  const badge = itemStateBadge(item.status, item.meeting.date);
  const headline = happened ? outcomeHeadline(item.outcome, item.votes) : null;
  const discussionSeconds =
    item.segment_start_seconds != null && item.segment_end_seconds != null
      ? item.segment_end_seconds - item.segment_start_seconds
      : null;
  const seekHref =
    item.segment_start_seconds != null
      ? `/meetings/${item.meeting.id}?t=${Math.floor(item.segment_start_seconds)}`
      : null;
  // The city's legislation page only exists once a final action is recorded
  // (pending legislation 404s) — link it for happened items only. Upcoming
  // items already link the agenda packet, which carries the draft text.
  const ordinanceHref = happened ? legislationUrl(item.legislation_ref) : null;
  const speakerGroups = groupItemSpeakers(item.speakers);
  const hasSpeakers =
    speakerGroups.officials.length > 0 || speakerGroups.others.length > 0;

  // Wayfinding matches the item's state: a happened item belongs to its
  // meeting; an upcoming item belongs to the Upcoming list.
  const crumbs =
    happened && item.meeting.status === "published"
      ? [
          {
            label: formatMeetingDate(item.meeting.date),
            href: `/meetings/${item.meeting.id}`,
          },
          { label: `Item ${item.item_number}` },
        ]
      : [
          { label: "Upcoming", href: "/upcoming" },
          { label: `Item ${item.item_number}` },
        ];

  return (
    <main className="itemPage">
      <Breadcrumbs items={crumbs} />
      <p className="itemChips">
        <span className={`itemBadge itemBadge-${badge.tone}`}>{badge.label}</span>
        {/* Post-meeting, the stage reads as context ("Second reading — final
            vote"), not status — a chip, not a section. */}
        {happened && item.stage && <span className="itemStageChip">{item.stage}</span>}
      </p>
      <h1>{item.summary_plain ?? item.title_raw}</h1>
      {item.summary_plain && (
        <p className="itemTitleRaw">
          On the agenda as: <em>{item.title_raw}</em>
        </p>
      )}

      {/* THE answer, first: outcome + margin, the clerk's motion sentence,
          and the full division — who voted which way, no clicks. */}
      {headline && (
        <section className={`decisionBanner decisionBanner-${headline.tone}`}>
          <h2 className="srOnly">Outcome</h2>
          <p className="decisionHeadline">
            {headline.glyph && (
              <span className="decisionGlyph" aria-hidden="true">
                {headline.glyph}{" "}
              </span>
            )}
            {headline.text}
          </p>
          {/* What the question WAS, right next to its answer. */}
          {item.decision_plain && (
            <p className="decisionContext">{item.decision_plain}</p>
          )}
          {item.votes.map((v) => (
            <VoteDivision key={v.id} vote={v} meetingId={item.meeting.id} />
          ))}
        </section>
      )}

      {happened && (seekHref || ordinanceHref) && (
        <p className="itemActions">
          {seekHref && (
            <Link className="itemActionLink itemActionLink-primary" href={seekHref}>
              Watch the discussion
              {discussionSeconds != null && discussionSeconds > 0 && (
                <span className="itemActionMeta"> · {formatDuration(discussionSeconds)}</span>
              )}
            </Link>
          )}
          {ordinanceHref && (
            <a className="itemActionLink" href={ordinanceHref} target="_blank" rel="noreferrer">
              Read the full text of {item.legislation_ref}
            </a>
          )}
        </p>
      )}

      {/* Standalone section only when there's no banner to carry it —
          upcoming items, and happened items with no recorded outcome. */}
      {item.decision_plain && !headline && (
        <section className="itemSection">
          <h2>{happened ? "What was being decided" : "What's being decided"}</h2>
          <p>{item.decision_plain}</p>
        </section>
      )}

      {/* Stage + how-to-comment guidance is advice for BEFORE the meeting;
          it reads stale (wrong tense) afterwards. */}
      {!happened && (item.stage || item.public_comment_note) && (
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

      {happened && hasSpeakers && (
        <section className="itemSection">
          <h2>Who spoke on this item</h2>
          {speakerGroups.officials.length > 0 && (
            <>
              <h3 className="itemSpeakerGroup">
                Council members &amp; officials · {speakerGroups.officials.length}
              </h3>
              <SpeakerList
                speakers={speakerGroups.officials}
                meetingId={item.meeting.id}
              />
            </>
          )}
          {speakerGroups.others.length > 0 && (
            <>
              <h3 className="itemSpeakerGroup">
                Public &amp; staff · {speakerGroups.others.length}
              </h3>
              <SpeakerList
                speakers={speakerGroups.others}
                meetingId={item.meeting.id}
              />
            </>
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
