"use client";

import { useCallback, useEffect, useState } from "react";
import type { Candidate } from "./types";

// Per-politician candidate collection, persisted to localStorage on this browser
// only (v1: single-device curation). Keyed by politician id so switching people
// swaps collections. The tool never writes to ev-accounts — this is the whole
// store.
const keyFor = (pid: string) => `otr:candidates:${pid}`;

function load(pid: string): Candidate[] {
  try {
    const raw = localStorage.getItem(keyFor(pid));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as Candidate[]) : [];
  } catch {
    return [];
  }
}

function persist(pid: string, cands: Candidate[]) {
  try {
    localStorage.setItem(keyFor(pid), JSON.stringify(cands));
  } catch {
    /* quota / privacy mode — nothing we can do; keep working in-memory */
  }
}

// Consumers supply everything except the fields the store owns: `id` and
// `created_at` are generated, and `politician_id` is injected from the hook key.
export type NewCandidate = Omit<Candidate, "id" | "created_at" | "politician_id">;

export interface CandidatesApi {
  cands: Candidate[];
  ready: boolean;
  add: (c: NewCandidate) => Candidate;
  remove: (id: string) => void;
  update: (id: string, patch: Partial<Candidate>) => void;
  replace: (next: Candidate[]) => void;
}

export function useCandidates(politicianId: string | null): CandidatesApi {
  const [cands, setCands] = useState<Candidate[]>([]);
  const [ready, setReady] = useState(false);

  // Hydrate from storage once the id is known (client-only; null until the URL
  // resolves). `ready` gates the persist effect so we never overwrite storage
  // with the empty initial state before hydration. Loading an external store on
  // key change is a legitimate effect-driven state sync (same pattern as
  // useApi.ts), hence the scoped disable.
  useEffect(() => {
    /* eslint-disable react-hooks/set-state-in-effect */
    if (!politicianId) {
      setCands([]);
      setReady(false);
      return;
    }
    setCands(load(politicianId));
    setReady(true);
    /* eslint-enable react-hooks/set-state-in-effect */
  }, [politicianId]);

  useEffect(() => {
    if (politicianId && ready) persist(politicianId, cands);
  }, [cands, politicianId, ready]);

  const add = useCallback(
    (c: NewCandidate) => {
      const full: Candidate = {
        ...c,
        politician_id: politicianId ?? "",
        id: crypto.randomUUID(),
        created_at: Date.now(),
      };
      setCands((prev) => [...prev, full]);
      return full;
    },
    [politicianId]
  );

  const remove = useCallback((id: string) => {
    setCands((prev) => prev.filter((c) => c.id !== id));
  }, []);

  const update = useCallback((id: string, patch: Partial<Candidate>) => {
    setCands((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  }, []);

  const replace = useCallback((next: Candidate[]) => setCands(next), []);

  return { cands, ready, add, remove, update, replace };
}
