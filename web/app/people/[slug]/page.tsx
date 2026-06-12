import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchAppearances, fetchPeople, fetchPerson } from "@/lib/queries";

export const dynamicParams = false;

export async function generateStaticParams() {
  const people = await fetchPeople();
  // output:"export" fails the build when a dynamic route has zero params
  // (e.g., before the first meeting is published). Emit one sentinel slug
  // that renders 404 so empty-data builds still succeed.
  if (people.length === 0) return [{ slug: "none" }];
  return people.map((p) => ({ slug: p.slug }));
}

// essentials.city politician profiles are /politician/<uuid>
const ESSENTIALS_BASE = "https://essentials.city";

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

export default async function PersonPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const person = await fetchPerson(slug);
  if (!person) notFound();
  const appearances = await fetchAppearances(slug);

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
                {a.city} {a.meeting_type} — {a.meeting_date}
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
