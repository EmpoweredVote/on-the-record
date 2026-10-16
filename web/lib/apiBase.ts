// Base URL for ev-accounts API calls made from the browser.
//
// In development, calls go to the dev server's own origin, and next.config's
// dev-only rewrite forwards /api/* to the live API (or EV_API_PROXY_TARGET).
// The live API's CORS allow-list holds only production domains, so calling it
// directly from localhost is blocked. Same-origin calls never meet CORS.
//
// Read at call time (not import time) so it's testable and still inlined by Next.
export function apiBase(): string {
  if (process.env.NODE_ENV === "development" && typeof window !== "undefined") {
    return window.location.origin;
  }
  return (process.env.NEXT_PUBLIC_EV_ACCOUNTS_URL ?? "").replace(/\/$/, "");
}
