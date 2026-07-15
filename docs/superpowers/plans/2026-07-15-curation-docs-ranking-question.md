# Curation Docs & Skills for the Ranking Question — Implementation Plan (Part C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the quote-curation principles and the `publish-quotes` / `audit-quotes` skills so they are correct and non-contradictory about the new per-race **ranking question** (the override introduced in ev-accounts#82).

**Architecture:** Mostly documentation. One small code change: the `audit-quotes` context builder must resolve the ranking question (`override ?? Compass`) so the `off-question` gate is measured against what citizens actually rank against, and a new `question-override` check can compare the Compass question to the override for axis-drift.

**Tech Stack:** Markdown docs; Python (psycopg2) for the audit scripts; the docs live in two repos.

**Scope note:** Part C of `read-rank/docs/superpowers/specs/2026-07-15-bespoke-race-topic-questions-design.md`. Part B shipped in read-rank#70; Parts A+D in ev-accounts#82. This plan depends on the ev-accounts table `essentials.readrank_race_topic_questions` (already live in prod). on-the-record work is done in an isolated worktree off origin/main.

**The model to encode (from the spec §2.2–2.4):**
- **Two questions.** The **Compass question** is canonical, tied to the axis, global — untouched. The **ranking question** is what Read & Rank shows and gates responsiveness against: `ranking question = per-race override ?? Compass question`.
- **Responsiveness gates against the resolved ranking question.** "Same question" = same as the other quotes ranked together (one race-topic), not identical to the Compass string.
- **Axis-invariant.** An override reframes wording/localizes only; it must engage the same Compass axis. Axis-wrong ⇒ Compass fix or re-home, never an override.
- **Override vs. escalate.** Race-local mismatch ⇒ override. Systemic mismatch ⇒ escalate to `compass-topic-builder` (the existing §7.1 remedy).

---

## File Structure

- **Modify (essentials):** `docs/QUOTE-CURATION-PRINCIPLES.md` — §7.1 (two questions + override-vs-escalate), §7.2 (scope of "same question"), new §7.3 (race-local ranking questions).
- **Modify (on-the-record):** `.claude/skills/publish-quotes/SKILL.md` — new race-level "confirm/sharpen the ranking question" step; off-question wording → resolved question.
- **Modify (on-the-record):** `.claude/skills/publish-quotes/REFERENCE.md` — document `essentials.readrank_race_topic_questions`.
- **Modify (on-the-record):** `.claude/skills/audit-quotes/scripts/db.py` — `fetch_stance` resolves the override (adds `race_id`; returns compass + resolved + flag).
- **Modify (on-the-record):** `.claude/skills/audit-quotes/scripts/audit.py` — race-scoped stance cache; pass `race_id`.
- **Modify (on-the-record):** `.claude/skills/audit-quotes/CHECKS.md` — off-question → resolved question; new `question-override` check; new stance fields.
- **Modify (on-the-record):** `.claude/skills/audit-quotes/SKILL.md` — reflect the new check in the checks list.
- **Create (on-the-record):** `.claude/skills/audit-quotes/tests/test_fetch_stance.py` — asserts the resolve query shape.

---

## Task 1: Principles — the two questions, override-vs-escalate, §7.3

**File:** `essentials/docs/QUOTE-CURATION-PRINCIPLES.md`  (essentials repo, branch `feat/readrank-ranking-question-principles`)

- [ ] **Step 1: Add "the two questions" after the §7.1 opening**

Find (the §7.1 opener, ending with the gate line):

```
**After the gate:** among quotes that clear responsiveness, prefer the one that shows the HOW, not
just an agreeable goal no one would contest (§4.6).
```

Insert immediately **before** that paragraph:

```
**The two questions.** Two questions hide behind "the topic's framed question," and the per-race
override (below, §7.3) splits them apart. The **Compass question** is canonical, tied to the
topic's axis, and global — it is what people answer when they take their Compass, and curation
never changes it here. The **ranking question** is what Read & Rank *displays* and what
responsiveness is gated against: `ranking question = per-race override ?? Compass question`. When no
override exists the two are identical (today's default). **Responsiveness (this section) is measured
against the resolved ranking question.** "Same question" means *the quotes ranked together answer
the same question as each other* within one race-topic — not that the string is identical to the
Compass question or the same across races.
```

- [ ] **Step 2: Add override-vs-escalate to the cross-team signal**

Find:

```
*Cross-team signal:* if a major, common position (prevention, Housing First) keeps landing
off-question for a topic, the **topic's framing may be too narrow** — feed that back to
`compass-topic-builder`. The mismatch is a signal, like the in-tension flag below.
```

Replace with:

```
*Cross-team signal:* if a major, common position (prevention, Housing First) keeps landing
off-question for a topic, the framing is mismatched. Two remedies, by scope: if the debate framed
*this race's* topic differently but the Compass question is fine elsewhere, write a **race-local
ranking-question override** (§7.3); if the Compass question is **systemically** wrong, feed that
back to `compass-topic-builder` to fix the topic globally. An override is never a substitute for
fixing a globally-broken Compass question. The mismatch is a signal, like the in-tension flag below.
```

- [ ] **Step 3: Clarify "same question" scope in §7.2**

Find (the last line of §7.2):

```
So: *same topics, not necessarily the same axes — but always the same question.*
```

Replace with:

```
So: *same topics, not necessarily the same axes — but always the same ranking question among the
quotes ranked together.* (Across races the ranking question may differ via a §7.3 override; within a
race-topic all ranked quotes answer the same one. The override never changes the Compass **value** or
axis — see §7.3 — so the coupling in this section is unaffected.)
```

- [ ] **Step 4: Add §7.3**

Find the start of §8:

```
## 8. Topic-portfolio balance
```

Insert immediately **before** it:

```
### 7.3 Race-local ranking questions (the override)

A curator may give a race-topic its own **ranking question** — the question Read & Rank shows and
gates responsiveness against — stored per `(race_id, topic_key)` and resolved as
`override ?? Compass question`. Use it when the debate or interview framed the topic in a
race-specific way the generic Compass question misses (e.g. a national "What role should fossil
fuels play?" vs. a California debate's "How should California balance environmental regulations with
the cost of gas?").

An override **may** re-word, localize, add debate context, or shorten. It **must**:

- **Stay on the same axis.** It engages the *same* Compass axis/dimension as the topic. If the
  race's real question is on a different axis, that is a Compass fix or a re-home (§7.1), **not** an
  override — an axis-shifting override silently breaks the §7.2 coupling and the "same topics"
  guarantee.
- **Stay blind.** It is shown identically to every candidate and must not name or contextually leak
  a candidate (§4.2). "California" (the race) is fine; "the former mayor's plan" is not.
- **Derive from the real question.** Prefer the actual debate/interview question, tightened for
  clarity, over an invented one. Record the source.

Because it is axis-invariant, "answers the ranking question" still implies "is evidence on the
Compass axis," so responsiveness (§7.1) and coupling (§7.2) both continue to hold. The override is a
**Read & Rank ranking-question concern only** — anywhere the Compass question is surfaced (Compass,
Essentials) still shows the canonical Compass question.
```

- [ ] **Step 5: Commit (essentials repo)**

```bash
# from the essentials repo root
git add docs/QUOTE-CURATION-PRINCIPLES.md
git commit -m "docs(curation): the two questions + race-local ranking-question override (§7.1–7.3)"
```

---

## Task 2: publish-quotes skill — authoring the override

**Files:** `on-the-record/.claude/skills/publish-quotes/SKILL.md`, `.../REFERENCE.md`  (worktree)

- [ ] **Step 1: Point the off-question step at the resolved question and add the override step**

In `SKILL.md`, find the end of the "Pick the topic_key" bullet:

```
      A candidate who only spoke in record/attacks (no forward position) is **absent** — don't
      launder record into a pseudo-position. See `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` §7.1.
      Among on-question candidates, prefer the quote that shows *how* the candidate would act, not
      just an agreeable goal no one would contest (`QUOTE-CURATION-PRINCIPLES.md` §4.6).
```

Replace with:

```
      A candidate who only spoke in record/attacks (no forward position) is **absent** — don't
      launder record into a pseudo-position. See `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` §7.1.
      Among on-question candidates, prefer the quote that shows *how* the candidate would act, not
      just an agreeable goal no one would contest (`QUOTE-CURATION-PRINCIPLES.md` §4.6).
      Responsiveness is judged against the topic's **ranking question** — the per-race override if
      one exists, else the Compass question (`QUOTE-CURATION-PRINCIPLES.md` §7.1 "the two questions").
- [ ] **Confirm the ranking question for this race-topic.** Check whether the Compass
      `question_text` actually fits how *this* race framed the topic. If the debate/interview asked a
      race-specific question the Compass one misses, set a per-race override in
      `essentials.readrank_race_topic_questions` (`(race_id, topic_key)` → `question_text`): derive
      it from the real question, tighten for clarity, keep it **on the same Compass axis** and
      **blind** (§7.3). If the Compass question is systemically wrong (not just race-specific),
      escalate to `compass-topic-builder` instead — don't override. Leave it unset to fall back to
      the Compass question.
```

- [ ] **Step 2: Document the override table in REFERENCE.md**

In `REFERENCE.md`, find the end of the `essentials.quotes` schema table:

```
| `readrank_selected` | boolean, NOT NULL default false — the "live" switch |
| `created_at` / `updated_at` | timestamptz |
```

Insert immediately **after** that table block (before "## The two flags that matter"):

```

## `essentials.readrank_race_topic_questions` — per-race ranking question

The **ranking question** Read & Rank shows for a topic is `COALESCE(override, inform.compass_topics.question_text)`.
The override lives here (one row per `(race_id, topic_key)`); leave it unset to use the Compass question.

| column | notes |
|---|---|
| `race_id` | uuid → `essentials.races` (NOT NULL), part of PK |
| `topic_key` | text, lowercase (`CHECK topic_key = lower(topic_key)`), part of PK |
| `question_text` | text, NOT NULL, non-empty — the race-local ranking question |
| `updated_at` / `updated_by` | timestamptz / text provenance |

Axis-invariant by policy (`QUOTE-CURATION-PRINCIPLES.md` §7.3): the override reframes wording only,
never the topic/axis. It's a Read & Rank concern — Compass/Essentials still show the Compass question.
```

- [ ] **Step 3: Commit (worktree)**

```bash
git add .claude/skills/publish-quotes/SKILL.md .claude/skills/publish-quotes/REFERENCE.md
git commit -m "docs(publish-quotes): author + document the per-race ranking-question override"
```

---

## Task 3: audit-quotes — resolve the ranking question (code, TDD)

**Files:** `.claude/skills/audit-quotes/scripts/db.py`, `.../scripts/audit.py`, `.../tests/test_fetch_stance.py`  (worktree)

- [ ] **Step 1: Write the failing test**

Create `.claude/skills/audit-quotes/tests/test_fetch_stance.py`:

```python
"""fetch_stance must resolve the per-race ranking question (override ?? compass)."""
from scripts.db import fetch_stance


class _Cur:
    def __init__(self, row):
        self._row = row
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.executed = (sql, params)

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, row):
        self.cur = _Cur(row)

    def cursor(self, **kw):
        return self.cur


def test_fetch_stance_resolves_override_and_passes_race_id():
    row = {
        "question_text": "OVERRIDE Q",
        "compass_question_text": "COMPASS Q",
        "override_active": True,
        "value": 3.0,
        "chairs": [],
    }
    conn = _Conn(row)
    stance = fetch_stance(conn, "pol-1", "fossil-fuels", race_id="race-1")

    # The resolved ranking question is what the audit gates against.
    assert stance["question_text"] == "OVERRIDE Q"
    assert stance["compass_question_text"] == "COMPASS Q"
    assert stance["override_active"] is True

    sql, params = conn.cur.executed
    assert "readrank_race_topic_questions" in sql
    assert "race-1" in params
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd .claude/skills/audit-quotes && python -m pytest tests/test_fetch_stance.py -q`
Expected: FAIL — current `fetch_stance` has no `race_id` param and returns no `override_active`/`compass_question_text`.

- [ ] **Step 3: Update `fetch_stance` in `db.py`**

Replace the whole function:

```python
def fetch_stance(conn, politician_id, topic_key):
    """Returns {'question_text':..., 'value': float|None, 'chairs': [{'v','text'}...]} or None.
    Keyed on politician_id (not full_name) — names collide across the national race set."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
          SELECT t.question_text,
                 (SELECT a.value FROM inform.politician_answers a
                  WHERE a.topic_id=t.id AND a.politician_id=%s::uuid) AS value,
                 (SELECT json_agg(json_build_object('v', s.value, 'text', s.text) ORDER BY s.value)
                  FROM inform.compass_stances s WHERE s.topic_id=t.id) AS chairs
          FROM inform.compass_topics t WHERE t.topic_key=%s
        """, (politician_id, topic_key))
        row = cur.fetchone()
        return dict(row) if row else None
```

with:

```python
def fetch_stance(conn, politician_id, topic_key, race_id=None):
    """Returns the candidate+topic stance, or None.

    `question_text` is the RESOLVED ranking question (per-race override ?? Compass), which is what
    Read & Rank gates responsiveness against. `compass_question_text` is the canonical Compass
    question and `override_active` says whether an override applied — both let the audit check an
    override for axis-drift. Keyed on politician_id (names collide across the national race set)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
          SELECT t.question_text AS compass_question_text,
                 COALESCE(rtq.question_text, t.question_text) AS question_text,
                 (rtq.question_text IS NOT NULL) AS override_active,
                 (SELECT a.value FROM inform.politician_answers a
                  WHERE a.topic_id=t.id AND a.politician_id=%s::uuid) AS value,
                 (SELECT json_agg(json_build_object('v', s.value, 'text', s.text) ORDER BY s.value)
                  FROM inform.compass_stances s WHERE s.topic_id=t.id) AS chairs
          FROM inform.compass_topics t
          LEFT JOIN essentials.readrank_race_topic_questions rtq
            ON rtq.race_id = %s::uuid AND rtq.topic_key = t.topic_key
          WHERE t.topic_key=%s
        """, (politician_id, race_id, topic_key))
        row = cur.fetchone()
        return dict(row) if row else None
```

- [ ] **Step 4: Race-scope the stance cache in `audit.py`**

Find the cache-key line and the fetch_stance call (currently keyed on `(politician_id, topic_key)`):

```python
            if key not in stance_cache:
                stance_cache[key] = fetch_stance(conn, r["politician_id"], r["topic_key"])
```

Replace with (key now includes race_id so the same candidate in different races resolves the right override):

```python
            key = (r["race_id"], r["politician_id"], r["topic_key"])
            if key not in stance_cache:
                stance_cache[key] = fetch_stance(
                    conn, r["politician_id"], r["topic_key"], race_id=r["race_id"]
                )
```

If there is an earlier `key = (...)` assignment above this block that keys on `(politician_id, topic_key)`, remove it (the new 3-tuple assignment above replaces it) — there must be exactly one `key = ...` definition feeding `stance_cache`.

- [ ] **Step 5: Run the test + the existing suite**

Run: `cd .claude/skills/audit-quotes && python -m pytest tests/ -q`
Expected: PASS (new `test_fetch_stance.py` + existing `test_apply_fixes.py`).

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/audit-quotes/scripts/db.py .claude/skills/audit-quotes/scripts/audit.py .claude/skills/audit-quotes/tests/test_fetch_stance.py
git commit -m "feat(audit-quotes): resolve per-race ranking question for the off-question gate"
```

---

## Task 4: audit-quotes — CHECKS.md + SKILL.md (resolved question + new check)

**Files:** `.claude/skills/audit-quotes/CHECKS.md`, `.../SKILL.md`  (worktree)

- [ ] **Step 1: Reword the `off-question` check row (CHECKS.md §3 table)**

Find:

```
| `off-question` | The quote doesn't genuinely answer the topic's framed `question_text` — it touches the subject but engages a different axis, or answers an adjacent question entirely. Comparability is the precondition for a valid ranking; this is a gate, not a preference. | high | decision-required |
```

Replace with:

```
| `off-question` | The quote doesn't genuinely answer the topic's **ranking question** (`stance.question_text` — the per-race override if one exists, else the Compass question) — it touches the subject but engages a different axis, or answers an adjacent question entirely. Comparability is the precondition for a valid ranking; this is a gate, not a preference. | high | decision-required |
```

- [ ] **Step 2: Add the `question-override` check row (CHECKS.md §3 table)**

Immediately after the `off-question` row, add:

```
| `question-override` | A per-race ranking-question override (`stance.override_active` is true) has drifted from its Compass topic: it shifts the **axis/dimension** away from `stance.compass_question_text` (should be a Compass fix or re-home, not an override), or it names/leaks a candidate (not blind), or it is not derived from the race's actual question. Axis-invariance is what keeps responsiveness and coupling valid (QUOTE-CURATION-PRINCIPLES §7.3). | high | decision-required |
```

- [ ] **Step 3: Update the stance-field description (CHECKS.md §4 prompt template)**

Find:

```
  - `stance` — `{question_text, value, chairs}` for this candidate+topic: the topic's
    framed question, the candidate's numeric Compass value on this topic's spectrum
    (may be null), and `chairs` (the spectrum's labeled anchor points, roughly 1-5,
    from one pole to the other)
```

Replace with:

```
  - `stance` — `{question_text, compass_question_text, override_active, value, chairs}` for this
    candidate+topic. `question_text` is the **ranking question** you gate responsiveness against
    (the per-race override if `override_active`, else the Compass question). `compass_question_text`
    is the canonical Compass question. `value` is the candidate's numeric Compass value on this
    topic's spectrum (may be null), and `chairs` are the spectrum's labeled anchor points
    (roughly 1-5, from one pole to the other)
```

- [ ] **Step 4: Add the override rule + task line to the §4 prompt**

Find (in the "## The rules" list of the §4 prompt):

```
- **Responsiveness — a hard gate, not a preference.** The quote must genuinely answer the
  topic's framed `question_text` — engage the axis/dimension the question sets, not merely
  touch the subject. If it answers a different question (even a related one), it is not a
  valid comparison point for this topic, no matter how well-written or distinctive it is.
```

Replace with:

```
- **Responsiveness — a hard gate, not a preference.** The quote must genuinely answer the
  topic's **ranking question** (`stance.question_text` — the per-race override if
  `stance.override_active`, else the Compass question) — engage the axis/dimension it sets, not
  merely touch the subject. If it answers a different question (even a related one), it is not a
  valid comparison point for this topic, no matter how well-written or distinctive it is.
- **Override must stay on-axis.** When `stance.override_active` is true, the override
  (`stance.question_text`) must engage the *same* axis as `stance.compass_question_text`, be blind
  (name no candidate), and read as the race's real question tightened — not a different question.
  If it shifts the axis, flag `question-override` (that's a Compass fix or re-home, not an override).
```

- [ ] **Step 5: Add `question-override` to the task list (CHECKS.md "Your task")**

Find:

```
- `off-question` — doesn't answer the topic's framed question (severity high, decision-required)
```

Replace with:

```
- `off-question` — doesn't answer the topic's ranking question (override ?? Compass) (severity high, decision-required)
- `question-override` — an active override shifts the axis / isn't blind / isn't the race's real question (severity high, decision-required)
```

- [ ] **Step 6: Mirror the new check in SKILL.md**

In `.claude/skills/audit-quotes/SKILL.md`, first read it to find where checks are enumerated. If SKILL.md lists the judgment checks (by id or with the `off-question` line), add a `question-override` entry alongside, worded as in Step 5. If SKILL.md only defers to CHECKS.md for the check list, make no change and note that in the commit body.

- [ ] **Step 7: Commit**

```bash
git add .claude/skills/audit-quotes/CHECKS.md .claude/skills/audit-quotes/SKILL.md
git commit -m "docs(audit-quotes): gate off-question on the ranking question; add question-override check"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** two-questions (T1 §7.1) ✓; responsiveness → resolved question (T1, T2, T3, T4) ✓; axis-invariance + override-vs-escalate (T1 §7.1/§7.3) ✓; blindness of override (T1 §7.3, T4 check) ✓; publish-quotes authors the override (T2) ✓; REFERENCE documents the table (T2) ✓; audit resolves + checks the override (T3 code, T4 check) ✓; "same question" scope clarified (T1 §7.2) ✓.
- **Placeholders:** none — every edit shows exact old/new text or complete code; Step 6 of Task 4 is conditional on reading SKILL.md but specifies both branches.
- **Consistency:** field names `question_text` (resolved), `compass_question_text`, `override_active` match across `db.py`, the test, CHECKS.md §4, and the checks. Check id `question-override` matches across CHECKS.md table, §4 prompt, task list, and SKILL.md. Table name `essentials.readrank_race_topic_questions` matches ev-accounts#82.
- **Cross-repo:** Task 1 commits in **essentials**; Tasks 2–4 commit in **on-the-record** (worktree). Two branches/PRs.
