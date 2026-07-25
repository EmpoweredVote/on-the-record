import { describe, it, expect, vi } from "vitest";
import { createAudioAdapter, type MediaLike } from "./audioAdapter";

function fakeEl(over: Partial<MediaLike> = {}): MediaLike {
  return {
    currentTime: 0,
    paused: true,
    ended: false,
    play: vi.fn(() => Promise.resolve()),
    ...over,
  };
}

describe("createAudioAdapter", () => {
  it("seekTo sets currentTime and starts playback", () => {
    const el = fakeEl();
    createAudioAdapter(el).seekTo(42);
    expect(el.currentTime).toBe(42);
    expect(el.play).toHaveBeenCalledTimes(1);
  });

  it("seekTo swallows a rejected play() and still seeks", () => {
    const el = fakeEl({ play: vi.fn(() => Promise.reject(new Error("autoplay blocked"))) });
    expect(() => createAudioAdapter(el).seekTo(5)).not.toThrow();
    expect(el.currentTime).toBe(5);
  });

  it("getCurrentTime reflects the element", () => {
    expect(createAudioAdapter(fakeEl({ currentTime: 12.5 })).getCurrentTime()).toBe(12.5);
  });

  it("isPlaying is true only when not paused and not ended", () => {
    expect(createAudioAdapter(fakeEl({ paused: false, ended: false })).isPlaying()).toBe(true);
    expect(createAudioAdapter(fakeEl({ paused: true })).isPlaying()).toBe(false);
    expect(createAudioAdapter(fakeEl({ paused: false, ended: true })).isPlaying()).toBe(false);
  });
});
