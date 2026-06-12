"use client";

import { useEffect, useRef } from "react";
import type { PlayerAdapter } from "./adapter";

interface YTPlayer {
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  getCurrentTime(): number;
  getPlayerState(): number;
  destroy(): void;
}

interface YTNamespace {
  Player: new (
    el: HTMLElement,
    opts: {
      videoId: string;
      playerVars?: Record<string, number>;
      events?: { onReady?: () => void };
    }
  ) => YTPlayer;
  PlayerState: { PLAYING: number };
}

declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

let apiPromise: Promise<void> | null = null;

function loadIframeApi(): Promise<void> {
  if (window.YT?.Player) return Promise.resolve();
  if (!apiPromise) {
    apiPromise = new Promise((resolve) => {
      const prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        prev?.();
        resolve();
      };
      const tag = document.createElement("script");
      tag.src = "https://www.youtube.com/iframe_api";
      document.head.appendChild(tag);
    });
  }
  return apiPromise;
}

export default function YouTubePlayer({
  videoId,
  onAdapter,
}: {
  videoId: string;
  onAdapter: (adapter: PlayerAdapter) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let player: YTPlayer | undefined;
    let cancelled = false;

    loadIframeApi().then(() => {
      if (cancelled || !hostRef.current || !window.YT) return;
      const yt = window.YT;
      player = new yt.Player(hostRef.current, {
        videoId,
        playerVars: { playsinline: 1, rel: 0 },
        events: {
          onReady: () => {
            onAdapter({
              seekTo: (s: number) => player?.seekTo(s, true),
              getCurrentTime: () => player?.getCurrentTime() ?? 0,
              isPlaying: () =>
                player?.getPlayerState() === yt.PlayerState.PLAYING,
            });
          },
        },
      });
    });

    return () => {
      cancelled = true;
      player?.destroy();
    };
  }, [videoId, onAdapter]);

  return (
    <div className="playerBox">
      <div ref={hostRef} />
    </div>
  );
}
