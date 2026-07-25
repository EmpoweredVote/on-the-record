import type { PlayerAdapter } from "@/app/meetings/[meetingId]/players/adapter";

// Minimal media-element surface the audio player needs. Declaring it here lets
// the adapter be unit tested in the node env without a DOM (HTMLAudioElement
// structurally satisfies it).
export interface MediaLike {
  currentTime: number;
  paused: boolean;
  ended: boolean;
  play(): Promise<void> | void;
}

// Wire a media element to the shared PlayerAdapter used by MeetingView for
// click-to-seek. seekTo starts playback on seek (matching FilePlayer); a
// rejected play() (e.g. autoplay policy) is swallowed so a seek never throws.
export function createAudioAdapter(el: MediaLike): PlayerAdapter {
  return {
    seekTo: (seconds: number) => {
      el.currentTime = seconds;
      Promise.resolve(el.play()).catch(() => {});
    },
    getCurrentTime: () => el.currentTime,
    isPlaying: () => !el.paused && !el.ended,
  };
}
