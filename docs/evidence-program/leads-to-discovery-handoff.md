# Handoff: leads → discovery (paste into the "Discovery queue review workflow" session)

Paste the block below into that session to bring it up to date and tee up the wire.

---

**Update from the evidence-program session (2026-09-21).**

on-the-record is becoming a **Sources & Evidence layer** — a citation library ("Zotero for stances") of
each politician's own words per issue, in `inform.evidence_items`. Read & Rank and Compass become
**views** over it. Full map: `docs/evidence-program/evidence-loop-architecture.md`.

Shipped so far: the evidence extract→verify→cross-check→judge pipeline (`src/evidence/`, PRs #246/#247/
#249/#250), the review surface (ev-accounts #569), and — in progress on branch
`feat/evidence-transcript-lane` — a lane that reads our **ingested `meetings.*` transcripts** as
evidence (own-words, click-to-seek deep links).

**The wire I want from discovery — chase-the-primary:**
The evidence pipeline emits **leads**: `{politician_id, reported_text, issue, event, secondary_url,
primary_handle}` — i.e. "a news/web source says the candidate said X at a debate/podcast/interview
(event), and here's the primary link if given." I'd like these leads to **land in the discovery review
queue as "find this primary" seeds**, so discovery's existing classify → `route=ingest` machinery pulls
the primary in; once it's ingested into `meetings.*`, the evidence transcript lane extracts the verbatim
primary. That closes the loop: news quote → lead → discovery → ingest → evidence → maybe a new lead.

**What the discovery side would need:**
- An intake path that accepts an evidence lead as a discovery seed/search (candidate + event + optional
  primary_handle), deduped against already-discovered sources.
- The **human review queue stays the brake** — leads queue for approval, they don't auto-chase forever.

**Coupling note:** this depends on the discovery queue's input schema, which you're actively evolving —
so treat it as **design-coupled**: let's agree the lead-intake shape before either side builds it.

**One small evidence-side change that would help:** today most leads lack `event` (news paraphrases
rather than attributes a spoken event). The extractor can be nudged to populate `reported_event` when a
source attributes a quote to a named spoken event — making leads actionable. I'll handle that on the
evidence side when we wire this up.

---

*(This note is saved at `docs/evidence-program/leads-to-discovery-handoff.md`. The build is deferred
until the discovery input schema settles; nothing to do here until we coordinate.)*
