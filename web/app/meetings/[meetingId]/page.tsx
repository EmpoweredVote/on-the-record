import MeetingDetailClient from "./MeetingDetailClient";

// One sentinel so output:"export" emits a single shell file for this route.
// Render rewrites serve this shell for ANY /meetings/* id; the client reads the
// real id from the URL and fetches it at runtime.
export function generateStaticParams() {
  return [{ meetingId: "view" }];
}

export default function MeetingPage() {
  return <MeetingDetailClient />;
}
