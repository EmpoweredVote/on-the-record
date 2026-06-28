import { describe, it, expect } from "vitest";
import { buildThumbnailModel, youtubeThumbnailUrl } from "./thumbnail";
import type { Meeting } from "./types";

const base: Meeting = {
  meeting_id: "m1",
  slug: null,
  title: null,
  event_kind: "council",
  city: "Asheville",
  chamber_id: null,
  race_id: null,
  meeting_type: "Regular Meeting",
  meeting_date: "2026-02-25",
  source_url: null,
  playback_kind: "youtube",
  playback_url: "abc123",
  duration_seconds: 8040,
  clip_start_seconds: null,
  clip_end_seconds: null,
  summary_preview: "A summary.",
  speakers: [],
  event_orgs: [],
  source_title: null,
};

describe("youtubeThumbnailUrl", () => {
  it("builds the public hqdefault URL", () => {
    expect(youtubeThumbnailUrl("abc123")).toBe(
      "https://img.youtube.com/vi/abc123/hqdefault.jpg"
    );
  });
});

describe("buildThumbnailModel", () => {
  it("uses the YouTube frame for youtube meetings", () => {
    const m = buildThumbnailModel(base);
    expect(m.imageSrc).toBe("https://img.youtube.com/vi/abc123/hqdefault.jpg");
    expect(m.showPlay).toBe(true);
    expect(m.transcriptOnly).toBe(false);
    expect(m.duration).toBe("2h 14m");
  });

  it("renders the info tile (no frame) for file videos but keeps play + duration", () => {
    const m = buildThumbnailModel({
      ...base,
      playback_kind: "file",
      playback_url: "https://cdn.example.com/v.mp4",
    });
    expect(m.imageSrc).toBeNull();
    expect(m.showPlay).toBe(true);
    expect(m.transcriptOnly).toBe(false);
    expect(m.duration).toBe("2h 14m");
    expect(m.location).toBe("Asheville");
    expect(m.date).toBe("Feb 25, 2026");
  });

  it("treats hls the same as file", () => {
    const m = buildThumbnailModel({
      ...base,
      playback_kind: "hls",
      playback_url: "https://cdn.example.com/v.m3u8",
    });
    expect(m.imageSrc).toBeNull();
    expect(m.showPlay).toBe(true);
  });

  it("marks no-video meetings transcript-only with no play or duration", () => {
    const m = buildThumbnailModel({
      ...base,
      playback_kind: null,
      playback_url: null,
    });
    expect(m.imageSrc).toBeNull();
    expect(m.showPlay).toBe(false);
    expect(m.duration).toBeNull();
    expect(m.transcriptOnly).toBe(true);
    expect(m.location).toBe("Asheville");
    expect(m.date).toBe("Feb 25, 2026");
  });

  it("treats a youtube meeting with no video id as not playable (info tile)", () => {
    const m = buildThumbnailModel({ ...base, playback_url: null });
    expect(m.imageSrc).toBeNull();
    expect(m.showPlay).toBe(false);
    expect(m.transcriptOnly).toBe(true);
  });

  it("falls back to the meeting title for location when city is null", () => {
    const m = buildThumbnailModel({
      ...base,
      city: null,
      title: "Special Joint Session",
    });
    expect(m.location).toBe("Special Joint Session");
  });
});
