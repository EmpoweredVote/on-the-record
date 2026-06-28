"use client";

import { useEffect, useState } from "react";

export interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: boolean;
}

/** Runs `fetcher` on mount and whenever `deps` change. Ignores results from
 *  superseded calls so a fast re-render can't apply stale data. */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ data: null, loading: true, error: false });

  useEffect(() => {
    let ignore = false;
    setState({ data: null, loading: true, error: false });
    fetcher()
      .then((data) => { if (!ignore) setState({ data, loading: false, error: false }); })
      .catch(() => { if (!ignore) setState({ data: null, loading: false, error: true }); });
    return () => { ignore = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
