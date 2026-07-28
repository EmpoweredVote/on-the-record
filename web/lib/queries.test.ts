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

  it("mapAgendaItem maps a full camelCase payload to snake_case", async () => {
    const { mapAgendaItem } = await load();
    const out = mapAgendaItem({
      id: "ai1",
      meetingId: "m1",
      position: 13,
      itemNumber: "13",
      titleRaw: "Ordinance 2026-16 — To Amend Title 20 (Unified Development Ordinance)",
      kind: "legislation",
      legislationRef: "Ordinance 2026-16",
      summaryPlain: "Changes zoning rules for duplexes.",
      decisionPlain: "Whether to adopt the ordinance.",
      stage: "second reading",
      publicComment: true,
      publicCommentNote: "Comment limited to 3 minutes.",
      status: "happened",
      outcome: "adopted 7-2",
      segmentStartSeconds: 1234.5,
      segmentEndSeconds: 2345.6,
      continuedFromItemId: "ai0",
      sourceUrl: "https://bloomington.in.gov/agenda.pdf",
    });
    expect(out).toEqual({
      id: "ai1",
      meeting_id: "m1",
      position: 13,
      item_number: "13",
      title_raw: "Ordinance 2026-16 — To Amend Title 20 (Unified Development Ordinance)",
      kind: "legislation",
      legislation_ref: "Ordinance 2026-16",
      summary_plain: "Changes zoning rules for duplexes.",
      decision_plain: "Whether to adopt the ordinance.",
      stage: "second reading",
      public_comment: true,
      public_comment_note: "Comment limited to 3 minutes.",
      status: "happened",
      outcome: "adopted 7-2",
      segment_start_seconds: 1234.5,
      segment_end_seconds: 2345.6,
      continued_from_item_id: "ai0",
      source_url: "https://bloomington.in.gov/agenda.pdf",
    });
  });

  it("mapAgendaItem defaults missing nullables and narrows status", async () => {
    const { mapAgendaItem } = await load();
    const out = mapAgendaItem({
      id: "ai2",
      meetingId: "m1",
      itemNumber: "1",
      titleRaw: "Roll Call",
      kind: "procedural",
      sourceUrl: "https://bloomington.in.gov/agenda.pdf",
    });
    expect(out.position).toBe(0);
    expect(out.legislation_ref).toBeNull();
    expect(out.summary_plain).toBeNull();
    expect(out.decision_plain).toBeNull();
    expect(out.stage).toBeNull();
    expect(out.public_comment).toBe(false);
    expect(out.public_comment_note).toBeNull();
    expect(out.status).toBe("upcoming");
    expect(out.outcome).toBeNull();
    expect(out.segment_start_seconds).toBeNull();
    expect(out.segment_end_seconds).toBeNull();
    expect(out.continued_from_item_id).toBeNull();
  });

  it("fetchUpcomingMeetings returns [] when the API base is unset", async () => {
    vi.stubEnv("NEXT_PUBLIC_EV_ACCOUNTS_URL", "");
    vi.resetModules();
    const { fetchUpcomingMeetings } = await import("./queries");
    expect(await fetchUpcomingMeetings()).toEqual([]);
  });

  it("fetchUpcomingMeetings hits the upcoming API and maps results", async () => {
    const f = mockFetch(200, [
      { id: "m9", date: "2026-08-05", meetingType: "Regular Session",
        status: "upcoming", startsAt: "2026-08-06T00:30:00Z", timezone: "America/Indiana/Indianapolis" },
    ]);
    vi.stubGlobal("fetch", f);
    const { fetchUpcomingMeetings } = await load();
    const out = await fetchUpcomingMeetings();
    const [url, init] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${API}/api/meetings/upcoming`);
    expect((init as RequestInit).cache).toBe("no-store");
    expect(out).toHaveLength(1);
    expect(out[0].meeting_id).toBe("m9");
    expect(out[0].status).toBe("upcoming");
    expect(out[0].starts_at).toBe("2026-08-06T00:30:00Z");
    expect(out[0].timezone).toBe("America/Indiana/Indianapolis");
  });

  it("mapMeeting carries status/starts_at/timezone and defaults status to published", async () => {
    vi.stubGlobal("fetch", mockFetch(200, [
      { id: "m1", date: "2026-01-01", meetingType: "X" },
      { id: "m2", date: "2026-08-05", meetingType: "Y",
        status: "upcoming", startsAt: "2026-08-06T00:30:00Z", timezone: "America/Chicago" },
    ]));
    const { fetchMeetings } = await load();
    const out = await fetchMeetings();
    expect(out[0].status).toBe("published");
    expect(out[0].starts_at).toBeNull();
    expect(out[0].timezone).toBeNull();
    expect(out[1].status).toBe("upcoming");
    expect(out[1].starts_at).toBe("2026-08-06T00:30:00Z");
    expect(out[1].timezone).toBe("America/Chicago");
  });

  it("fetchAgendaItems hits the meeting items API and maps results", async () => {
    const f = mockFetch(200, [
      { id: "ai1", meetingId: "m 1", position: 1, itemNumber: "1",
        titleRaw: "Roll Call", kind: "procedural", status: "upcoming",
        sourceUrl: "https://example.gov/a.pdf" },
    ]);
    vi.stubGlobal("fetch", f);
    const { fetchAgendaItems } = await load();
    const out = await fetchAgendaItems("m 1");
    const [url] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${API}/api/meetings/m%201/agenda-items`);
    expect(out).toHaveLength(1);
    expect(out[0].meeting_id).toBe("m 1");
    expect(out[0].title_raw).toBe("Roll Call");
  });

  it("fetchAgendaItem returns the detail with embedded meeting", async () => {
    const f = mockFetch(200, {
      id: "ai1", meetingId: "m1", position: 13, itemNumber: "13",
      titleRaw: "Ordinance 2026-16", kind: "legislation", status: "upcoming",
      sourceUrl: "https://example.gov/a.pdf",
      meeting: {
        id: "m1", title: "Common Council Regular Session", date: "2026-08-05",
        city: "Bloomington", status: "upcoming",
        startsAt: "2026-08-06T00:30:00Z", timezone: "America/Indiana/Indianapolis",
      },
    });
    vi.stubGlobal("fetch", f);
    const { fetchAgendaItem } = await load();
    const out = await fetchAgendaItem("ai1");
    const [url] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${API}/api/agenda-items/ai1`);
    expect(out?.id).toBe("ai1");
    expect(out?.title_raw).toBe("Ordinance 2026-16");
    expect(out?.meeting).toEqual({
      id: "m1", title: "Common Council Regular Session", date: "2026-08-05",
      city: "Bloomington", status: "upcoming",
      starts_at: "2026-08-06T00:30:00Z", timezone: "America/Indiana/Indianapolis",
    });
  });

  it("fetchAgendaItem returns null on 404", async () => {
    vi.stubGlobal("fetch", mockFetch(404, {}));
    const { fetchAgendaItem } = await load();
    expect(await fetchAgendaItem("missing")).toBeNull();
  });

  it("fetchAgendaItem defaults votes/speakers to [] and continued_from to null", async () => {
    // Old API payloads (pre-panel deploy) simply omit the new fields.
    vi.stubGlobal(
      "fetch",
      mockFetch(200, {
        id: "ai1", meetingId: "m1", position: 13, itemNumber: "13",
        titleRaw: "Ordinance 2026-16", kind: "legislation", status: "upcoming",
        sourceUrl: "https://example.gov/a.pdf",
        meeting: { id: "m1", date: "2026-08-05" },
      })
    );
    const { fetchAgendaItem } = await load();
    const out = await fetchAgendaItem("ai1");
    expect(out?.votes).toEqual([]);
    expect(out?.speakers).toEqual([]);
    expect(out?.continued_from).toBeNull();
  });

  it("fetchAgendaItem maps votes, speakers, and continued_from to snake_case", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(200, {
        id: "ai1", meetingId: "m1", position: 13, itemNumber: "13",
        titleRaw: "Ordinance 2026-16", kind: "legislation", status: "happened",
        sourceUrl: "https://example.gov/a.pdf",
        meeting: { id: "m1", date: "2026-07-22" },
        votes: [
          {
            id: "v1", resolution: "Ordinance 2026-16", description: "Adoption",
            result: "Passed · 7–0", voteType: "roll-call", timestamp: 5321.5,
            records: [
              { position: "aye", name: "Isak Nti Asare", politicianId: "p1" },
              { position: "nay", name: "Dave Rollo", politicianId: null },
            ],
          },
        ],
        speakers: [
          {
            name: "Kate Rosenbarger", politicianId: "p2", role: null,
            firstSpokeSeconds: 1210.2, segmentCount: 7,
          },
        ],
        continuedFrom: { id: "prior1", itemNumber: "7A", meetingDate: "2026-07-15" },
      })
    );
    const { fetchAgendaItem } = await load();
    const out = await fetchAgendaItem("ai1");
    expect(out?.votes).toEqual([
      {
        id: "v1", resolution: "Ordinance 2026-16", description: "Adoption",
        result: "Passed · 7–0", vote_type: "roll-call", timestamp: 5321.5,
        records: [
          { position: "aye", name: "Isak Nti Asare", politician_id: "p1" },
          { position: "nay", name: "Dave Rollo", politician_id: null },
        ],
      },
    ]);
    expect(out?.speakers).toEqual([
      {
        name: "Kate Rosenbarger", politician_id: "p2", role: null,
        first_spoke_seconds: 1210.2, segment_count: 7,
      },
    ]);
    expect(out?.continued_from).toEqual({
      id: "prior1", item_number: "7A", meeting_date: "2026-07-15",
    });
  });
});
