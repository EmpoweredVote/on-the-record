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

    if (kind === "hls") {
      // Prefer hls.js wherever MSE is available (Chrome, Firefox, Edge, desktop
      // Safari) and use native HLS only as a fallback (iOS / older Safari).
      // Order matters: Chrome reports
      // canPlayType("application/vnd.apple.mpegurl") === "maybe" (truthy) but
      // CANNOT actually play HLS natively — pointing <video>.src at the manifest
      // stalls with MEDIA_ERR_SRC_NOT_SUPPORTED on seek. So native must never be
      // the first choice.
      import("hls.js").then(({ default: Hls }) => {
        const el = videoRef.current;
        if (!el) return;
        if (Hls.isSupported()) {
          const instance = new Hls();
          instance.loadSource(src);
          instance.attachMedia(el);
          hls = instance;
        } else if (el.canPlayType("application/vnd.apple.mpegurl")) {
          el.src = src; // iOS / older Safari: real native HLS
        }
        // else: no HLS support at all; leave the empty <video>.
      });
    } else {
      video.src = src; // direct mp4/webm/blob
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
