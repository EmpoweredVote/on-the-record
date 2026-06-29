"use client";

import Link from "next/link";
import { fetchAppearances, fetchPerson } from "@/lib/queries";
import { formatMeetingDate, formatTime, meetingTitle } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { usePathParam } from "@/lib/usePathParam";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import NotFound from "@/components/NotFound";

// essentials.city politician profiles are /politician/<uuid>
const ESSENTIALS_BASE = "https://essentials.city";

export default function PersonDetailClient() {
  const id = usePathParam(1); // /people/<id> — real URL id, not the build sentinel
  const ready = id != null;

  const personQ = useApi(() => (ready ? fetchPerson(id) : Promise.resolve(null)), [id]);
  const appearancesQ = useApi(() => (ready ? fetchAppearances(id) : Promise.resolve([])), [id]);

  if (!ready || personQ.loading) return <main className="indexPage personPage"><Loading label="Loading person…" /></main>;
  if (personQ.error) return <main className="indexPage personPage"><ErrorState /></main>;
  if (!personQ.data) return <NotFound message="Person not found." />;

  const person = personQ.data;
  const appearances = appearancesQ.data ?? [];

  return (
    <main className="indexPage personPage">
      <Link href="/people" className="backLink">
        ← All people
      </Link>
      <header className="personHeader">
        {person.headshot_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img className="personPhoto large" src={person.headshot_url} alt="" />
        ) : (
          <span className="personPhoto large personPhotoFallback" aria-hidden>
            {person.name.charAt(0)}
          </span>
        )}
        <div>
          <h1>{person.name}</h1>
          <p className="personOffice">
            {[person.office_title, person.district, person.jurisdiction]
              .filter(Boolean)
              .join(" · ")}
            {person.party ? ` · ${person.party}` : ""}
          </p>
          {person.politician_id && (
            <a
              className="sourceLink"
              href={`${ESSENTIALS_BASE}/politician/${person.politician_id}`}
              target="_blank"
              rel="noreferrer"
            >
              Full profile on essentials.city ↗
            </a>
          )}
        </div>
      </header>
      {person.bio_text && <p className="personBio">{person.bio_text}</p>}

      <h2>Appearances</h2>
      {appearances.length === 0 ? (
        <p>No appearances on record.</p>
      ) : (
        appearances.map((a) => (
          <section key={a.meeting_id} className="appearance">
            <h3>
              <Link href={`/meetings/${a.meeting_id}`}>
                {meetingTitle(a)} — {formatMeetingDate(a.meeting_date)}
              </Link>
              <span className="personMeta">
                {" "}
                · {a.segments.length} segment
                {a.segments.length === 1 ? "" : "s"}
              </span>
            </h3>
            <ul className="appearanceSegments">
              {a.segments.map((seg) => (
                <li key={seg.segment_id}>
                  <Link
                    href={`/meetings/${a.meeting_id}?t=${Math.floor(seg.start_time)}#seg-${seg.segment_id}`}
                    className="timestampLink"
                  >
                    {formatTime(seg.start_time)}
                  </Link>{" "}
                  <span className="appearanceText">{seg.text}</span>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </main>
  );
}
