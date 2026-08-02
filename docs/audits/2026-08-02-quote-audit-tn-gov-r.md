# Quote Audit — TN Gov R final (post-fix)

**5 findings** — 0 high, 5 medium, 0 low

## Summary by race
- **race ea27533a-f24a-4f9e-b804-cd11c34698dd** — 5 findings (0 high, 5 med, 0 low)

## race ea27533a-f24a-4f9e-b804-cd11c34698dd
- `medium` · `decision-required` · **not-rankable** (topic) —  / deportation [deportation]
    - Only 0 candidate(s) live on this topic; not a valid head-to-head.
    - fix: Source a second candidate's on-question quote, or drop the topic from the race.
- `medium` · `decision-required` · **not-rankable** (topic) —  / housing [housing]
    - Only 0 candidate(s) live on this topic; not a valid head-to-head.
    - fix: Source a second candidate's on-question quote, or drop the topic from the race.
- `medium` · `decision-required` · **not-rankable** (topic) —  / public-safety-approach [public-safety-approach]
    - Only 0 candidate(s) live on this topic; not a valid head-to-head.
    - fix: Source a second candidate's on-question quote, or drop the topic from the race.
- `medium` · `decision-required` · **not-rankable** (topic) —  / school-vouchers [school-vouchers]
    - Only 0 candidate(s) live on this topic; not a valid head-to-head.
    - fix: Source a second candidate's on-question quote, or drop the topic from the race.
- `medium` · `decision-required` · **not-rankable** (topic) —  / taxes [taxes]
    - Only 0 candidate(s) live on this topic; not a valid head-to-head.
    - fix: Source a second candidate's on-question quote, or drop the topic from the race.


## Judgment pass (CHECKS.md §3) — 0 findings

Run in-context over the full 11-quote bundle for this race. All nine judgment checks applied to
every quote; nothing flagged. Notable resolutions from the 2026-08-01 run:

- `non-differentiating-goal` on **Fritts / public-safety-approach** (`29d84e19`) — **resolved.**
  Restoring the cut clause ("doing the job of the local law enforcement") supplies the mechanism:
  the Guard should not be a substitute for local police. Directional and contestable.
- `non-differentiating-goal` on **Rose / public-safety-approach** (`8e8928c8`) — **resolved.**
  Restoring "to confront crime and the willingness to do so" supplies the contested claim that
  local agencies currently lack the will to act.
- `non-differentiating-goal` on **Rose / school-vouchers** (`d29d9032`) — **resolved by curator
  decision.** The quote is a popularity claim, not Rose's own commitment; he is treated as absent
  on the topic. The row is retained as a draft carrying a `NOT FOR LIVE SELECTION` note so a later
  session does not select it. School vouchers ranks two-way instead.

Verified clean on every quote: forward-looking operative clause; on-question against the resolved
ranking question (no per-race overrides are set for this race, so all five use the Compass
question); policy/institution critique only, no personal attacks; `deidentified_text` present and
honestly marked on all 11 rows; verbatim sentences from every source, no curator summaries.

**Coupling.** Three quotes have a synthesized Compass value to check against: Blackburn on
deportation (5.0, "move quickly to deport all") and school vouchers (5.0, universal vouchers) and
taxes (4.0, cut taxes and scale back services). All three are reinforcing — none in tension. Her
vouchers quote ("continue with school choice") is directionally consistent with but milder than a
5.0; that is a difference of intensity, not direction, so no `coupling-in-tension` finding.

### Watch item, not a finding

**Blackburn / taxes** (`577f9663`) contains "Tennessee DOGE", which is a partisan side tell. It is
harmless in an all-Republican primary — it cannot reveal which side is speaking when every
candidate is on the same side — but if this quote is ever carried into the general election, the
blind card will need it depersonalized.

## Portfolio pass (CHECKS.md §5) — 0 findings

Selectable topic coverage: **Blackburn 4/5** (deportation, housing, school-vouchers, taxes),
**Rose 3/5** (housing, public-safety-approach, taxes), **Fritts 3/5** (deportation,
public-safety-approach, school-vouchers). Roughly comparable — no `coverage-skew` finding.

One effort signal worth recording rather than flagging: Fritts is absent from **taxes** despite it
being his signature issue, purely because WKRN published only a paraphrase of his grocery-tax
answer and never a direct quotation. That is a source-availability gap, not an unevenly applied
curation pass, and the underlying video is already on the ingest shortlist.

## Standing caveat on this report

The mechanical pass does not re-fetch `source_url`. Every quote in this race was re-fetched and
string-matched **by hand** on 2026-08-02, which is how the two source failures were found; a future
run of this report on other races carries no such guarantee until the `--verify-sources` check
lands.
