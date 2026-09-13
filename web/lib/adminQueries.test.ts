import { describe, it, expect, vi, beforeEach } from "vitest";

const authedFetchMock = vi.fn();
vi.mock("./adminAuth", () => ({ authedFetch: (...a: unknown[]) => authedFetchMock(...a) }));

import {
  fetchDraftMeetings, fetchAdminMeeting, promoteMeeting, archiveMeeting,
} from "./adminQueries";

beforeEach(() => authedFetchMock.mockReset());

describe("fetchDraftMeetings", () => {
  it("requests the draft queue and returns the rows", async () => {
    authedFetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [{ id: "m1", status: "draft", named: 33, linked: 32 }],
    });
    const rows = await fetchDraftMeetings();
    expect(authedFetchMock).toHaveBeenCalledWith("/api/admin/meetings?status=draft");
    expect(rows[0]).toMatchObject({ id: "m1", named: 33, linked: 32 });
  });

  it("preserves gate verdict + coverage from processingMetadata", async () => {
    authedFetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [{
        id: "m1", status: "draft", named: 33, linked: 32,
        processingMetadata: { gate_verdict: "review", gate_coverage: 0.63 },
      }],
    });
    const rows = await fetchDraftMeetings();
    expect(rows[0].id).toBe("m1");
    expect(rows[0].processingMetadata?.gate_verdict).toBe("review");
    expect(rows[0].processingMetadata?.gate_coverage).toBe(0.63);
  });

  it("throws when the response is not ok (instead of returning [])", async () => {
    authedFetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(fetchDraftMeetings()).rejects.toThrow();
  });
});

describe("fetchAdminMeeting", () => {
  it("returns a draft meeting (no published-only filter)", async () => {
    authedFetchMock.mockResolvedValueOnce({
      ok: true, status: 200,
      json: async () => ({ id: "m1", status: "draft", speakers: [] }),
    });
    const m = await fetchAdminMeeting("m1");
    expect(authedFetchMock).toHaveBeenCalledWith("/api/admin/meetings/m1");
    expect(m?.status).toBe("draft");
  });

  it("returns null on 404", async () => {
    authedFetchMock.mockResolvedValueOnce({ ok: false, status: 404 });
    expect(await fetchAdminMeeting("m1")).toBeNull();
  });
});

describe("mutations", () => {
  it("promoteMeeting PATCHes status=published", async () => {
    authedFetchMock.mockResolvedValueOnce({ ok: true, status: 200 });
    await promoteMeeting("m1");
    const [path, init] = authedFetchMock.mock.calls[0];
    expect(path).toBe("/api/admin/meetings/m1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ status: "published" });
  });

  it("archiveMeeting PATCHes status=archived", async () => {
    authedFetchMock.mockResolvedValueOnce({ ok: true, status: 200 });
    await archiveMeeting("m1");
    const [, init] = authedFetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ status: "archived" });
  });

  it("throws when the PATCH fails", async () => {
    authedFetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(promoteMeeting("m1")).rejects.toThrow();
  });
});
