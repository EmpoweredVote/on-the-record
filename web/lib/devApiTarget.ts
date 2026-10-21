// Where `next dev` forwards /api/* (see next.config.ts and lib/apiBase.ts).
//
// Order: EV_API_PROXY_TARGET, then a LOCAL NEXT_PUBLIC_EV_ACCOUNTS_URL (a
// developer who already pointed the site at their own backend keeps it, so
// admin actions they think are local never reach production), then the live API.
export const LIVE_API_ORIGIN = "https://accounts-api.empowered.vote";

const LOCAL_HOST = /^https?:\/\/(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?(\/|$)/;

export function devApiTarget(env: Record<string, string | undefined>): string {
  const override = env.EV_API_PROXY_TARGET?.trim();
  if (override) return override.replace(/\/$/, "");
  const configured = env.NEXT_PUBLIC_EV_ACCOUNTS_URL?.trim() ?? "";
  if (LOCAL_HOST.test(configured)) return configured.replace(/\/$/, "");
  return LIVE_API_ORIGIN;
}
