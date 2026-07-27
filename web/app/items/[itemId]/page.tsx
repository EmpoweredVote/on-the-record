import ItemDetailClient from "./ItemDetailClient";

// One sentinel so output:"export" emits a single shell file for this route.
// Render rewrites serve this shell for ANY /items/* id; the client reads the
// real id from the URL and fetches it at runtime.
export function generateStaticParams() {
  return [{ itemId: "view" }];
}

export default function ItemPage() {
  return <ItemDetailClient />;
}
