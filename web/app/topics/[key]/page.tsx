import TopicDetailClient from "./TopicDetailClient";

// One sentinel so output:"export" emits a single shell file for this route.
// Render rewrites serve this shell for ANY /topics/* key; the client reads the
// real key from the URL and fetches it at runtime.
export function generateStaticParams() {
  return [{ key: "view" }];
}

export default function TopicPage() {
  return <TopicDetailClient />;
}
