"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AdminGate from "@/components/AdminGate";
import MeetingView from "@/app/meetings/[meetingId]/MeetingView";
import { usePathParam } from "@/lib/usePathParam";
import { buildOutline } from "@/lib/outline";
import {
  fetchAdminMeeting, fetchAdminSegments, fetchAdminSummary, fetchAdminVotes,
  promoteMeeting, archiveMeeting,
} from "@/lib/adminQueries";
import type { Meeting, Segment, MeetingSummary, Vote } from "@/lib/types";

function Review({ id }: { id: string }) {
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [summary, setSummary] = useState<MeetingSummary | null>(null);
  const [votes, setVotes] = useState<Vote[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "notfound" | "error">("loading");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setState("loading");
    try {
      const m = await fetchAdminMeeting(id);
      if (!m) { setState("notfound"); return; }
      const [seg, sum, vts] = await Promise.all([
        fetchAdminSegments(id), fetchAdminSummary(id), fetchAdminVotes(id),
      ]);
      setMeeting(m); setSegments(seg); setSummary(sum); setVotes(vts);
      setState("ready");
    } catch {
      setState("error");
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  async function act(fn: (id: string) => Promise<void>, verb: string) {
    if (!confirm(`${verb} this meeting?`)) return;
    setBusy(true);
    try {
      await fn(id);
      window.location.href = "/admin/floor";
    } catch {
      alert(`Could not ${verb.toLowerCase()} the meeting.`);
      setBusy(false);
    }
  }

  if (state === "loading") return <p>Loading…</p>;
  if (state === "notfound") return <p>Draft not found. <Link href="/admin/floor">Back to queue</Link></p>;
  if (state === "error") return <div className="adminError"><p>Could not load this draft.</p><button onClick={load}>Retry</button></div>;

  const actionBar = (
    <>
      <Link href="/admin/floor">← Queue</Link>
      <span>Status: {meeting!.status}</span>
      <button disabled={busy} onClick={() => act(promoteMeeting, "Promote")}>Promote to live</button>
      <button disabled={busy} onClick={() => act(archiveMeeting, "Archive")}>Archive</button>
    </>
  );

  return (
    <MeetingView
      meeting={meeting!}
      segments={segments}
      outline={buildOutline(summary?.sections)}
      votes={votes}
      actionBar={actionBar}
    />
  );
}

export default function AdminMeetingClient() {
  // /admin/meetings/<id> — one segment deeper than the public /meetings/<id>
  // (which reads index 1, see MeetingDetailClient.tsx), so this reads index 2.
  const id = usePathParam(2);
  return <AdminGate>{id ? <Review id={id} /> : <p>Loading…</p>}</AdminGate>;
}
