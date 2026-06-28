import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchAppearances, fetchPeople, fetchPerson } from "@/lib/queries";
import { formatMeetingDate, formatTime } from "@/lib/format";

export const dynamicParams = false;
export const dynamic = "force-static";

export async function generateStaticParams() {
  // Wrap in try/catch so builds succeed when EV_ACCOUNTS_URL is unset or the
  // API is unreachable (fetch throws TypeError: Invalid URL for relative paths).
  let people: Awaited<ReturnType<typeof fetchPeople>> = [];
  try {
    people = await fetchPeople();
  } catch {
    // API unavailable at build time — fall through to sentinel below.
  }
  // output:"export" fails the build when a dynamic route has zero params
  // (e.g., before the first person is linked). Emit one sentinel id — the nil
  // UUID — which is a valid UUID (so the API returns 404, not 422) and renders
  // 404, so empty-data builds still succeed.
  if (people.length === 0) return [{ id: "00000000-0000-0000-0000-000000000000" }];
  return people.map((p) => ({ id: p.politician_id }));
}

// essentials.city politician profiles are /politician/<uuid>
const ESSENTIALS_BASE = "https://essentials.city";

export default async function PersonPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const person = await fetchPerson(id);
  if (!person) notFound();
  const appearances = await fetchAppearances(id);

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
                {a.city} {a.meeting_type} — {formatMeetingDate(a.meeting_date)}
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
