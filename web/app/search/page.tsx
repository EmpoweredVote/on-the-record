import { Suspense } from "react";
import SearchView from "./SearchView";
import Breadcrumbs from "@/components/Breadcrumbs";

export const metadata = { title: "Search — On the Record" };

export default function SearchPage() {
  return (
    <main className="indexPage searchPage">
      <Breadcrumbs items={[{ label: "Meetings", href: "/" }, { label: "Search" }]} />
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
