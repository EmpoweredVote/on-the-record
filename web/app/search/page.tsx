import Link from "next/link";
import { Suspense } from "react";
import { fetchMeetings, fetchPeople } from "@/lib/queries";
import SearchView from "./SearchView";

export const metadata = { title: "Search — On the Record" };

export default async function SearchPage() {
  let cities: string[] = [];
  let speakers: { slug: string; name: string }[] = [];
  try {
    const [meetings, people] = await Promise.all([
      fetchMeetings(),
      fetchPeople(),
    ]);
    cities = [...new Set(meetings.map((m) => m.city))].sort();
    speakers = people
      .map((p) => ({ slug: p.slug, name: p.name }))
      .sort((a, b) => a.name.localeCompare(b.name));
  } catch {
    // Dropdowns degrade to empty lists; search itself is a runtime request.
  }

  return (
    <main className="indexPage searchPage">
      <Link href="/" className="backLink">
        ← All meetings
      </Link>
      <h1>Search</h1>
      <p className="tagline">
        Search every word spoken across all published meetings.
      </p>
      {/* useSearchParams requires a Suspense boundary under static export */}
      <Suspense fallback={null}>
        <SearchView cities={cities} speakers={speakers} />
      </Suspense>
    </main>
  );
}
