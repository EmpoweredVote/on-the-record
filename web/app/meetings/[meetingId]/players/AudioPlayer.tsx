"use client";

import { useEffect, useRef } from "react";
import type { PlayerAdapter } from "./adapter";
import { createAudioAdapter } from "@/lib/audioAdapter";

// Native <audio> player for podcast/radio episodes (playback_kind "audio").
// Shows the episode/show artwork as album art above the control bar when a
// thumbnail is present; controls-only otherwise. Wires the shared PlayerAdapter
// so transcript/vote click-to-seek jumps the audio.
export default function AudioPlayer({
  src,
  thumbnailUrl,
  onAdapter,
}: {
  src: string;
  thumbnailUrl?: string | null;
  onAdapter: (adapter: PlayerAdapter) => void;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.src = src;
    onAdapter(createAudioAdapter(audio));
  }, [src, onAdapter]);

  return (
    <div className="audioBox">
      {thumbnailUrl ? (
        // Static export has no image optimizer; an intentional <img> is correct here.
        // eslint-disable-next-line @next/next/no-img-element
        <img className="audioArt" src={thumbnailUrl} alt="" loading="lazy" />
      ) : null}
      <audio ref={audioRef} controls preload="metadata" />
    </div>
  );
}
