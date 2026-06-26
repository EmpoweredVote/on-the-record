"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { formatMeetingDate, formatTime } from "@/lib/format";
import type { SearchResult } from "@/lib/types";

// Runtime requests from the browser — needs the NEXT_PUBLIC_ env var,
// baked in at build time. The build-time EV_ACCOUNTS_URL is not visible here.
const API_BASE = (process.env.NEXT_PUBLIC_EV_ACCOUNTS_URL ?? "").replace(/\/$/, "");
const PAGE_SIZE = 25; // keep in sync with SEARCH_PAGE_SIZE in ev-accounts
const MAX_PAGE = 400; // keep in sync with MAX_PAGE in ev-accounts

interface SpeakerOption {
  id: string;
  name: string;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapResult(r: any): SearchResult {
  return {
    meeting_id: r.meetingId,
    city: r.city,
    meeting_type: r.meetingType,
    meeting_date: r.date,
    segment_id: r.segmentIndex,
    start_time: r.startTime,
    end_time: r.endTime,
    speaker_name: r.speakerName ?? null,
    politician_id: r.politicianId ?? null,
    snippet: r.snippet ?? "",
  };
}

// ts_headline emits [[[match]]] sentinels (never HTML) — split into <mark>
// React nodes so nothing goes through dangerouslySetInnerHTML. Transcript
// text containing a literal [[[ would only desync the even/odd highlighting
// (cosmetic); everything stays a text node either way.
function renderSnippet(snippet: string) {
  const parts = snippet.split(/\[\[\[|\]\]\]/);
  return parts.map((part, i) =>
    i % 2 === 1 ? <mark key={i}>{part}</mark> : <span key={i}>{part}</span>
  );
}

interface MeetingGroup {
  meeting_id: string;
  city: string;
  meeting_type: string;
  meeting_date: string;
  hits: SearchResult[];
}

type Status = "idle" | "loading" | "done" | "error";

export default function SearchView({
  cities,
  speakers,
}: {
  cities: string[];
  speakers: SpeakerOption[];
}) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const urlQ = searchParams.get("q") ?? "";
  const urlCity = searchParams.get("city") ?? "";
  const urlSpeaker = searchParams.get("speaker") ?? "";
  const urlPage = Math.min(
    MAX_PAGE,
    Math.max(1, Math.floor(Number(searchParams.get("page") ?? "1") || 1))
  );

  const [input, setInput] = useState(urlQ);
  const [city, setCity] = useState(urlCity);
  const [speaker, setSpeaker] = useState(urlSpeaker);
  const [status, setStatus] = useState<Status>("idle");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  // Bumped on every submit so re-searching an unchanged query after an
  // error re-runs the fetch effect (the URL alone wouldn't change).
  const [retryNonce, setRetryNonce] = useState(0);

  // Keep the form in sync with the URL (back/forward navigation).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setInput(urlQ);
    setCity(urlCity);
    setSpeaker(urlSpeaker);
  }, [urlQ, urlCity, urlSpeaker]);

  // The URL is the source of truth: fetch whenever ?q= is present.
  useEffect(() => {
    if (!urlQ) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStatus("idle");
      setResults([]);
      setTotalCount(0);
      return;
    }
    const controller = new AbortController();
    const params = new URLSearchParams({ q: urlQ, page: String(urlPage) });
    if (urlCity) params.set("city", urlCity);
    if (urlSpeaker) params.set("speaker", urlSpeaker);
    setStatus("loading");
    fetch(`${API_BASE}/api/search?${params}`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`search failed: ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setResults((data.results as unknown[]).map(mapResult));
        setTotalCount(data.totalCount);
        setStatus("done");
      })
      .catch((err: unknown) => {
        if ((err as Error).name !== "AbortError") setStatus("error");
      });
    return () => controller.abort();
  }, [urlQ, urlCity, urlSpeaker, urlPage, retryNonce]);

  const navigate = useCallback(
    (q: string, c: string, s: string, page: number) => {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (c) params.set("city", c);
      if (s) params.set("speaker", s);
      if (page > 1) params.set("page", String(page));
      router.replace(`/search?${params}`);
    },
    [router]
  );

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setRetryNonce((n) => n + 1);
    navigate(input.trim(), city, speaker, 1);
  }

  // Group by meeting, preserving API rank order (first hit wins position).
  const groups: MeetingGroup[] = [];
  for (const r of results) {
    const existing = groups.find((g) => g.meeting_id === r.meeting_id);
    if (existing) {
      existing.hits.push(r);
    } else {
      groups.push({
        meeting_id: r.meeting_id,
        city: r.city,
        meeting_type: r.meeting_type,
        meeting_date: r.meeting_date,
        hits: [r],
      });
    }
  }

  const totalPages = Math.min(MAX_PAGE, Math.max(1, Math.ceil(totalCount / PAGE_SIZE)));

  return (
    <div className="searchView">
      <form className="searchForm" onSubmit={onSubmit}>
        <input
          type="search"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder='e.g. affordable housing, "parking garage"'
          aria-label="Search transcripts"
          maxLength={200}
        />
        <select
          value={city}
          onChange={(e) => setCity(e.target.value)}
          aria-label="Filter by city"
        >
          <option value="">All cities</option>
          {cities.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select
          value={speaker}
          onChange={(e) => setSpeaker(e.target.value)}
          aria-label="Filter by speaker"
        >
          <option value="">All speakers</option>
          {speakers.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <button type="submit">Search</button>
      </form>

      {status === "idle" && (
        <p className="searchHint">
          Try a phrase in quotes, or exclude words with a leading dash.
        </p>
      )}
      {status === "loading" && (
        <p className="searchHint" role="status">
          Searching…
        </p>
      )}
      {status === "error" && (
        <p className="searchHint" role="status">
          Search is temporarily unavailable. Please try again shortly.
        </p>
      )}
      {status === "done" && totalCount === 0 && (
        <p className="searchHint" role="status">
          No results for &ldquo;{urlQ}&rdquo;.
        </p>
      )}

      {status === "done" && totalCount > 0 && (
        <>
          <p className="searchCount">
            {totalCount} result{totalCount === 1 ? "" : "s"}
          </p>
          {groups.map((g) => (
            <section key={g.meeting_id} className="searchGroup">
              <h2>
                <Link href={`/meetings/${g.meeting_id}`}>
                  {g.city} {g.meeting_type} — {formatMeetingDate(g.meeting_date)}
                </Link>
              </h2>
              <ul className="searchHits">
                {g.hits.map((hit) => (
                  <li key={hit.segment_id} className="searchHit">
                    <Link
                      href={`/meetings/${hit.meeting_id}?t=${Math.floor(hit.start_time)}#seg-${hit.segment_id}`}
                      className="timestampLink"
                    >
                      {formatTime(hit.start_time)}
                    </Link>{" "}
                    {hit.speaker_name &&
                      (hit.politician_id ? (
                        <Link
                          href={`/people/${hit.politician_id}`}
                          className="speakerLink searchSpeaker"
                        >
                          {hit.speaker_name}
                        </Link>
                      ) : (
                        <span className="searchSpeaker">{hit.speaker_name}</span>
                      ))}
                    <p className="searchSnippet">{renderSnippet(hit.snippet)}</p>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {totalPages > 1 && (
            <div className="searchPager">
              <button
                disabled={urlPage <= 1}
                onClick={() => navigate(urlQ, urlCity, urlSpeaker, urlPage - 1)}
              >
                ← Previous
              </button>
              <span>
                Page {urlPage} of {totalPages}
              </span>
              <button
                disabled={urlPage >= totalPages}
                onClick={() => navigate(urlQ, urlCity, urlSpeaker, urlPage + 1)}
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
