import Link from "next/link";
import { Suspense } from "react";
import SearchView from "./SearchView";

export const metadata = { title: "Search — On the Record" };

export default function SearchPage() {
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
        <SearchView />
      </Suspense>
    </main>
  );
}
