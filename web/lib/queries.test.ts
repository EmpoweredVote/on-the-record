import { afterEach, describe, expect, it, vi } from "vitest";

const API = "https://api.test";

function mockFetch(status: number, body: unknown) {
  return vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })) as unknown as typeof fetch;
}

async function load() {
  vi.stubEnv("NEXT_PUBLIC_EV_ACCOUNTS_URL", API);
  vi.resetModules();
  return await import("./queries");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("queries data layer", () => {
  it("fetchMeetings hits the public API with no-store and maps results", async () => {
    const f = mockFetch(200, [{ id: "m1", date: "2026-01-01", meetingType: "X" }]);
    vi.stubGlobal("fetch", f);
    const { fetchMeetings } = await load();
    const out = await fetchMeetings();
    const [url, init] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${API}/api/meetings`);
    expect((init as RequestInit).cache).toBe("no-store");
    expect(out).toHaveLength(1);
    expect(out[0].meeting_id).toBe("m1");
  });

  it("fetchMeeting returns null on 404", async () => {
    vi.stubGlobal("fetch", mockFetch(404, {}));
    const { fetchMeeting } = await load();
    expect(await fetchMeeting("missing")).toBeNull();
  });

  it("fetchMeeting throws on a non-404 error", async () => {
    vi.stubGlobal("fetch", mockFetch(500, {}));
    const { fetchMeeting } = await load();
    await expect(fetchMeeting("x")).rejects.toThrow();
  });
});
