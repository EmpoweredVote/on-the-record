-- TN Governor R primary (race ea27533a-f24a-4f9e-b804-cd11c34698dd)
-- Source-verification fix pass 2026-08-02 over the 11 drafts inserted 2026-08-01.
-- Every quote was re-fetched against its cited source; 2 were absent from it.
-- Drafts only -- readrank_selected is never touched here. No deletes.
-- Each statement is keyed to a single quote id.

-- ============================================================================
-- A. HIGH -- Blackburn / deportation: staged text was the VOTERS' words, reported
--    by Blackburn ("People will say, 'Hey, ...'"), presented as her own pledge,
--    and cited to WVLT where it does not appear at all. Replaced with a genuine
--    first-person commitment from her own campaign ad (user decision 2026-08-02).
-- ============================================================================
UPDATE essentials.quotes SET
  quote_text        = 'As governor, I''ll deport illegals, stop the flow of drugs and drug money.',
  deidentified_text = '…I''ll deport illegals, stop the flow of drugs and drug money.',
  source_name       = 'nashvillebanner.com',
  source_url        = 'https://nashvillebanner.com/2026/07/16/marsha-blackburn-tennessee-immigration-crime-ad/',
  editor_note       = 'From the script of Blackburn''s July 2026 "Can''t Stay" campaign ad, as reported by the Nashville Banner — a paid campaign message in her own voice, not an interview answer, and flagged here as a lower-tier source. Replaces an earlier draft that quoted Blackburn relaying what voters say ("People will say, ''Hey, ... let''s pick up the pace deporting illegal aliens''"), which was not her own stated position; the blind version drops the leading "As governor," because it identifies the speaker.',
  updated_at        = now()
WHERE id = '32980122-4f91-4ac3-b82f-745f133d0a74';

-- ============================================================================
-- B. HIGH -- Fritts / public-safety-approach: cited to WVLT (absent there); the
--    real source is WSMV 2026-07-25, and the staged text cut the load-bearing
--    final clause. Restoring it also answers the non-differentiating-goal flag.
-- ============================================================================
UPDATE essentials.quotes SET
  quote_text        = 'We should not expect our National Guardsmen to spend their entire careers in Memphis doing the job of the local law enforcement.',
  deidentified_text = 'We should not expect our National Guardsmen to spend their entire careers in Memphis doing the job of the local law enforcement.',
  source_name       = 'www.wsmv.com',
  source_url        = 'https://www.wsmv.com/2026/07/25/we-asked-gop-gubernatorial-candidates-how-theyll-handle-some-tn-voters-top-concerns-heres-what-they-said/',
  editor_note       = 'From WSMV''s July 2026 series putting the same voter-concern questions to each Republican candidate; asked about the National Guard deployment to Memphis, Fritts argues the Guard is being used as a substitute for local police rather than a stopgap. Verbatim; an earlier draft cut the closing "doing the job of the local law enforcement" and mis-cited the quote to WVLT.',
  updated_at        = now()
WHERE id = '29d84e19-69b7-4636-ad1b-b1e41fea06a5';

-- ============================================================================
-- C. MEDIUM -- Rose / public-safety-approach: restore the dropped closing clause
--    "and the willingness to do so", which carries the contestable claim.
--    Resolves the non-differentiating-goal flag.
-- ============================================================================
UPDATE essentials.quotes SET
  quote_text        = 'We''ve got to have a governor who will work hard to make sure that our local law enforcement agencies have the support and the manpower they need to confront crime and the willingness to do so.',
  deidentified_text = 'We''ve got to have a governor who will work hard to make sure that our local law enforcement agencies have the support and the manpower they need to confront crime and the willingness to do so.',
  editor_note       = 'From WKRN''s July 2026 interview on public safety and affordability, reached through a syndicated Yahoo News copy because wkrn.com blocks automated requests. Rose puts the state''s job as backstopping local police on staffing and resources while pointedly questioning their willingness to act; an earlier draft cut that closing clause, which is the part opponents could dispute.',
  updated_at        = now()
WHERE id = '8e8928c8-5144-4ff1-a2ad-efffb02d86b1';

-- ============================================================================
-- D. MEDIUM -- Blackburn / taxes: "to" had been silently substituted for the
--    spoken "and". Bracket it so the change is visibly the editor's.
-- ============================================================================
UPDATE essentials.quotes SET
  quote_text        = 'We have some of our state leaders who want to do a Tennessee DOGE. I support that. Let''s take that big red marker [to] programs that have outlived their usefulness.',
  deidentified_text = 'We have some of our state leaders who want to do a Tennessee DOGE. I support that. Let''s take that big red marker [to] programs that have outlived their usefulness.',
  editor_note       = 'From WKRN''s July 2026 affordability interview (reached via a syndicated Yahoo News copy; wkrn.com blocks automated requests), where Blackburn was asked about cutting the grocery tax and answered by putting a spending review first. Bracketed "[to]" replaces the spoken "and" to make the sentence parse — an earlier draft made that swap silently.',
  updated_at        = now()
WHERE id = '577f9663-fc08-4b10-b40d-54e4e80fb244';

-- ============================================================================
-- E. MEDIUM -- Rose / housing: the staged text ended mid-sentence and used
--    "[and]" in place of a comma. The full sentence is cleaner and fully
--    verbatim, so the bracket goes away entirely.
-- ============================================================================
UPDATE essentials.quotes SET
  quote_text        = 'We cannot allow Wall Street investment firms to buy up our housing supply, convert them to rentals, and put home ownership out of reach for average Tennesseans.',
  deidentified_text = 'We cannot allow Wall Street investment firms to buy up our housing supply, convert them to rentals, and put home ownership out of reach for average Tennesseans.',
  editor_note       = 'Rose''s written answer to the Nashville Banner''s July 2026 candidate questionnaire on housing costs — a campaign statement in text, with no video to check it against. He aims at institutional buyers rather than at permitting rules, the opposite end of the problem from Blackburn''s answer to the same question; now quoted as the full verbatim sentence, replacing a draft that stopped partway through it.',
  updated_at        = now()
WHERE id = '5da17c8d-1598-4f7b-a734-d81a8c3f9ab8';

-- ============================================================================
-- F. LOW -- editor_note accuracy and standalone-readability only.
--    No quote_text changes below this line.
-- ============================================================================

-- Fritts / deportation: note claimed a leading "So" was dropped; no "So" appears
-- in either printed source. Also no longer refers to Blackburn's replaced quote.
UPDATE essentials.quotes SET
  editor_note = 'From WVLT''s July 2026 sit-down with all three Republican candidates, asked how far the state should go on removing undocumented immigrants; Fritts states a categorical deport-everyone position. Verbatim — the following sentence ("I think that they commit the crime when they come in") is his reasoning and is left out so the quote makes a single claim.',
  updated_at  = now()
WHERE id = 'eeff0ba4-1389-43d8-8ea3-16df45645d0a';

-- Fritts / school-vouchers: note attributed a specific constitutional article he
-- never named, and called the opponents' stances "expansion" when Blackburn said
-- "continue" and Rose stated no program position.
UPDATE essentials.quotes SET
  editor_note = 'From WVLT''s July 2026 sit-down with all three Republican candidates on school choice; Fritts is the only one who would end Tennessee''s Education Freedom Scholarship voucher program, and he grounds it in a constitutional objection he does not spell out further. Verbatim, no edits.',
  updated_at  = now()
WHERE id = 'aeec06b1-5238-4ddc-8c25-3027f2e67d5a';

-- Blackburn / school-vouchers: "keep and grow" overstated "continue".
UPDATE essentials.quotes SET
  editor_note = 'From WVLT''s July 2026 sit-down with all three Republican candidates on school choice; Blackburn commits to continuing Tennessee''s voucher program on parental-choice grounds, directly against Fritts''s pledge to cancel it in the same interview. Verbatim, no edits.',
  updated_at  = now()
WHERE id = '9a65e83d-37f0-494b-8223-7734a71521aa';

-- Rose / taxes: note compared this to a Fritts grocery-tax quote that is not in
-- the batch (Fritts gave WKRN only a written statement, never quoted directly).
UPDATE essentials.quotes SET
  editor_note = 'From WKRN''s July 2026 affordability interview (reached via a syndicated Yahoo News copy; wkrn.com blocks automated requests), where Rose was asked about cutting the grocery tax. He backs a cut but sequences property-tax reform ahead of it, a different order of operations from Blackburn''s spending-review-first answer; his preceding generic "cut taxes anywhere I can" line is left out as filler, and this sentence stands on its own.',
  updated_at  = now()
WHERE id = 'f53f338a-d6ee-483a-a312-f179ef53b7dc';

-- Blackburn / housing: trim to two sentences (note-too-long finding).
UPDATE essentials.quotes SET
  editor_note = 'Blackburn''s written answer to the Nashville Banner''s July 2026 candidate questionnaire on housing costs — a campaign statement in text, with no video to check it against. Her fix is supply-side deregulation through simpler permitting rather than any public intervention. Verbatim, no edits.',
  updated_at  = now()
WHERE id = 'afe1041e-88c6-4ab0-a0a1-7f77223b406f';

-- ============================================================================
-- G. Rose / school-vouchers: DELIBERATELY NOT SELECTED (user decision 2026-08-02).
--    Row is kept as a draft -- never deleted -- but Rose is absent from this
--    topic, which ranks two-way as Blackburn-continue vs Fritts-cancel. The note
--    has to carry that decision or a later session will simply select it.
-- ============================================================================
UPDATE essentials.quotes SET
  editor_note = 'NOT FOR LIVE SELECTION — kept on file only. From WVLT''s July 2026 sit-down with all three Republican candidates on school choice. Verbatim and correctly sourced, but it reports what Tennesseans want rather than what Rose would do, and WVLT records only that he added "the question is how to facilitate those options" — his actual approach was never printed as a direct quote, so there is no position here to rank. Rose is treated as absent on school vouchers; the topic ranks between Blackburn''s pledge to continue the program and Fritts''s pledge to cancel it. Replace only with a quote stating what he would do — the NewsChannel5 "Inside Politics" episode is the likeliest source.',
  updated_at  = now()
WHERE id = 'd29d9032-0566-4a68-99de-f0c24d5e0cb6';
