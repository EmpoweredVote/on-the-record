"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import Link from "next/link";
import type { Meeting, Segment, SummarySection } from "@/lib/types";
import type { PlayerAdapter } from "./players/adapter";
import YouTubePlayer from "./players/YouTubePlayer";
import FilePlayer from "./players/FilePlayer";
import { formatTime } from "@/lib/format";
import ProvenanceBadge, { speakerStatus } from "@/components/ProvenanceBadge";

// Index of the segment playing at time t (last segment with start_time <= t).
function segmentIndexAt(starts: number[], t: number): number {
  let lo = 0;
  let hi = starts.length - 1;
  let ans = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (starts[mid] <= t) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

function Highlighted({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const lower = text.toLowerCase();
  const q = query.toLowerCase();
  const parts: React.ReactNode[] = [];
  let i = 0;
  for (let at = lower.indexOf(q); at !== -1; at = lower.indexOf(q, i)) {
    if (at > i) parts.push(text.slice(i, at));
    parts.push(<mark key={at}>{text.slice(at, at + q.length)}</mark>);
    i = at + q.length;
  }
  parts.push(text.slice(i));
  return <>{parts}</>;
}

export default function MeetingView({
  meeting,
  segments,
  outline = [],
}: {
  meeting: Meeting;
  segments: Segment[];
  outline?: SummarySection[];
}) {
  const adapterRef = useRef<PlayerAdapter | null>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const pendingSeek = useRef<number | null>(null);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [follow, setFollow] = useState(true);
  const [query, setQuery] = useState("");
  const [matchCursor, setMatchCursor] = useState(0);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const starts = useMemo(() => segments.map((s) => s.start_time), [segments]);

  const matches = useMemo(() => {
    if (!query.trim()) return [];
    const q = query.trim().toLowerCase();
    return segments.reduce<number[]>((acc, seg, i) => {
      if (seg.text.toLowerCase().includes(q)) acc.push(i);
      return acc;
    }, []);
  }, [query, segments]);

  const onAdapter = useCallback((adapter: PlayerAdapter) => {
    adapterRef.current = adapter;
    if (pendingSeek.current !== null) {
      adapter.seekTo(pendingSeek.current);
      pendingSeek.current = null;
    }
  }, []);

  // Deep links: ?t=SECONDS seeks the player; #seg-N scrolls without a player.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const t = Number(params.get("t"));
    if (Number.isFinite(t) && t > 0) {
      if (adapterRef.current) adapterRef.current.seekTo(t);
      else pendingSeek.current = t;
      // Scroll the target segment into view; highlight catches up on play.
      const idx = segmentIndexAt(starts, t);
      document
        .getElementById(`seg-${segments[idx]?.segment_id}`)
        ?.scrollIntoView({ block: "center" });
    } else if (window.location.hash.startsWith("#seg-")) {
      document
        .querySelector(window.location.hash)
        ?.scrollIntoView({ block: "center" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Playback sync: poll the adapter and highlight the current segment.
  useEffect(() => {
    const timer = setInterval(() => {
      const adapter = adapterRef.current;
      if (!adapter || !adapter.isPlaying()) return;
      const idx = segmentIndexAt(starts, adapter.getCurrentTime());
      setActiveIndex((prev) => (prev === idx ? prev : idx));
    }, 300);
    return () => clearInterval(timer);
  }, [starts]);

  // Auto-scroll the active segment into view while following.
  useEffect(() => {
    if (activeIndex < 0 || !follow) return;
    document
      .getElementById(`seg-${segments[activeIndex].segment_id}`)
      ?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeIndex, follow, segments]);

  // Manual scroll pauses following so the reader can move around freely.
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const stopFollowing = () => setFollow(false);
    el.addEventListener("wheel", stopFollowing, { passive: true });
    el.addEventListener("touchmove", stopFollowing, { passive: true });
    return () => {
      el.removeEventListener("wheel", stopFollowing);
      el.removeEventListener("touchmove", stopFollowing);
    };
  }, []);

  const seekToSegment = (i: number) => {
    adapterRef.current?.seekTo(segments[i].start_time);
    setActiveIndex(i);
  };

  const seekToTime = useCallback((seconds: number) => {
    if (adapterRef.current) adapterRef.current.seekTo(seconds);
    else pendingSeek.current = seconds;
    const idx = segmentIndexAt(starts, seconds);
    const target = idx === -1 ? segments.length - 1 : Math.max(0, idx);
    document.getElementById(`seg-${segments[target]?.segment_id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [segments, starts]);

  const copyLink = async (i: number) => {
    const seg = segments[i];
    const url = `${window.location.origin}/meetings/${meeting.meeting_id}?t=${Math.floor(seg.start_time)}#seg-${seg.segment_id}`;
    await navigator.clipboard.writeText(url);
    setCopiedId(seg.segment_id);
    setTimeout(() => setCopiedId(null), 1500);
  };

  const jumpToMatch = (cursor: number) => {
    if (!matches.length) return;
    const wrapped = ((cursor % matches.length) + matches.length) % matches.length;
    setMatchCursor(wrapped);
    setFollow(false);
    document
      .getElementById(`seg-${segments[matches[wrapped]].segment_id}`)
      ?.scrollIntoView({ block: "center" });
  };

  const player =
    meeting.playback_kind === "youtube" && meeting.playback_url ? (
      <YouTubePlayer videoId={meeting.playback_url} onAdapter={onAdapter} />
    ) : (meeting.playback_kind === "file" || meeting.playback_kind === "hls") &&
      meeting.playback_url ? (
      <FilePlayer
        src={meeting.playback_url}
        kind={meeting.playback_kind}
        onAdapter={onAdapter}
      />
    ) : null;

  // Build a label → provenance-status map from the meeting's speaker list.
  const statusByLabel = new Map(
    (meeting.speakers ?? []).map((sp) => [sp.label, speakerStatus(sp.id_method)] as const)
  );

  return (
    <div className="meetingLayout">
      <div className="mediaPane">
        {player ?? (
          <div className="noPlayer">
            <p>No embeddable video for this meeting.</p>
            {meeting.source_url && (
              <p>
                <a href={meeting.source_url} target="_blank" rel="noreferrer">
                  Watch the source ↗
                </a>
              </p>
            )}
          </div>
        )}
        <div className="searchBar">
          <input
            type="search"
            placeholder="Search this transcript…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setMatchCursor(0);
            }}
            aria-label="Search transcript"
          />
          {query.trim() && (
            <span className="matchNav">
              {matches.length ? `${matchCursor + 1} / ${matches.length}` : "0 matches"}
              <button onClick={() => jumpToMatch(matchCursor - 1)} disabled={!matches.length} aria-label="Previous match">↑</button>
              <button onClick={() => jumpToMatch(matchCursor + 1)} disabled={!matches.length} aria-label="Next match">↓</button>
            </span>
          )}
          {player && (
            <label className="followToggle">
              <input
                type="checkbox"
                checked={follow}
                onChange={(e) => setFollow(e.target.checked)}
              />
              Follow video
            </label>
          )}
        </div>

        {outline.length > 0 && (
          <section className="outline">
            <h2>Discussed</h2>
            <ul>
              {outline.map((sec) => (
                <li key={sec.sort_order} className="outlineItem">
                  <button
                    type="button"
                    className="outlineLink"
                    onClick={() => seekToTime(Math.floor(sec.start_time ?? 0))}
                  >
                    <span className="outlineTitle">{sec.title}</span>
                    <span className="outlineTime">{formatTime(sec.start_time ?? 0)}</span>
                  </button>
                  {sec.topics.length > 0 && (
                    <span className="outlineTopics">
                      {sec.topics.map((t) => (
                        <span key={t.key} className="topicLabel">
                          <Link href={`/topics/${t.key}`}>{t.title ?? t.key}</Link>
                          <ProvenanceBadge status={t.status} />
                        </span>
                      ))}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <div className="transcriptPane" ref={listRef}>
        {segments.map((seg, i) => (
          <div
            key={seg.segment_id}
            id={`seg-${seg.segment_id}`}
            className={`segment${i === activeIndex ? " active" : ""}`}
          >
            <div className="segmentMeta">
              <button
                className="timestamp"
                onClick={() => seekToSegment(i)}
                title="Jump video to this moment"
              >
                {formatTime(seg.start_time)}
              </button>
              <span className="speaker">
                {seg.politician_slug ? (
                  <Link
                    href={`/people/${seg.politician_slug}`}
                    className="speakerLink"
                    title="View this person's appearances"
                  >
                    {seg.speaker_name || seg.speaker_label}
                  </Link>
                ) : (
                  seg.speaker_name || seg.speaker_label
                )}
              </span>
              {(i === 0 || segments[i - 1].speaker_label !== seg.speaker_label) && (
                <ProvenanceBadge status={statusByLabel.get(seg.speaker_label) ?? "predicted"} />
              )}
              <button
                className="copyLink"
                onClick={() => copyLink(i)}
                title="Copy link to this moment"
              >
                {copiedId === seg.segment_id ? "✓ copied" : "🔗"}
              </button>
            </div>
            <p className="segmentText">
              <Highlighted text={seg.text} query={query.trim()} />
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
