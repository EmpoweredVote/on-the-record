"use client";

import { useEffect, useState } from "react";

/**
 * Read a path segment from the browser URL (0-indexed, ignoring empty segments).
 *
 * Detail routes are served as a single static "view" shell via Render rewrites,
 * so Next's `useParams()` returns the build-time sentinel ("view"), NOT the real
 * id in the address bar. Reading `window.location.pathname` gives the actual id.
 * Returns `null` until the effect runs (server prerender / first paint), so
 * callers should treat `null` as "still resolving".
 *
 * Examples (segment 1): /meetings/<id> -> <id>, /people/<id> -> <id>,
 * /topics/<key> -> <key>.
 */
export function usePathParam(index: number): string | null {
  const [value, setValue] = useState<string | null>(null);
  useEffect(() => {
    const segs = window.location.pathname.split("/").filter(Boolean);
    setValue(segs[index] != null ? decodeURIComponent(segs[index]) : null);
  }, []);
  return value;
}
