"use client";

import { useEffect, useRef } from "react";
import type { PlayerAdapter } from "./adapter";

// Native <video> player for direct media URLs — CATS TV blob .m4v files,
// any .mp4/.webm, and HLS (natively on Safari; via hls.js elsewhere).
export default function FilePlayer({
  src,
  kind,
  onAdapter,
}: {
  src: string;
  kind: "file" | "hls";
  onAdapter: (adapter: PlayerAdapter) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let hls: { destroy(): void } | null = null;

    if (kind === "hls" && !video.canPlayType("application/vnd.apple.mpegurl")) {
      import("hls.js").then(({ default: Hls }) => {
        if (Hls.isSupported() && videoRef.current) {
          const instance = new Hls();
          instance.loadSource(src);
          instance.attachMedia(videoRef.current);
          hls = instance;
        }
      });
    } else {
      video.src = src;
    }

    onAdapter({
      seekTo: (s: number) => {
        video.currentTime = s;
        video.play().catch(() => {});
      },
      getCurrentTime: () => video.currentTime,
      isPlaying: () => !video.paused && !video.ended,
    });

    return () => hls?.destroy();
  }, [src, kind, onAdapter]);

  return (
    <div className="playerBox">
      <video ref={videoRef} controls preload="metadata" />
    </div>
  );
}
