"use client";

import { useCallback, useSyncExternalStore } from "react";

// The address bar is an external store, so it is read with useSyncExternalStore
// rather than copied into React state from an effect. Each detail shell is its
// own static document, so the path never changes under a mounted component and
// there is nothing to subscribe to — but the subscribe function must still be
// stable, or React re-subscribes on every render.
const subscribe = () => () => {};

// Prerender and the hydration render must agree, so both report "not resolved
// yet"; React re-renders with the real segment immediately after hydration.
const getServerSnapshot = () => null;

/**
 * Read a path segment from the browser URL (0-indexed, ignoring empty segments).
 *
 * Detail routes are served as a single static "view" shell via Render rewrites,
 * so Next's `useParams()` returns the build-time sentinel ("view"), NOT the real
 * id in the address bar. Reading `window.location.pathname` gives the actual id.
 * Returns `null` until hydration completes (server prerender / first paint), so
 * callers should treat `null` as "still resolving".
 *
 * Examples (segment 1): /meetings/<id> -> <id>, /people/<id> -> <id>,
 * /topics/<key> -> <key>.
 */
export function usePathParam(index: number): string | null {
  const getSnapshot = useCallback(() => {
    const segs = window.location.pathname.split("/").filter(Boolean);
    return segs[index] != null ? decodeURIComponent(segs[index]) : null;
  }, [index]);

  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
