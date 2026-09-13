"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AdminGate from "@/components/AdminGate";
import { AuthError } from "@/lib/adminAuth";
import { fetchDraftMeetings, promoteMeeting, archiveMeeting, type DraftListItem } from "@/lib/adminQueries";
import { draftRowView } from "@/lib/adminRow";

function Queue() {
  const [rows, setRows] = useState<DraftListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRows(await fetchDraftMeetings("draft"));
    } catch (err) {
      if (err instanceof AuthError && err.code === "FORBIDDEN") {
        setError("This account is not an admin.");
      } else {
        setError("Could not load drafts.");
      }
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function act(id: string, fn: (id: string) => Promise<void>, verb: string) {
    if (!confirm(`${verb} this meeting?`)) return;
    setBusyId(id);
    try {
      await fn(id);
      await load();
    } catch {
      setError(`Could not ${verb.toLowerCase()} the meeting.`);
    } finally {
      setBusyId(null);
    }
  }

  if (error) return <div className="adminError"><p>{error}</p><button onClick={load}>Retry</button></div>;
  if (!rows) return <p>Loading drafts…</p>;
  if (rows.length === 0) return <p>No drafts awaiting review.</p>;

  return (
    <table className="adminQueue">
      <thead><tr><th>Date</th><th>Meeting</th><th>Duration</th><th>Gate</th><th>Coverage</th><th>Speakers</th><th>Actions</th></tr></thead>
      <tbody>
        {rows.map((r) => {
          const v = draftRowView(r);
          return (
            <tr key={v.id}>
              <td>{v.date}</td>
              <td><Link href={`/admin/meetings/${v.id}`}>{v.title}</Link></td>
              <td>{v.duration}</td>
              <td>{v.gateVerdict}</td>
              <td>{v.coverage}</td>
              <td>{v.counts}</td>
              <td>
                <Link href={`/admin/meetings/${v.id}`}>Review</Link>{" "}
                <button disabled={busyId === v.id} onClick={() => act(v.id, promoteMeeting, "Promote")}>Promote</button>{" "}
                <button disabled={busyId === v.id} onClick={() => act(v.id, archiveMeeting, "Archive")}>Archive</button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function FloorQueueClient() {
  return <AdminGate><h1>House-floor draft review</h1><Queue /></AdminGate>;
}
