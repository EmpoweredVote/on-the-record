"use client";

import { useEffect, useState } from "react";

export interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: boolean;
}

const LOADING: ApiState<never> = { data: null, loading: true, error: false };

/** Element-wise, the way useEffect compares its own dependency array. `deps`
 *  arrives as a fresh array on every render (the default is a literal), so a
 *  reference check would report a change every time. */
function sameDeps(a: unknown[], b: unknown[]): boolean {
  return a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
}

/** Runs `fetcher` on mount and whenever `deps` change. Ignores results from
 *  superseded calls so a fast re-render can't apply stale data. */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []): ApiState<T> {
  // A settled result is stored together with the deps it was fetched for, so
  // "loading" can be DERIVED — it is simply the absence of a result for the
  // current deps. That keeps the reset-to-loading behaviour on a deps change
  // without a synchronous setState in the effect body, which would cause a
  // cascading render, and without a render-phase write.
  const [settled, setSettled] = useState<{ deps: unknown[]; state: ApiState<T> } | null>(null);

  useEffect(() => {
    let ignore = false;
    fetcher()
      .then((data) => {
        if (!ignore) setSettled({ deps, state: { data, loading: false, error: false } });
      })
      .catch(() => {
        if (!ignore) setSettled({ deps, state: { data: null, loading: false, error: true } });
      });
    return () => { ignore = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  // No result yet, or one belonging to a previous set of deps: still loading.
  if (settled === null || !sameDeps(settled.deps, deps)) return LOADING;
  return settled.state;
}
