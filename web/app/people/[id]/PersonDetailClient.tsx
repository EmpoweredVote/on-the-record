"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  fetchAppearances,
  fetchMeeting,
  fetchPerson,
  fetchSummary,
} from "@/lib/queries";
import { formatMeetingDate, formatTime, meetingTitle } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import { usePathParam } from "@/lib/usePathParam";
import { useCandidates } from "@/lib/useCandidates";
import { topicForTime } from "@/lib/topicForTime";
import { meetingTopics } from "@/lib/outline";
import { quoteDeepLink } from "@/lib/sourceLink";
import { applyStar, groupByLabel } from "@/lib/candidates";
import { candidatesToMarkdown } from "@/lib/candidateMarkdown";
import type { Appearance, Candidate, SummarySection } from "@/lib/types";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import NotFound from "@/components/NotFound";
import PersonPhoto from "@/components/PersonPhoto";

const ESSENTIALS_BASE = "https://essentials.city";

interface MeetingMeta {
  sections: SummarySection[];
  source_url: string | null;
  playback_kind: string | null;
}

// Fetch appearances, then each meeting's summary (for section topics) and detail
// (for the source url that builds deep-links). Parallelized across meetings.
async function loadTranscript(id: string) {
  const appearances = await fetchAppearances(id);
  const entries = await Promise.all(
    appearances.map(async (a): Promise<[string, MeetingMeta]> => {
      const [summary, meeting] = await Promise.all([
        fetchSummary(a.meeting_id),
        fetchMeeting(a.meeting_id),
      ]);
      return [
        a.meeting_id,
        {
          sections: summary?.sections ?? [],
          source_url: meeting?.source_url ?? null,
          playback_kind: meeting?.playback_kind ?? null,
        },
      ];
    })
  );
  return { appearances, meta: Object.fromEntries(entries) as Record<string, MeetingMeta> };
}

export default function PersonDetailClient() {
  const id = usePathParam(1);
  const ready = id != null;

  const personQ = useApi(() => (ready ? fetchPerson(id) : Promise.resolve(null)), [id]);
  const dataQ = useApi(
    () => (ready ? loadTranscript(id) : Promise.resolve(null)),
    [id]
  );

  const collection = useCandidates(ready ? id : null);

  const [view, setView] = useState<"read" | "curate">("read");

  if (!ready || personQ.loading)
    return (
      <main className="indexPage personPage">
        <Loading label="Loading person…" />
      </main>
    );
  if (personQ.error)
    return (
      <main className="indexPage personPage">
        <ErrorState />
      </main>
    );
  if (!personQ.data) return <NotFound message="Person not found." />;

  const person = personQ.data;
  const appearances = dataQ.data?.appearances ?? [];
  const meta = dataQ.data?.meta ?? {};
  const liveCount = collection.cands.length;

  return (
    <main className="indexPage personPage skim">
      <Link href="/people" className="backLink">
        ← All people
      </Link>
      <header className="personHeader">
        <PersonPhoto name={person.name} url={person.headshot_url} large />
        <div>
          <h1>{person.name}</h1>
          <p className="personOffice">
            {[person.office_title, person.district, person.jurisdiction]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {person.politician_id && (
            <a
              className="sourceLink"
              href={`${ESSENTIALS_BASE}/politician/${person.politician_id}`}
              target="_blank"
              rel="noreferrer"
            >
              Full profile on essentials ↗
            </a>
          )}
        </div>
      </header>
      {person.bio_text && <p className="personBio">{person.bio_text}</p>}

      <div className="skimTabs" role="tablist">
        <button
          role="tab"
          aria-selected={view === "read"}
          className={view === "read" ? "on" : ""}
          onClick={() => setView("read")}
        >
          Read transcript
        </button>
        <button
          role="tab"
          aria-selected={view === "curate"}
          className={view === "curate" ? "on" : ""}
          onClick={() => setView("curate")}
        >
          Curation
          <span className="skimCount">{liveCount}</span>
        </button>
      </div>

      {view === "read" ? (
        <ReadView
          appearances={appearances}
          meta={meta}
          loading={dataQ.loading}
          collection={collection}
        />
      ) : (
        <CurateView personName={person.name} collection={collection} />
      )}

      {view === "read" && (
        <Drawer collection={collection} onOpenCuration={() => setView("curate")} />
      )}
    </main>
  );
}

/* ============================ Reading space ============================ */

interface SelPayload {
  text: string;
  x: number;
  y: number;
  appearance: Appearance;
  segId: number;
  startTime: number;
}

function ReadView({
  appearances,
  meta,
  loading,
  collection,
}: {
  appearances: Appearance[];
  meta: Record<string, MeetingMeta>;
  loading: boolean;
  collection: ReturnType<typeof useCandidates>;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // sticky-bar topic per meeting, tracked on scroll
  const [barTopic, setBarTopic] = useState<Record<string, SummarySection["topics"][number] | null>>({});
  // floating "grab selection" button + its payload
  const [sel, setSel] = useState<SelPayload | null>(null);
  // quick-label popover for the just-grabbed candidate
  const [quick, setQuick] = useState<{ id: string; x: number; y: number } | null>(null);
  const scrollRoot = useRef<HTMLDivElement>(null);

  const toggle = (mid: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(mid)) next.delete(mid);
      else next.add(mid);
      return next;
    });

  // Grab a candidate, then offer an optional quick label at the cursor. Used by
  // both whole-turn grab and selection grab.
  const grab = useCallback(
    (a: Appearance, segId: number, startTime: number, text: string, x: number, y: number) => {
      const m = meta[a.meeting_id];
      const c = collection.add({
        meeting_id: a.meeting_id,
        meeting_title: meetingTitle(a),
        meeting_date: a.meeting_date,
        segment_id: segId,
        start_time: startTime,
        source_url: m?.source_url ?? null,
        playback_kind: m?.playback_kind ?? null,
        orig_text: text,
        edit_text: text,
        label: "",
        note: "",
        starred: false,
      });
      setQuick({ id: c.id, x, y });
    },
    [collection, meta]
  );

  // Selection grab — works across adjacent turns (the browser selection can
  // cross turn boundaries). Timestamp anchors to the turn the selection starts
  // in. Small delay lets the selection settle after mouseup.
  useEffect(() => {
    const onMouseUp = (e: MouseEvent) => {
      if ((e.target as HTMLElement)?.closest?.(".skimSelBtn")) return;
      window.setTimeout(() => {
        const s = window.getSelection();
        const txt = s?.toString().trim() ?? "";
        if (!s || txt.length < 3) {
          setSel(null);
          return;
        }
        const node = s.anchorNode;
        const anchorEl =
          node?.nodeType === 3 ? node.parentElement : (node as Element | null);
        const turnEl = anchorEl?.closest?.(".skimTurn") as HTMLElement | null;
        if (!turnEl || !scrollRoot.current?.contains(turnEl)) {
          setSel(null);
          return;
        }
        const mid = turnEl.closest<HTMLElement>(".skimMtg")?.dataset.mid;
        const appearance = appearances.find((a) => a.meeting_id === mid);
        if (!appearance) {
          setSel(null);
          return;
        }
        const r = s.getRangeAt(0).getBoundingClientRect();
        setSel({
          text: txt,
          x: r.left + r.width / 2,
          y: r.top,
          appearance,
          segId: Number(turnEl.dataset.seg),
          startTime: Number(turnEl.dataset.t),
        });
      }, 10);
    };
    document.addEventListener("mouseup", onMouseUp);
    return () => document.removeEventListener("mouseup", onMouseUp);
  }, [appearances]);

  // On scroll, for each expanded meeting find the top-most visible turn and set
  // the bar's topic from the section containing it.
  useEffect(() => {
    let raf = 0;
    const compute = () => {
      raf = 0;
      const next: Record<string, SummarySection["topics"][number] | null> = {};
      const bars = scrollRoot.current?.querySelectorAll<HTMLElement>(".skimBar");
      bars?.forEach((bar) => {
        const section = bar.closest<HTMLElement>(".skimMtg");
        if (!section) return;
        const mid = section.dataset.mid!;
        if (section.classList.contains("collapsed")) return;
        const turns = section.querySelectorAll<HTMLElement>(".skimTurn");
        const bottom = bar.getBoundingClientRect().bottom;
        let current: HTMLElement | null = turns[0] ?? null;
        turns.forEach((t) => {
          if (t.getBoundingClientRect().top <= bottom + 4) current = t;
        });
        const t = current ? Number(current.dataset.t) : NaN;
        next[mid] = Number.isNaN(t) ? null : topicForTime(meta[mid]?.sections, t);
      });
      setBarTopic(next);
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(compute);
    };
    compute();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [appearances, meta, collapsed]);

  if (loading && appearances.length === 0)
    return <Loading label="Loading transcript…" />;
  if (appearances.length === 0) return <p>No appearances on record.</p>;

  return (
    <div ref={scrollRoot}>
      <p className="skimHint">
        Hover a turn and click <b>＋ grab</b>, or select any text (even across
        turns) to grab exactly that. The sticky bar keeps the meeting + topic in
        view — <span className="skimChip pred">dashed = predicted</span>, solid =
        verified. Click a bar to collapse a meeting.
      </p>
      {appearances.map((a) => {
        const mid = a.meeting_id;
        const isCollapsed = collapsed.has(mid);
        const grabbedHere = collection.cands.filter((c) => c.meeting_id === mid).length;
        // When actively scrolling a tagged section, show that one topic (sticky
        // indicator). Otherwise (collapsed, or at an untagged intro) fall back to
        // the meeting's distinct topics so interviews don't read as "untagged".
        const scrollTopic = barTopic[mid];
        const topics = scrollTopic ? [scrollTopic] : meetingTopics(meta[mid]?.sections);
        return (
          <section
            key={mid}
            className={`skimMtg${isCollapsed ? " collapsed" : ""}`}
            data-mid={mid}
          >
            <div className="skimBar" onClick={() => toggle(mid)}>
              <button className="skimCollapse" aria-label="Collapse meeting">
                ▾
              </button>
              <div className="skimBarMain">
                <div className="skimTitleRow">
                  <h2>{meetingTitle(a)}</h2>
                  {grabbedHere > 0 && (
                    <span className="skimGrabCount">{grabbedHere} grabbed</span>
                  )}
                  <span className="skimDate">{formatMeetingDate(a.meeting_date)}</span>
                </div>
                <div className="skimTopicRow">
                  <span className="skimLead">Topic</span>
                  {topics.length > 0 ? (
                    topics.map((t) => (
                      <span
                        key={t.key}
                        className={`skimChip${t.status === "predicted" ? " pred" : ""}`}
                      >
                        <span className="skimDot" />
                        {t.title ?? t.key}
                      </span>
                    ))
                  ) : (
                    <span className="skimUntagged">— procedural / untagged</span>
                  )}
                </div>
              </div>
            </div>
            <div className="skimTurns">
              {a.segments.map((seg) => (
                <Turn
                  key={seg.segment_id}
                  appearance={a}
                  meta={meta[mid]}
                  segId={seg.segment_id}
                  startTime={seg.start_time}
                  text={seg.text}
                  collection={collection}
                  onGrab={(x, y) =>
                    grab(a, seg.segment_id, seg.start_time, seg.text, x, y)
                  }
                />
              ))}
            </div>
          </section>
        );
      })}
      {sel && (
        <button
          className="skimSelBtn"
          style={{ left: sel.x, top: sel.y }}
          onClick={() => {
            grab(sel.appearance, sel.segId, sel.startTime, sel.text, sel.x, sel.y);
            window.getSelection()?.removeAllRanges();
            setSel(null);
          }}
        >
          Grab selection
        </button>
      )}
      {quick && (
        <QuickLabel
          x={quick.x}
          y={quick.y}
          onSave={(v) => {
            collection.update(quick.id, { label: v });
            setQuick(null);
          }}
          onSkip={() => setQuick(null)}
        />
      )}
    </div>
  );
}

function QuickLabel({
  x,
  y,
  onSave,
  onSkip,
}: {
  x: number;
  y: number;
  onSave: (v: string) => void;
  onSkip: () => void;
}) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <div
      className="skimQuick"
      style={{ left: Math.min(x, (typeof window !== "undefined" ? window.innerWidth : 9999) - 130), top: y }}
    >
      <label>Quick label (optional)</label>
      <input
        ref={ref}
        value={value}
        placeholder="e.g. housing insurance"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") onSave(value.trim());
          if (e.key === "Escape") onSkip();
        }}
      />
      <div className="skimQuickRow">
        <button className="skimQuickSkip" onClick={onSkip}>
          Skip
        </button>
        <button className="skimQuickSave" onClick={() => onSave(value.trim())}>
          Save
        </button>
      </div>
    </div>
  );
}

function Turn({
  appearance,
  meta,
  segId,
  startTime,
  text,
  collection,
  onGrab,
}: {
  appearance: Appearance;
  meta: MeetingMeta | undefined;
  segId: number;
  startTime: number;
  text: string;
  collection: ReturnType<typeof useCandidates>;
  onGrab: (x: number, y: number) => void;
}) {
  const grabbed = collection.cands.some(
    (c) => c.meeting_id === appearance.meeting_id && c.segment_id === segId
  );
  // Timestamps go to our own meeting page at the moment (player seeks via ?t=,
  // transcript scrolls via #seg-), not out to the raw YouTube source.
  const meetingLink = `/meetings/${appearance.meeting_id}?t=${Math.floor(
    startTime
  )}#seg-${segId}`;

  const handle = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (grabbed) {
      // toggle off: remove every candidate originating from this turn
      collection.cands
        .filter((c) => c.meeting_id === appearance.meeting_id && c.segment_id === segId)
        .forEach((c) => collection.remove(c.id));
      return;
    }
    onGrab(e.clientX, e.clientY);
  };

  return (
    <div
      className={`skimTurn${grabbed ? " grabbed" : ""}`}
      data-t={startTime}
      data-seg={segId}
    >
      <Link className="skimTs" href={meetingLink}>
        {formatTime(startTime)}
      </Link>
      <span className="skimTurnText">{text}</span>
      <button className="skimGrabBtn" onClick={handle}>
        {grabbed ? "✓ grabbed" : "＋ grab"}
      </button>
    </div>
  );
}

/* ============================ Curation space ============================ */

function CurateView({
  personName,
  collection,
}: {
  personName: string;
  collection: ReturnType<typeof useCandidates>;
}) {
  const [exportOpen, setExportOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const groups = groupByLabel(collection.cands);
  const md = candidatesToMarkdown(personName, collection.cands);

  const copy = () => {
    navigator.clipboard.writeText(md).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    });
  };

  return (
    <div className="skimCurate">
      <div className="skimCurateHead">
        <h2>Candidate quotes — {personName}</h2>
        <button
          className="skimExportBtn"
          onClick={() => setExportOpen(true)}
          disabled={collection.cands.length === 0}
        >
          Export Markdown
        </button>
      </div>
      <p className="skimHint">
        Edit wording on the right — the verbatim grab stays on the left. Group by
        topic label; ★ marks the one quote that would go live for that topic.
        Reconcile labels to real compass topics later, at publish.
      </p>

      {collection.cands.length === 0 ? (
        <p className="skimEmpty">
          No candidates yet. Grab some quotes from the transcript.
        </p>
      ) : (
        groups.map(([label, cands]) => (
          <div key={label} className="skimGroup">
            <h3>
              <span className="skimGroupDot" />
              {label}
            </h3>
            <div className="skimGroupSub">
              {cands.length} candidate{cands.length === 1 ? "" : "s"} · ★ picks the
              live quote for this topic
            </div>
            {cands.map((c) => (
              <CandidateCard key={c.id} c={c} collection={collection} />
            ))}
          </div>
        ))
      )}

      {exportOpen && (
        <div className="skimModal" onClick={() => setExportOpen(false)}>
          <div className="skimModalCard" onClick={(e) => e.stopPropagation()}>
            <header>
              <h3>Export — Markdown</h3>
              <button className="skimModalX" onClick={() => setExportOpen(false)}>
                ×
              </button>
            </header>
            <pre>{md}</pre>
            <footer>
              <button className="skimBtnSoft" onClick={() => setExportOpen(false)}>
                Close
              </button>
              <button className="skimBtnPrimary" onClick={copy}>
                {copied ? "Copied ✓" : "Copy to clipboard"}
              </button>
            </footer>
          </div>
        </div>
      )}
    </div>
  );
}

function CandidateCard({
  c,
  collection,
}: {
  c: Candidate;
  collection: ReturnType<typeof useCandidates>;
}) {
  const link = quoteDeepLink(c.source_url, c.playback_kind, c.start_time);
  return (
    <div className={`skimCand${c.starred ? " star" : ""}`}>
      <div className="skimCandTop">
        <span className="skimCandSrc">
          {c.meeting_title} · {formatMeetingDate(c.meeting_date)} ·{" "}
          {link ? (
            <a href={link} target="_blank" rel="noreferrer">
              source ↗ ({formatTime(c.start_time)})
            </a>
          ) : (
            <>at {formatTime(c.start_time)}</>
          )}
        </span>
        <span className="skimCandActs">
          <button
            className={`skimStar${c.starred ? " on" : ""}`}
            onClick={() => collection.replace(applyStar(collection.cands, c.id))}
          >
            ★ {c.starred ? "live pick" : "pick"}
          </button>
          <button className="skimDiscard" onClick={() => collection.remove(c.id)}>
            discard
          </button>
        </span>
      </div>
      <div className="skimTwoCol">
        <div>
          <div className="skimColLab">Original (verbatim)</div>
          <div className="skimOrig">{c.orig_text}</div>
        </div>
        <div>
          <div className="skimColLab">Edited quote</div>
          <textarea
            className="skimEdit"
            value={c.edit_text}
            onChange={(e) => collection.update(c.id, { edit_text: e.target.value })}
          />
        </div>
      </div>
      <div className="skimCandFoot">
        <input
          className="skimLabelIn"
          value={c.label}
          placeholder="topic label"
          onChange={(e) => collection.update(c.id, { label: e.target.value })}
        />
        <input
          className="skimNoteIn"
          value={c.note}
          placeholder="note to self — why it matters / spectrum hunch"
          onChange={(e) => collection.update(c.id, { note: e.target.value })}
        />
      </div>
    </div>
  );
}

/* ============================ Drawer ============================ */

function Drawer({
  collection,
  onOpenCuration,
}: {
  collection: ReturnType<typeof useCandidates>;
  onOpenCuration: () => void;
}) {
  const [open, setOpen] = useState(false);
  const items = collection.cands;
  return (
    <>
      <button className="skimDrawerBtn" onClick={() => setOpen(true)}>
        Grabbed <span className="skimDrawerN">{items.length}</span>
      </button>
      <div
        className={`skimOverlay${open ? " on" : ""}`}
        onClick={() => setOpen(false)}
      />
      <aside className={`skimDrawer${open ? " open" : ""}`}>
        <div className="skimDrawerHead">
          <h3>Grabbed quotes</h3>
          <button className="skimDrawerX" onClick={() => setOpen(false)}>
            ×
          </button>
        </div>
        <div className="skimDrawerList">
          {items.length === 0 ? (
            <p className="skimEmpty">Nothing grabbed yet.</p>
          ) : (
            items.map((c) => (
              <div key={c.id} className="skimGItem">
                <div className="skimGMeta">
                  <span className={`skimGLabel${c.label.trim() ? "" : " none"}`}>
                    {c.label.trim() || "no label"}
                  </span>
                  <button className="skimGRm" onClick={() => collection.remove(c.id)}>
                    remove
                  </button>
                </div>
                <p>{truncate(c.edit_text, 140)}</p>
              </div>
            ))
          )}
        </div>
        <div className="skimDrawerFoot">
          <button
            onClick={() => {
              setOpen(false);
              onOpenCuration();
            }}
          >
            Open curation →
          </button>
        </div>
      </aside>
    </>
  );
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
