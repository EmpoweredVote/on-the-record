"use client";

import Link from "next/link";
import { fetchPeople } from "@/lib/queries";
import { useApi } from "@/lib/useApi";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import EmptyState from "@/components/EmptyState";
import PersonPhoto from "@/components/PersonPhoto";

export default function PeoplePage() {
  const { data: people, loading, error } = useApi(fetchPeople);

  return (
    <main className="indexPage">
      <h1>People</h1>
      <p className="tagline">
        Everyone identified speaking in published meetings, linked to every
        moment they spoke.
      </p>
      <nav className="siteNav">
        <Link href="/search">Search →</Link>
      </nav>
      {loading ? (
        <Loading label="Loading people…" />
      ) : error ? (
        <ErrorState message="People are temporarily unavailable. Please try again shortly." />
      ) : !people || people.length === 0 ? (
        <EmptyState message="No people yet." />
      ) : (
        <ul className="peopleGrid">
          {people.map((p) => (
            <li key={p.politician_id}>
              <Link href={`/people/${p.politician_id}`} className="personCard">
                <PersonPhoto name={p.name} url={p.headshot_url} />
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
