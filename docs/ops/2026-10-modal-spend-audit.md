# Modal spend audit, 2026-10-07

Source: ev-cto watchlist #83. Read-only audit. Nothing was changed, rotated, or deleted.

## 1. Spend

Workspace `chrisandrewsedu`, app `councilscribe-bench` (the only app with cost). Data from `modal billing report`.

| Period | Cost | Notes |
|---|---|---|
| Sept 2026 (last month) | **$7.32** | 31 app runs, 6 active days. Peaks: 09-26 $2.95, 09-19 $2.27 |
| Oct 1-6, 2026 | $1.05 | 15 app runs |

Expected cost is $0.35-$0.70 per 3-4 hour meeting (bench/FINDINGS.md). September's cost is about 10-20 meetings of that size. This is **inside the expected range**. No runaway usage found.

The billing report does not show GPU type or per-run duration. The report covers compute only. It does not show volume storage cost.

## 2. Token in GitHub secrets

- `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` exist as repo secrets (set 2026-09-12). GitHub never shows secret values, so the token's identity cannot be read from here.
- The Modal CLI has no command to list tokens. Only the dashboard shows them.
- Modal tokens belong to a user, not to a workspace. A token has the access of its user in every workspace that user can reach. Modal has no narrower scope.
- This Mac has one profile and one workspace (`chrisandrewsedu`). Its token was created 2026-05-13. It is a personal token, not CI-only.
- **Unverified:** whether the GitHub secret is a separate CI token or a copy of the personal token. If it is a copy, a leak would expose the whole user account.

Action for the founder: Dashboard -> Settings -> API Tokens. Check that a token for CI exists, with its own name and a creation date near 2026-09-12. If not, create a new token named `github-ci-otr`, update the two GitHub secrets, then delete the old token. Needs explicit approval first.

## 3. Spend limit

Not verifiable from the CLI or API. The CLI has no billing-limit command. A dashboard login is needed. Treat as **no limit set** until the founder confirms.

Proposed limit: **$30 per month** (about 4x September, about 40+ typical meetings). The founder reports Modal gives $30 of free credit each month, so September's $7.32 was likely covered in full and a $30 limit means no out-of-pocket spend. Revisit when coverage grows.

Dashboard steps for the founder:
1. Open https://modal.com/settings and choose the workspace `chrisandrewsedu`.
2. Open **Usage and Billing**.
3. Find **Workspace Spend Limit** (or **Billing limit**).
4. Enter `30` USD. Save.
5. Optionally set an alert at $15.
6. Take a screenshot and send it to the CTO advisor.

## 4. Volume `councilscribe-bench`

Top-level directories: `meetings`, `results`, `cache`, `vibevoice`, `vibevoice-gpu-sweep`.

`meetings/` holds 187 directories, about 22 GiB (`audio.wav`, sometimes `source.m4v`). The pipeline re-uploads audio on every run (`upload_audio` in `src/modal_compute.py`), so the volume is a staging area. Deleting a directory does not break a later re-run. It only costs one re-upload.

Matched against `meetings.meetings.slug` in prod (read-only query):

| Group | Dirs | Size | Meaning |
|---|---|---|---|
| status `published` | 143 | 13.3 GiB | Safe to delete. |
| status `draft` | 13 | 5.5 GiB | All `*-house-floor` (2026-09-02 to 2026-10-01). Awaiting admin review. Keep until promoted. |
| not in DB | 31 | 3.2 GiB | Unpublished, or published under another slug. Check each one first. Includes `latest-council` and four `house-floor-tuning` dirs. |

Not in DB (31): mostly interviews, podcasts and candidate forums from 2026-01 to 2026-08, plus `2019-07-11-house-floor-ndaa`, the three MI governor debates and `latest-council`. A name mismatch (slug renamed or UUID-based) would explain some. Others may be work that was never published.

Other directories: `results/` has 4 council entries. `cache/huggingface` is the model cache and should stay. `vibevoice` and `vibevoice-gpu-sweep` were not measured. They look like old benchmark data.

Cleanup done 2026-10-07 with founder approval: deleted the 143 published directories (13.3 GiB). Each had a local audio copy in `~/CouncilScribe/meetings/`. The volume now holds 44 meeting directories (13 drafts, 31 unmatched). The 31 unmatched directories were traced (none matches a DB row by video id):
- Deleted 2026-10-07, bench scratch (1.6 GiB): four `*-house-floor-tuning`, `latest-council`, `2019-07-11-house-floor-ndaa`.
- 17 processed locally but not published (1.1 GiB). Local `audio.opus` exists, so the volume copy is redundant. Whether to publish them is a separate decision.
- 8 with no local dir (0.6 GiB). Five are stale pre-rename duplicates: audio length matches a published, renamed local meeting exactly (`2026-04-01-interview`, `2026-04-09-podcast`, `2026-04-20-california-governor-…`, `2026-05-05-la-mayoral-debate-(sherman-oaks,-may)`, `2026-07-08-debate-mi-governor-dem-primary`). Three have no local match: `2026-03-27-house-floor-proceedings` (15 min) and two `2026-07-10-debate-mi-governor-gop-primary*` clips (2-3 min, likely test snippets).
- The 5 stale duplicates were deleted 2026-10-07 (volume now 33 meeting dirs: 13 drafts, 17 unpublished, 3 unmatched clips).
- Drafts (13) stay until promoted. Storage cost is small, so this is housekeeping, not urgent.

## 5. Open items

- [ ] Founder: confirm CI token identity (section 2).
- [ ] Founder: set spend limit (section 3).
- [x] Published-directory cleanup done (section 4).
- [x] Unmatched directories traced; group A deleted.
- [ ] Decide on the 3 unmatched clips, and the 17 unpublished meetings (section 4).
