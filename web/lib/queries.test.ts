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

  it("fetchVotes hits the votes API and preserves null timestamps", async () => {
    const f = mockFetch(200, [
      { id: "v1", resolution: "Roll No. 438", description: "On the Smith amendment",
        result: "Yea 236, Nay 193", voteType: "recorded", timestamp: 14702.64 },
      { id: "v2", resolution: "Roll No. 443", description: "On the Connolly amendment",
        result: "Yea 247, Nay 182", voteType: "recorded", timestamp: null },
    ]);
    vi.stubGlobal("fetch", f);
    const { fetchVotes } = await load();
    const out = await fetchVotes("m1");
    const [url] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${API}/api/meetings/m1/votes`);
    expect(out).toHaveLength(2);
    expect(out[0].resolution).toBe("Roll No. 438");
    expect(out[0].timestamp).toBe(14702.64);
    expect(out[1].timestamp).toBeNull();
  });

  it("fetchVotes returns [] on 404 (meeting has no votes)", async () => {
    vi.stubGlobal("fetch", mockFetch(404, {}));
    const { fetchVotes } = await load();
    expect(await fetchVotes("m1")).toEqual([]);
  });
});
