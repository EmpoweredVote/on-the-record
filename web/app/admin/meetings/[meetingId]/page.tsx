import AdminMeetingClient from "./AdminMeetingClient";

// One sentinel so output:"export" emits a single shell; render.yaml rewrites
// /admin/meetings/* to this shell and the client reads the id from the URL.
export function generateStaticParams() {
  return [{ meetingId: "view" }];
}

export default function AdminMeetingPage() {
  return <AdminMeetingClient />;
}
