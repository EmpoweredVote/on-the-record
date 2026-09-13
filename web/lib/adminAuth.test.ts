import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { login, getToken, logout, authedFetch, AuthError } from "./adminAuth";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("NEXT_PUBLIC_EV_ACCOUNTS_URL", "https://api.test");
  fetchMock.mockReset();
  logout();
});
afterEach(() => vi.unstubAllGlobals());

describe("login", () => {
  it("stores the access token on success", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true, status: 200,
      json: async () => ({ access_token: "tok-123", token_type: "bearer" }),
    });
    await login("chris@empowered.vote", "pw");
    expect(getToken()).toBe("tok-123");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/auth/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ email: "chris@empowered.vote", password: "pw" });
  });

  it("throws INVALID_CREDENTIALS on 401", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) });
    await expect(login("x", "y")).rejects.toMatchObject({ code: "INVALID_CREDENTIALS" });
    expect(getToken()).toBeNull();
  });
});

describe("authedFetch", () => {
  it("throws UNAUTHORIZED when there is no token", async () => {
    await expect(authedFetch("/api/admin/meetings")).rejects.toBeInstanceOf(AuthError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("adds the Bearer header when a token is present", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ access_token: "tok-9" }) });
    await login("a", "b");
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => [] });
    await authedFetch("/api/admin/meetings?status=draft");
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("https://api.test/api/admin/meetings?status=draft");
    expect(init.headers.Authorization).toBe("Bearer tok-9");
  });

  it("clears the token and throws on a 401 response", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ access_token: "tok-9" }) });
    await login("a", "b");
    fetchMock.mockResolvedValueOnce({ ok: false, status: 401 });
    await expect(authedFetch("/api/admin/meetings")).rejects.toMatchObject({ code: "UNAUTHORIZED" });
    expect(getToken()).toBeNull();
  });
});
