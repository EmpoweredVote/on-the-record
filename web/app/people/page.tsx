import Link from "next/link";
import { fetchPeople } from "@/lib/queries";
import type { Person } from "@/lib/types";

export const metadata = { title: "People — On the Record" };

export default async function PeoplePage() {
  let people: Person[] = [];
  let loadError = false;
  try {
    people = await fetchPeople();
  } catch {
    // Don't take the whole site down (or fail CI builds) on an API hiccup.
    loadError = true;
  }

  return (
    <main className="indexPage">
      <h1>People</h1>
      <p className="tagline">
        Everyone identified speaking in published meetings, linked to every
        moment they spoke.
      </p>
      {loadError ? (
        <p>People are temporarily unavailable. Please try again shortly.</p>
      ) : people.length === 0 ? (
        <p>No identified speakers yet.</p>
      ) : (
        <ul className="peopleGrid">
          {people.map((p) => (
            <li key={p.slug}>
              <Link href={`/people/${p.slug}`} className="personCard">
                {p.headshot_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img className="personPhoto" src={p.headshot_url} alt="" />
                ) : (
                  <span className="personPhoto personPhotoFallback" aria-hidden>
                    {p.name.charAt(0)}
                  </span>
                )}
                <span className="personName">{p.name}</span>
                {p.office_title && (
                  <span className="personOffice">
                    {p.office_title}
                    {p.district ? `, ${p.district}` : ""}
                  </span>
                )}
                <span className="personMeta">
                  {p.meeting_count} meeting{p.meeting_count === 1 ? "" : "s"}
                  {p.cities.length > 0 ? ` · ${p.cities.join(", ")}` : ""}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
