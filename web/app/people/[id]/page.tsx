import PersonDetailClient from "./PersonDetailClient";

// One sentinel so output:"export" emits a single shell file for this route.
// Render rewrites serve this shell for ANY /people/* id; the client reads the
// real id from the URL and fetches it at runtime.
export function generateStaticParams() {
  return [{ id: "view" }];
}

export default function PersonPage() {
  return <PersonDetailClient />;
}
