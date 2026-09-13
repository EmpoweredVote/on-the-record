// Client-side auth for the /admin review panel. The static bundle holds no
// secret; real protection is the ev-accounts requireAuth+requireAdmin gate.
const TOKEN_KEY = "otr_admin_token";
let memoryToken: string | null = null;

function apiBase(): string {
  return (process.env.NEXT_PUBLIC_EV_ACCOUNTS_URL ?? "").replace(/\/$/, "");
}

export class AuthError extends Error {
  code: "INVALID_CREDENTIALS" | "UNAUTHORIZED" | "FORBIDDEN" | "NETWORK";
  constructor(code: "INVALID_CREDENTIALS" | "UNAUTHORIZED" | "FORBIDDEN" | "NETWORK") {
    super(code);
    this.name = "AuthError";
    this.code = code;
  }
}

export function getToken(): string | null {
  if (memoryToken) return memoryToken;
  try {
    if (typeof sessionStorage !== "undefined") memoryToken = sessionStorage.getItem(TOKEN_KEY);
  } catch {
    /* private mode / storage disabled — memory only */
  }
  return memoryToken;
}

function setToken(token: string): void {
  memoryToken = token;
  try {
    if (typeof sessionStorage !== "undefined") sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* ignore */
  }
}

export function logout(): void {
  memoryToken = null;
  try {
    if (typeof sessionStorage !== "undefined") sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export async function login(email: string, password: string): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  } catch {
    throw new AuthError("NETWORK");
  }
  if (res.status === 401 || res.status === 403) throw new AuthError("INVALID_CREDENTIALS");
  if (!res.ok) throw new AuthError("NETWORK");
  const data = await res.json();
  if (!data?.access_token) throw new AuthError("NETWORK");
  setToken(data.access_token);
}

export async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  if (!token) throw new AuthError("UNAUTHORIZED");
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    cache: "no-store",
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    logout();
    throw new AuthError("UNAUTHORIZED");
  }
  if (res.status === 403) {
    // A valid token, but the account isn't an admin — keep the session,
    // just surface a distinct error for the caller.
    throw new AuthError("FORBIDDEN");
  }
  return res;
}
