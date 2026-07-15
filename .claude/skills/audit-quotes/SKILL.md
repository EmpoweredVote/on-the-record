---
name: audit-quotes
description: Audit curated quotes in essentials.quotes (ev-accounts DB) against the Read & Rank curation principles — mechanical checks plus a per-race judgment pass — and surface findings with gated, human-confirmed fixes. Use when the user wants to audit quotes, review quotes, check quotes against principles, audit the quotes in the DB, or run a quote audit.
---

# Audit Quotes

Audit already-curated quotes in `essentials.quotes` (the **ev-accounts** DB) against
`essentials/docs/QUOTE-CURATION-PRINCIPLES.md`. By default the audit sweeps **all live quotes
across all races** — narrower scopes (a candidate, a topic, explicit ids) are opt-in. It runs a
free mechanical pass, fans out a judgment pass per race, runs a portfolio (coverage-skew) pass,
and renders a consolidated report. Any fix is dry-run first and applied only after explicit user
sign-off. Pairs with `publish-quotes` (which sources and inserts new quotes) — this skill reviews
what's already there.

## Workflow

- [ ] **Read principles + catalog first.** `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` (the
      *why*) and this skill's [CHECKS.md](CHECKS.md) (the *mechanics* — findings schema, the nine
      mechanical checks, the eight judgment checks, the judgment-agent prompt template, and the
      portfolio instructions). If the two ever disagree, the principles doc wins.
- [ ] **Resolve scope + confirm.** Run `scripts/audit.py` with the user's scope (default: no
      flags, all races). It prints a `SCOPE:` line and `MECHANICAL FINDINGS: N`, and writes
      `.runs/<date>/context/<race>.json` bundles plus `mechanical_findings.json` and
      `mechanical_report.md`. Show the user both printed lines and **confirm before the judgment
      fan-out** — state roughly one subagent per race. The mechanical pass is free and read-only,
      so run it first regardless.
- [ ] **Judgment fan-out.** For each `.runs/<date>/context/<race>.json` bundle, dispatch a
      parallel `Agent`-tool subagent using the judgment-agent prompt template in CHECKS.md §4
      (fill in `{context_bundle_json}` with the bundle; the agent also needs the principles doc).
      Each subagent returns a JSON array of findings (empty array = clean for that race). Aggregate
      these with the mechanical findings.
- [ ] **Portfolio pass.** Per race, apply the CHECKS.md §5 coverage-skew instructions over that
      race's bundle (per-candidate topic coverage, compared across candidates). Append any
      `portfolio`-level `coverage-skew` findings to the aggregate.
- [ ] **Render the report.** Merge mechanical + judgment + portfolio findings and write the
      consolidated report with `scripts/report.py`'s `render(findings, scope_label)` to
      `docs/audits/<YYYY-MM-DD>-quote-audit[-<scope>].md`. Summarize inline for the user: total
      counts by severity and the top races by finding count.
- [ ] **Gated fixes, per race.** For each race with mechanical or guided fixes: build a fixes JSON
      (see op shapes below), run `scripts/apply_fixes.py fixes.json` (default dry-run — transaction
      + rollback, prints a before→after diff), show the user the diff, and re-run with `--commit`
      **only** after explicit user OK. For **guided** fixes, draft the replacement text yourself and
      confirm the exact wording with the user before building the fix op. List every
      **decision-required** finding for the user to resolve manually — never auto-apply those.

## Running the scripts

Run from the skill directory so the module path resolves; the venv lives three levels up.

```bash
cd .claude/skills/audit-quotes
../../../.venv/bin/python -m scripts.audit                              # default: all live quotes, all races
../../../.venv/bin/python -m scripts.audit --candidate "Steve Hilton" --topic housing
../../../.venv/bin/python -m scripts.audit --ids id1,id2 --include-drafts
../../../.venv/bin/python -m scripts.audit --scope-label "CA governor" --out .runs/ca-gov
```

Flags: `--race RACE_ID` (scope to one race — both candidates; needed for the portfolio pass on a
single race; find race_ids in a default run's report), `--candidate NAME`, `--topic KEY`,
`--ids id1,id2`, `--include-drafts` (drafts are excluded by default), `--out DIR` (default resolves
relative to the skill, cwd-independent), `--scope-label LABEL` (used in the rendered report heading).

Fixes file for `scripts/apply_fixes.py` (dry-run by default; `--commit` persists):

```json
[
  {"kind": "set_field", "id": "quote-uuid", "field": "editor_note", "value": "New note text."},
  {"kind": "regex_sub", "id": "quote-uuid", "field": "deidentified_text",
   "pattern": "\\.\\.\\.$", "repl": ""},
  {"kind": "set_live", "id": "quote-uuid", "value": false}
]
```

Allowed `field` values for `set_field`/`regex_sub`: `editor_note`, `deidentified_text`,
`quote_text`, `topic_key`. `set_live` toggles `readrank_selected` and takes no `field`.

```bash
../../../.venv/bin/python -m scripts.apply_fixes fixes.json            # dry-run: shows diff, rolls back
../../../.venv/bin/python -m scripts.apply_fixes fixes.json --commit   # writes for real
```

## Non-negotiables

- **Read-only until the gated fix step.** Every write goes through `apply_fixes.py`'s
  dry-run-then-explicit-OK flow — this is a production DB.
- **Never auto-apply `decision-required` findings.** Those are for the user to resolve; list them,
  don't act on them.
- **The report is the primary deliverable.** Even a run with zero applied fixes is a success if
  the consolidated report accurately surfaces what's there.
- **Only `trailing-ellipsis` is a truly mechanical auto-fix** (a regex strip). Note, de-id, and
  partisan-tell fixes are **guided**: draft the replacement text, confirm wording with the user,
  then apply — never rewrite and commit in the same step.
