# Deid-dishonest triage — review before applying

80 quotes → **61 fix** (redraft blind text), **19 remove** (quote_text is itself a summary/fragment).

## FIX — redraft blind text (`deidentified_text`)

### Mike Braun · redistricting · `b2478868-61c2-45e9-96f9-20a30eee41bb`
- quote_text: "Indiana Republicans drew maps that reflect this state's conservative values and the will of Hoosier voters. The process was transparent, legal, and resulted in fair representation for Indiana communities."
- OLD blind: "[Indiana lawmakers] drew maps that reflect this state's conservative values and the will of Hoosier voters. The process was transparent, legal, and resulted in fair representation for Indiana communities."
- **NEW blind: "[Lawmakers here] drew maps that reflect this state's conservative values and the will of … voters. The process was transparent, legal, and resulted in fair representation for … communities."**
- why: Verbatim first-person defense of the maps. Removed 'Indiana Republicans' self-ID/state tell to '[Lawmakers here]', dropped 'Hoosier' and 'Indiana' state tells with ellipsis, keeping the substance (transparent, legal, fair) intact.

### Kathy Hochul · abortion · `05e1025a-7ed7-426c-a8fa-4cff1d8ac6fe`
- quote_text: '"Overturning Roe changed nothing in NY, because I\'m governor"'
- OLD blind: "Overturning Roe changed nothing in this state, because I'm governor"
- **NEW blind: 'Overturning Roe changed nothing in [this state].'**
- why: Verbatim first-person statement. 'NY' neutralized to '[this state]'; the office self-ID 'I'm governor' is an identity leak and is dropped with ellipsis. The remaining claim (overturning Roe changed nothing here) is preserved.

### Kay Ivey · abortion · `173bdf1f-30cc-4ea1-bd74-ae2c6988d29c`
- quote_text: 'I could not be more proud as a governor, a Christian and a woman to see this misguided and detrimental decision overturned'
- OLD blind: 'I could not be more proud as a governor, a Christian and a woman to see this misguided and detrimental decision overturned'
- **NEW blind: 'I could not be more proud … to see this misguided and detrimental decision overturned'**
- why: Verbatim first-person statement. The office/personal self-ID 'as a governor, a Christian and a woman' is an identity leak (and 'as a governor' discloses the role); dropped with ellipsis. Position (pride at the decision being overturned) unchanged.

### Mike Dunleavy · abortion · `b8d1edfb-86fa-4bf5-862c-95e5e3a0b74c`
- quote_text: "I'm a pro-life Governor, and my administration is ready to work with all of you over the next four years to achieve my goal to make Alaska the most pro-life state."
- OLD blind: "I'm a pro-life Governor, and my administration is ready to work with all of you over the next four years to achieve my goal to make this the most pro-life state."
- **NEW blind: "I'm a pro-life [official], and my administration is ready to work with all of you over the next four years to achieve my goal to make [this] the most pro-life state."**
- why: Verbatim first-person statement. 'Governor' neutralized to '[official]' (retaining 'my administration' still shows executive but office title is the direct self-ID leak); 'Alaska' already masked to '[this]'. Substance (goal to make the state the most pro-life) preserved.

### Matthew D. Klein · climate-change · `86220544-2441-4853-9dac-f2a5b803a1ac`
- quote_text: 'I know climate change is a crisis, and we have the means to address it now. As a member of the Senate Energy Committee, I supported our commitment to use 100% clean electricity sources in Minnesota by 2040.'
- OLD blind: 'I know climate change is a crisis, and we have the means to address it now. As a member of the state Senate Energy Committee, I supported a commitment to use 100% clean electricity sources by 2040.'
- **NEW blind: 'I know climate change is a crisis, and we have the means to address it now. … I supported [a] commitment to use 100% clean electricity sources … by 2040.'**
- why: Verbatim first-person statement. Removed the role self-ID 'As a member of the [state] Senate Energy Committee' with ellipsis and dropped the 'Minnesota' state tell; kept the position (support for 100% clean electricity by 2040) verbatim.

### Steve Marshall · abortion · `86a133bb-a3a8-44cf-a79a-77540410ffff`
- quote_text: "Alabama's law is clear, abortion is illegal in this state. These companies are not only breaking the law, they are deceiving Alabama consumers about the very real dangers of these drugs. That stops now."
- OLD blind: "Alabama's law is clear, abortion is illegal in this state. These companies are not only breaking the law, they are deceiving consumers about the very real dangers of these drugs. That stops now."
- **NEW blind: "[This state's] law is clear, abortion is illegal in this state. These companies are not only breaking the law, they are deceiving … consumers about the very real dangers of these drugs. That stops now."**
- why: Verbatim first-person statement. Both 'Alabama' state tells masked consistently ('[This state's]' and dropping the second 'Alabama'); substance (abortion illegal here, companies deceiving consumers) preserved verbatim.

### Jared Polis · same-sex-marriage · `29420975-2239-40f1-9d79-1f9c14b3ea8b`
- quote_text: 'As a gay man, I support prohibiting discrimination'
- OLD blind: 'As a gay man, I support prohibiting discrimination'
- **NEW blind: '… I support prohibiting discrimination'**
- why: Verbatim first-person statement. 'As a gay man' is a strong self-identifying tell removed with ellipsis; the position (support for prohibiting discrimination) is unchanged and not load-bearing on the identity.

### Phil Scott · same-sex-marriage · `b654dca0-1a64-4867-b638-d900bf543134`
- quote_text: 'Vermont has a well-earned reputation for embracing equality and being inclusive'
- OLD blind: 'Vermont has a well-earned reputation for embracing equality and being inclusive'
- **NEW blind: '[This state] has a well-earned reputation for embracing equality and being inclusive'**
- why: Verbatim first-person statement. 'Vermont' state tell substituted with '[This state]'; the substance (state's reputation for equality/inclusion) is preserved.

### Sky Danley · climate-change · `a943dff9-465b-472d-8434-1051a4650be5`
- quote_text: "Methane gas is 80 times more of a contributing factor to global warming than carbon dioxide, so I feel it's statistically pointless to deal with CO2 emissions."
- OLD blind: 'Methane gas is claimed to be a far bigger contributing factor to global warming than carbon dioxide, so dealing with CO2 emissions specifically is seen as statistically pointless.'
- **NEW blind: "Methane gas is 80 times more of a contributing factor to global warming than carbon dioxide, so I feel it's statistically pointless to deal with CO2 emissions."**
- why: Verbatim first-person statement with no identity leak. The broken deidentified_text was a paraphrase that hedged the assertion and stripped the first-person voice; restored to the verbatim quote_text unchanged (nothing to redact).

### Sky Danley · campaign-finance · `96121f02-8e87-4bb9-bc78-7910018ee10c`
- quote_text: 'In the interest of returning our government to where our founders intended, with the people, we must absolutely get the corrupting money out of politics.'
- OLD blind: 'In the interest of returning government to where the founders intended, with the people, the corrupting money must be gotten out of politics.'
- **NEW blind: 'In the interest of returning our government to where our founders intended, with the people, we must absolutely get the corrupting money out of politics.'**
- why: Verbatim first-person statement with no identity leak. The broken deidentified_text was an unnecessary passive-voice paraphrase; restored verbatim to quote_text (no identifier to remove).

### Gavin Newsom · deportation · `e8b11655-5d7f-416f-b5ad-bcac68479e65`
- quote_text: 'California will not be complicit in policies that tear families apart and sow terror in our communities. We are a state that defends the dignity and rights of all people, regardless of immigration status.'
- OLD blind: 'California will not be complicit in policies that tear families apart and sow terror in our communities. We are a state that defends the dignity and rights of all people, regardless of immigration status.'
- **NEW blind: '[This state] will not be complicit in policies that tear families apart and sow terror in our communities. We are a state that defends the dignity and rights of all people, regardless of immigration status.'**
- why: Genuine first-person verbatim statement. Only leak is the geographic self-ID 'California' (speaker is its governor); neutralized to '[This state]' by honest bracket-substitution, everything else kept verbatim.

### JB Pritzker · deportation · `768f9712-e761-4c3f-a2d3-c5fb95b60100`
- quote_text: '"violent criminals have no place on our streets; if they are undocumented, we want them out of Illinois and out of our country"'
- OLD blind: '"violent criminals have no place on our streets; if they are undocumented, we want them out of Illinois and out of our country"'
- **NEW blind: 'violent criminals have no place on our streets; if they are undocumented, we want them out of [this state] and out of our country'**
- why: Verbatim first-person statement. Only leak is the geographic tell 'Illinois' (speaker is its governor); replaced with bracketed '[this state]', rest kept word-for-word.

### Bo Biteman · fossil-fuels · `eec41ad9-2d92-47f7-9e1d-84c6cbc89023`
- quote_text: "Wyoming voters want action on President Trump's agenda. As president of the Senate, I want to continue to support and defend our energy industries to regain energy dominance...and make Wyoming business friendly again by eliminating excessive and costly regulations."
- OLD blind: "Voters here want action on the President's agenda. As a state legislative leader, I want to continue to support and defend our energy industries to regain energy dominance...and make the state business friendly again by eliminating excessive and costly regulations."
- **NEW blind: "[This state's] voters want action on [the President's] agenda. …I want to continue to support and defend our energy industries to regain energy dominance...and make [this state] business friendly again by eliminating excessive and costly regulations."**
- why: Genuine first-person verbatim quote that was previously paraphrased. Restored verbatim wording; marked identity leaks only: 'Wyoming'->'[this state]' (geographic), the role self-ID 'As president of the Senate,' dropped with ellipsis, and partisan tell 'President Trump's'->'[the President's]' (neutralized to office).

### Ilhan Omar · civil-rights · `57e7ab13-29b2-49ac-a8bb-999732552696`
- quote_text: 'Fight for the liberation of our LGBTQ community'
- OLD blind: 'Fight for the liberation of our community'
- **NEW blind: 'Fight for the liberation of our LGBTQ community'**
- why: Candidate-voice statement ('our ... community'); 'LGBTQ' is substantive content, not an identity leak of the speaker, and was silently deleted. Restored verbatim; no identity redaction needed.

### Matthew Hayes · misinformation · `578e7e7c-d59d-4aa1-bc5f-806b6d78301d`
- quote_text: 'In the Age of The Internet and Cable News, Disinformation presents a Clear and Present Danger to our Democracy... We must be vigilant in choosing reliable sources for our news and health information... Please reject all lies and falsehoods!'
- OLD blind: 'Disinformation is a real danger, but the answer is for citizens to be vigilant and choose reliable sources -- not government content moderation.'
- **NEW blind: 'In the Age of The Internet and Cable News, Disinformation presents a Clear and Present Danger to our Democracy... We must be vigilant in choosing reliable sources for our news and health information... Please reject all lies and falsehoods!'**
- why: Genuine first-person verbatim statement ('our Democracy', 'We must be vigilant'). The prior de-id fabricated the position 'not government content moderation', which appears nowhere in the source. No identity leaks, so restored fully verbatim.

### Ashtyn Kennedy · ai-regulation · `1608ac80-5d8f-4e6c-b91b-b00b6f5dbd68`
- quote_text: 'Investigate their rapid expansion, protect our water and grid, and regulate AI before more communities are sold out.'
- OLD blind: "The platform calls to investigate data centers' rapid expansion, protect water and grid resources, and regulate AI before more communities are affected."
- **NEW blind: 'Investigate their rapid expansion, protect our water and grid, and regulate AI before more communities are sold out.'**
- why: Coherent imperative sentence in the candidate's voice ('our water and grid'), no identity leak. Prior de-id was a paraphrase ('The platform calls to', 'data centers'', 'sold out'->'affected'). Restored verbatim without added content.

### Nathanael Schultz · abortion · `de640b1f-3bfe-43f5-a5dd-7a4664b0fe21`
- quote_text: 'actually pro life (no, you cannot kill an infant because it had Down syndrome or was conceived in tragedy)'
- OLD blind: 'actually pro life (you cannot end a pregnancy because of a disability diagnosis or because it was conceived in tragedy)'
- **NEW blind: 'actually pro life (no, you cannot kill an infant because it had Down syndrome or was conceived in tragedy)'**
- why: Candidate's own position stated in-voice; no identity leak. Prior de-id softened the substantive claim ('kill an infant'->'end a pregnancy', 'Down syndrome'->'disability diagnosis'), a substance change. Restored verbatim.

### Nathanael Schultz · religious-freedom · `3965c4bf-1dae-4253-8864-c224f9508cbb`
- quote_text: 'Islam is not welcome in our government, we are a Christian Nation'
- OLD blind: 'This is a Christian Nation, and other religions are not welcome in our government.'
- **NEW blind: 'Islam is not welcome in our government, we are a Christian Nation'**
- why: First-person-plural statement ('we are a Christian Nation'); no geographic/partisan/self-ID leak. Prior de-id changed substance by generalizing 'Islam'->'other religions'. Restored verbatim.

### Ayden Scott · abortion · `ab4d0f46-f20d-4684-92c9-de90a4dae76d`
- quote_text: 'I am proudly pro-life. I believe life begins at conception and that every life deserves protection. I support policies that protect the unborn and will oppose taxpayer funding of abortion at the federal level. Decisions of this magnitude belong as close to the people as possible — at the state and community level, not dictated from Washington.'
- OLD blind: 'Being proudly pro-life and believing life begins at conception, with support for policies protecting the unborn and opposition to taxpayer funding of abortion at the federal level, while believing decisions of this magnitude belong at the state and community level, not dictated from Washington.'
- **NEW blind: 'I am proudly pro-life. I believe life begins at conception and that every life deserves protection. I support policies that protect the unborn and will oppose taxpayer funding of abortion at the federal level. Decisions of this magnitude belong as close to the people as possible — at the state and community level, not dictated from Washington.'**
- why: Genuine first-person verbatim quote that was nominalized into paraphrase. No identity leaks ('Washington' denotes the federal government, not a speaker tell). Restored fully verbatim.

### Ayden Scott · climate-change · `bcbcfe38-db17-402d-9c08-6c76c8fcf435`
- quote_text: 'I support an all-of-the-above energy strategy that includes oil, gas, coal, nuclear, and renewables — chosen by the market, not mandated by government.'
- OLD blind: 'Support for an all-of-the-above energy strategy that includes oil, gas, coal, nuclear, and renewables — chosen by the market, not mandated by government.'
- **NEW blind: 'I support an all-of-the-above energy strategy that includes oil, gas, coal, nuclear, and renewables — chosen by the market, not mandated by government.'**
- why: First-person verbatim statement nominalized by the prior de-id ('Support for...'). No identity leak; restored verbatim.

### Ayden Scott · healthcare · `3bc4153e-ceb2-4413-8806-79f7fbdb1eab`
- quote_text: 'I support free-market solutions that increase transparency, expand Health Savings Accounts, and break the barriers that prevent real price competition... I will oppose any push toward a government-run system that removes what little choice patients still have.'
- OLD blind: 'We should support free-market solutions that increase transparency, expand Health Savings Accounts, and break the barriers that prevent real price competition, and oppose any push toward a government-run system that removes what little choice patients still have.'
- **NEW blind: 'I support free-market solutions that increase transparency, expand Health Savings Accounts, and break the barriers that prevent real price competition... I will oppose any push toward a government-run system that removes what little choice patients still have.'**
- why: Genuine two-part first-person verbatim quote reworded by the prior de-id ('We should support...and oppose...'). No identity leak; restored verbatim, preserving the original ellipsis between the two parts.

### Ayden Scott · taxes · `bd4b92c7-9b7e-4f5c-a23d-b0b6ac232606`
- quote_text: 'I will push for a balanced budget amendment, oppose continuing resolutions that fund bloated government indefinitely, and demand independent, transparent accountability for every dollar of federal spending... I will fight to put permanent, nonpartisan safeguards in place that protect against exploitation of federal funds, cut wasteful programs, and eliminate duplicative agencies.'
- OLD blind: 'Pushing for a balanced budget amendment, opposing continuing resolutions that fund bloated government indefinitely, and demanding transparent accountability for every dollar of federal spending, including cutting wasteful programs and eliminating duplicative agencies.'
- **NEW blind: 'I will push for a balanced budget amendment, oppose continuing resolutions that fund bloated government indefinitely, and demand independent, transparent accountability for every dollar of federal spending... I will fight to put permanent, nonpartisan safeguards in place that protect against exploitation of federal funds, cut wasteful programs, and eliminate duplicative agencies.'**
- why: Genuine verbatim first-person statement. No identity leaks present (no self-ID, geography, named parties, or partisan tells). The original quote_text is fully publishable as-is; the broken version had paraphrased first-person commitments into a gerund summary. Restored verbatim including the candidate's own ellipsis.

### Anthony Hamilton · campaign-finance · `06c0edec-7ad1-41f4-a6d6-a9f69d45db0c`
- quote_text: 'Foreign money, dark money, and the revolving door between Congress and K Street are corrupting your government. I will push to ban foreign-affiliated PACs, extend post-service lobbying bans to 5 years, and require full public disclosure of every meeting between lobbyists and elected officials.'
- OLD blind: 'Foreign money, dark money, and the revolving door between Congress and K Street are corrupting government. Foreign-affiliated PACs should be banned, post-service lobbying bans extended to 5 years, and full public disclosure required of every meeting between lobbyists and elected officials.'
- **NEW blind: 'Foreign money, dark money, and the revolving door between Congress and K Street are corrupting your government. I will push to ban foreign-affiliated PACs, extend post-service lobbying bans to 5 years, and require full public disclosure of every meeting between lobbyists and elected officials.'**
- why: Genuine verbatim first-person statement with no identity leaks (Congress/K Street are general references, not self-ID or partisan tells). Restored verbatim; the broken version had paraphrased first-person commitments into passive voice.

### Ryan Mackenzie · abortion · `ed10928c-2794-40e6-b8e6-9bda6af93e22`
- quote_text: '"We should not be doing a federal ban, which has been talked about by some candidates, not only in this race, but nationally."'
- OLD blind: '"The issue of abortion access should be left to the states — a federal ban should not be done, which has been talked about by some candidates, not only in this race, but nationally."'
- **NEW blind: '"We should not be doing a federal ban, which has been talked about by some candidates, not only in this race, but nationally."'**
- why: Genuine verbatim first-person statement with no identity leaks. Restored verbatim; the broken version fabricated a prepended clause ('The issue of abortion access should be left to the states') the candidate never said.

### Jerry Carl · religious-freedom · `3313aaac-2505-4e51-ae7b-fbb8863479fe`
- quote_text: 'Governments should not discriminate against individuals, organizations or small businesses because of their belief that marriage is only a union of one man and one woman.'
- OLD blind: 'People should not be discriminated against by government for holding a traditional belief about marriage.'
- **NEW blind: 'Governments should not discriminate against individuals, organizations or small businesses because of their belief that marriage is only a union of one man and one woman.'**
- why: Genuine verbatim statement with no identity leaks (no self-ID, geography, or partisan tells; a policy position is not an identity tell). Restored verbatim; the broken version had paraphrased the specific position down to a vague summary, altering substance though nothing needed removal.

### Katrina deVille · civil-rights · `2b95812e-80e3-4db8-adbc-ccd555f767f3`
- quote_text: "The past year's steady erosion of Constitutional rights for the trans community has been a window of reality."
- OLD blind: "The past year's steady erosion of Constitutional rights for my community has been a window of reality."
- **NEW blind: "The past year's steady erosion of Constitutional rights for the trans community has been a window of reality."**
- why: Genuine verbatim statement. The original quote_text ('the trans community') contains no identity leak; it is the broken version that INTRODUCED a self-ID tell by rewriting to 'my community.' Restoring the verbatim original removes the leak.

### Keith Arnold · abortion · `acceb118-f51a-4ec6-a5e1-9cc85b42f6da`
- quote_text: 'I am Pro Life. (Abortion should be allowed only in cases of rape, incest, or a significant health risk. That significant health risk should be limited to life-threatening risk until medicine can give us a better definition which may consider severe birth defects.)'
- OLD blind: 'I am pro-life, and I believe abortion should be allowed only in cases of rape, incest, or a significant, life-threatening health risk to the mother.'
- **NEW blind: 'I am Pro Life. (Abortion should be allowed only in cases of rape, incest, or a significant health risk. That significant health risk should be limited to life-threatening risk until medicine can give us a better definition which may consider severe birth defects.)'**
- why: Genuine verbatim first-person statement with no identity leaks. Restored verbatim; the broken version paraphrased and silently dropped the candidate's 'severe birth defects' / awaiting-better-medical-definition qualifier, altering the position's scope.

### Riley M. Moore · fossil-fuels · `1b1c4abe-9b81-4ee2-b5e1-33fbe622ef58`
- quote_text: 'I was the first state treasurer in this country to divest BlackRock'
- OLD blind: 'The speaker was the first state treasurer in the country to divest BlackRock and created a restricted financial institution list that put woke banks on it.'
- **NEW blind: 'I was the first state treasurer in this country to divest BlackRock'**
- why: Genuine verbatim first-person statement with no identity leak ('state treasurer' here is a factual claim about an accomplishment, not a geographic/partisan tell, and no state is named). Restored verbatim; the broken version paraphrased into third person and added fabricated material with a partisan tell ('woke banks').

### Doug Chapin · deportation · `73340559-ae6a-4a36-94e0-7e1e6b6da8c1`
- quote_text: 'I support efforts to withhold ICE funding, even if it means a shutdown, to force ICE out Minnesota - and in Congress will seek to dismantle ICE so this never happens again.'
- OLD blind: 'A supporter of efforts to withhold ICE funding, even if it means a shutdown, to force ICE out of the state - and in Congress would seek to dismantle ICE so this never happens again.'
- **NEW blind: 'I support efforts to withhold ICE funding, even if it means a shutdown, to force ICE out [this state] - and in Congress will seek to dismantle ICE so this never happens again.'**
- why: Genuine verbatim first-person statement. Only identity leak is the geographic tell 'Minnesota,' replaced with '[this state]' per marking policy. First person needs no rewrite; the broken version had needlessly paraphrased into third person.

### Doug Chapin · immigration · `674a0c7c-5d71-4c82-86d4-b401330337b0`
- quote_text: 'I will fight for common-sense reform that works for immigrants and employers alike and reduces the time, cost and red tape associated with becoming an American.'
- OLD blind: 'A candidate will fight for common-sense reform that works for immigrants and employers alike and reduces the time, cost and red tape associated with becoming an American.'
- **NEW blind: 'I will fight for common-sense reform that works for immigrants and employers alike and reduces the time, cost and red tape associated with becoming an American.'**
- why: Genuine verbatim first-person statement with no identity leaks. First person is not a self-ID. Restored verbatim; the broken version needlessly rewrote 'I will fight' into 'A candidate will fight.'

### Vince George · climate-change · `55696f8b-0072-44dc-9692-92c950daeb0c`
- quote_text: "I also believe in a free market that allows renewable energy to expand in West Virginia if that's what businesses want to do. This would create more jobs and lower electrical costs."
- OLD blind: "A free market should allow renewable energy to expand if that's what businesses want to do. This would create more jobs and lower electrical costs."
- **NEW blind: "I also believe in a free market that allows renewable energy to expand in [this state] if that's what businesses want to do. This would create more jobs and lower electrical costs."**
- why: Genuine verbatim first-person statement. Only identity leak is the geographic tell 'West Virginia,' replaced with '[this state]'. First person preserved verbatim; the broken version had paraphrased the belief into an impersonal prescription.

### Ralph Alvarado · abortion · `e3d3ea49-c610-4f41-9598-b0f6126c2829`
- quote_text: 'House Bill 5 would hold the abortionist accountable for performing an abortion for a specific reason: because the baby is a boy or a girl, because the baby is a particular race or because they might be born with a known or suspected disability.'
- OLD blind: 'A state senator said a bill would hold the abortion provider accountable for performing an abortion for a specific reason: because the baby is a boy or a girl, because the baby is a particular race, or because they might be born with a known or suspected disability.'
- **NEW blind: 'House Bill 5 would hold the abortionist accountable for performing an abortion for a specific reason: because the baby is a boy or a girl, because the baby is a particular race or because they might be born with a known or suspected disability.'**
- why: Genuine verbatim statement (candidate describing a bill). No identity leak in quote_text itself; the office ('state senator') tell was INTRODUCED by the broken deidentified version's added attribution frame. 'House Bill 5' is a bill name, not a self-ID. Restored verbatim.

### Zach Dembo · healthcare · `5142f6d4-8803-4638-9ff6-f208ee279ff1`
- quote_text: 'Working families all across the country are already struggling to make ends meet. Now, Kentucky families will start 2026 at risk of their health care premiums doubling what they paid last year because Kentucky Republicans in Congress voted against lowering their health care costs. Lives are on the line, our families deserve affordable coverage, and Kentuckians deserve better.'
- OLD blind: 'Working families all across the country are already struggling to make ends meet. Families will start next year at risk of health care premiums doubling what they paid last year because Republicans in Congress voted against lowering health care costs. Lives are on the line, families deserve affordable coverage, and they deserve better.'
- **NEW blind: 'Working families all across the country are already struggling to make ends meet. Now, families will start 2026 at risk of their health care premiums doubling what they paid last year because … in Congress voted against lowering their health care costs. Lives are on the line, our families deserve affordable coverage, and … deserve better.'**
- why: Genuine verbatim first-person-style statement. Identity leaks removed via honest marking: geographic tells 'Kentucky'/'Kentuckians' dropped (families → 'families'; final 'Kentuckians deserve better' → '… deserve better'), and the partisan tell 'Kentucky Republicans' depersonalized by dropping the actor ('because … in Congress voted against'). Preserved substance and the year '2026'.

### Rashida Tlaib · social-security · `7dc0324f-450a-45a8-8e44-2b17cf19af7e`
- quote_text: "The Republicans are eager to cut Social Security, Medicare, and Medicaid to pay for their tax cuts for the rich, and we can't let them get away with it."
- OLD blind: "The Republicans are eager to cut Social Security, Medicare, and Medicaid to pay for their tax cuts for the rich, and we can't let them get away with it."
- **NEW blind: "[Some in Congress] are eager to cut Social Security, Medicare, and Medicaid to pay for tax cuts for the rich, and we can't let them get away with it."**
- why: Genuine verbatim first-person statement. Only the partisan actor tell 'The Republicans' (and the partisan possessive 'their') is an identity leak; neutralized to office via bracket substitution, rest kept verbatim.

### Patrick Ryan · taxes · `6c762260-ece7-49b6-82c2-7d937aa7e96a`
- quote_text: "If Republicans want to get serious about cutting the deficit, let's talk about finally making the ultra-wealthy pay their fair share: removing the yacht tax deduction, cutting the private-plane tax break, and reining in the big corporations who don't pay a cent in taxes."
- OLD blind: "If Republicans want to get serious about cutting the deficit, let's talk about finally making the ultra-wealthy pay their fair share: removing the yacht tax deduction, cutting the private-plane tax break, and reining in the big corporations who don't pay a cent in taxes."
- **NEW blind: "If [lawmakers] want to get serious about cutting the deficit, let's talk about finally making the ultra-wealthy pay their fair share: removing the yacht tax deduction, cutting the private-plane tax break, and reining in the big corporations who don't pay a cent in taxes."**
- why: Genuine verbatim first-person statement. Only the partisan tell 'Republicans' is an identity leak; neutralized to '[lawmakers]', everything else verbatim.

### Alex Scheel · campaign-finance · `58d01aa6-1bb2-4272-9ec2-ea462eadd02d`
- quote_text: 'We DO NOT and will NEVER accept: AIPAC $$ Corporate PAC $$'
- OLD blind: 'We do not and will never accept money from foreign-lobby-aligned groups or corporate PACs.'
- **NEW blind: 'We DO NOT and will NEVER accept: AIPAC $$ Corporate PAC $$'**
- why: Genuine verbatim first-person statement. 'AIPAC' is the load-bearing target of the stance, not a speaker-identity leak; the broken version editorialized it to 'foreign-lobby-aligned groups'. Restored verbatim with no identity leak to strip.

### Alex Scheel · deportation · `a10e2d77-4b7e-414d-ad24-06e62c5dbacd`
- quote_text: 'I want to fight against the reign of terror imposed against our immigrant neighbors by ICE and Geo Group'
- OLD blind: 'I want to fight against the harsh treatment imposed on our immigrant neighbors by ICE and private detention contractors.'
- **NEW blind: 'I want to fight against the reign of terror imposed against our immigrant neighbors by ICE and Geo Group'**
- why: Genuine verbatim first-person statement. 'ICE' and 'Geo Group' are load-bearing targets, not speaker-identity leaks; the broken version softened 'reign of terror' and paraphrased the target. Restored verbatim; no identity leak present.

### Alex Scheel · immigration · `5b37f36e-3e73-4efe-88e4-662910d1b58a`
- quote_text: 'I want to fight against the reign of terror imposed against our immigrant neighbors by ICE and Geo Group'
- OLD blind: 'I want to fight against the harsh treatment imposed on our immigrant neighbors by ICE and private detention contractors.'
- **NEW blind: 'I want to fight against the reign of terror imposed against our immigrant neighbors by ICE and Geo Group'**
- why: Same statement as the deportation entry. Genuine verbatim first-person; targets are load-bearing, no speaker-identity leak. Restored verbatim; broken version wrongly softened tone and paraphrased 'Geo Group'.

### Kurtis Engle · redistricting · `e22b2018-2e5d-48e9-8a2e-95e98464f6ad`
- quote_text: 'What process do you favor for redistricting? Scrupulously non-partisan. The way no one does it, now, and the way everyone should... the process should be placed in non-political hands.'
- OLD blind: 'I favor a scrupulously non-partisan redistricting process, placed entirely in non-political hands, not run by elected officials of either party.'
- **NEW blind: '…Scrupulously non-partisan. The way no one does it, now, and the way everyone should… the process should be placed in non-political hands.'**
- why: The candidate's answer is genuine verbatim first-person with no identity leak. Stripped the embedded interviewer question ('What process do you favor for redistricting?') with a leading ellipsis; kept the candidate's words verbatim. Broken version was a fabricated paraphrase that added 'not run by elected officials of either party'.

### James Russell · climate-change · `8c4f70a2-a156-452f-8f86-c2f68687153d`
- quote_text: 'We need to invest in a clean energy future that uses multiple sources to stay safe and strong.'
- OLD blind: 'The platform calls for investing in a clean energy future that uses multiple energy sources to stay safe and strong.'
- **NEW blind: 'We need to invest in a clean energy future that uses multiple sources to stay safe and strong.'**
- why: Genuine verbatim first-person statement with no identity leak; broken version narrated it into third person ('The platform calls for...'). Restored verbatim.

### James Russell · healthcare · `a9612fb0-a901-4b5e-b40a-02c08e44d42a`
- quote_text: 'Americans pay way more for healthcare than people in other countries -- but we get worse results.'
- OLD blind: "The candidate's platform notes that Americans pay far more for healthcare than people in other countries but get worse results."
- **NEW blind: 'Americans pay way more for healthcare than people in other countries -- but we get worse results.'**
- why: Genuine verbatim first-person statement with no identity leak; broken version converted it to a third-person paraphrase. Restored verbatim.

### James Russell · housing · `d6c7b000-4bfe-4613-8048-69e26aeab485`
- quote_text: 'We should also count rent payments when people apply for a mortgage, so more folks can buy a home.'
- OLD blind: 'The platform proposes counting rent payments toward mortgage applications, so more people can buy a home.'
- **NEW blind: 'We should also count rent payments when people apply for a mortgage, so more folks can buy a home.'**
- why: Genuine verbatim first-person statement with no identity leak; broken version paraphrased into third person ('The platform proposes...'). Restored verbatim.

### James Russell · medicare/aid · `fc9cde0f-f8d7-4523-9c1d-aba18afd3fd2`
- quote_text: 'We can fix this by expanding Medicare and moving toward healthcare for everyone.'
- OLD blind: "The candidate's platform calls for expanding Medicare and moving toward healthcare for everyone."
- **NEW blind: 'We can fix this by expanding Medicare and moving toward healthcare for everyone.'**
- why: Genuine verbatim first-person statement with no identity leak; broken version narrated it into third person. Restored verbatim.

### James Russell · taxes · `9c1a216d-69a8-4a21-806c-b38d5954be97`
- quote_text: 'That means making taxes fairer. Working people should pay less, and the rich should pay their fair share.'
- OLD blind: "The candidate's platform calls for making taxes fairer -- working people should pay less, and the rich should pay their fair share."
- **NEW blind: 'That means making taxes fairer. Working people should pay less, and the rich should pay their fair share.'**
- why: Genuine verbatim first-person statement with no identity leak; broken version re-narrated it as 'The candidate's platform calls for...'. Restored verbatim (period, not the em-dash the broken version inserted).

### Erin Jemison · healthcare · `1a306803-0437-46f1-b3cf-68c18ba73c54`
- quote_text: 'Healthcare is a right'
- OLD blind: 'not a privilege.'
- **NEW blind: 'Healthcare is a right'**
- why: The deidentified_text was a corrupted tail fragment ('not a privilege.'). quote_text is a genuine first-person position statement with no identity leak; restored the blind text to match quote_text verbatim (cannot add 'not a privilege' since it is absent from quote_text).

### Scott Stephenson · public-safety-approach · `fcb716f3-77fa-4ccf-9a0a-d0d973b656a7`
- quote_text: '""Adding more laws does not always create safer communities. I support targeted enforcement, strong community partnerships, and investment in mental health and substance-use recovery."",""Adding more laws does not always create safer communities. I support targeted enforcement'
- OLD blind: 'strong community partnerships'
- **NEW blind: 'Adding more laws does not always create safer communities. I support targeted enforcement, strong community partnerships, and investment in mental health and substance-use recovery.'**
- why: quote_text is corrupted (duplicated/escaped), but the complete first-person statement is fully present and recoverable within it, with no identity leak. Restored the clean canonical sentence, defensible verbatim against the first complete instance in quote_text; broken deid was a stray three-word fragment.

### Matthew Dunlap · abortion · `885e051d-b59e-464d-912a-6dffee511b1f`
- quote_text: "I will always stand up for a woman's right to make her own health decisions, especially regarding reproductive health and safety...Safe access to reproductive health care should be a constitutional right."
- OLD blind: "Support for a person's right to make their own health decisions, especially regarding reproductive health and safety...Safe access to reproductive health care should be a constitutional right."
- **NEW blind: "I will always stand up for a woman's right to make her own health decisions, especially regarding reproductive health and safety...Safe access to reproductive health care should be a constitutional right."**
- why: Genuine verbatim first-person statement. 'I' is not an identity leak (blind cards are first-person) and there is no self-ID/geographic/partisan tell. Broken version wrongly rewrote it into a detached third-person nominal and changed 'woman's' to 'person's'. Restored verbatim.

### Matthew Dunlap · childcare · `1d86fcbd-f1bc-4820-b79d-dae4eafd589a`
- quote_text: "I'll fight for universal childcare, good-paying jobs, and a surge in affordable housing to help our neighbors manage a middle-class life again."
- OLD blind: 'There is a pledge to fight for universal childcare, good-paying jobs, and a surge in affordable housing to help neighbors manage a middle-class life again.'
- **NEW blind: "I'll fight for universal childcare, good-paying jobs, and a surge in affordable housing to help our neighbors manage a middle-class life again."**
- why: Genuine verbatim first-person pledge. No identity leaks present (no self-ID, geography, party, or named third party). The broken text detached it into third person; restoring the verbatim quote is correct with no marking needed.

### Matthew Dunlap · climate-change · `83d2e861-8f67-4320-8228-f6778d420a2a`
- quote_text: "I would support restoring the Inflation Reduction Act's tax credits. They were an immense investment in our green future."
- OLD blind: "There is support for restoring the Inflation Reduction Act's tax credits, described as an immense investment in the green future."
- **NEW blind: "I would support restoring the Inflation Reduction Act's tax credits. They were an immense investment in our green future."**
- why: Verbatim first-person statement. No identity leaks (the IRA is a policy reference, not a speaker/party self-ID). The broken text was a third-person summary; the verbatim quote stands as-is.

### Matthew Dunlap · medicare/aid · `97d59f0b-244e-41a1-a963-e5d7ff276b27`
- quote_text: 'my main priority will be introducing legislation to bring down healthcare costs for working Mainers'
- OLD blind: 'a main priority would be introducing legislation to bring down healthcare costs for working families'
- **NEW blind: 'my main priority will be introducing legislation to bring down healthcare costs for working [families]'**
- why: Verbatim first-person statement. 'Mainers' is a geographic tell (Maine) and must be dropped; ellipsis marks the removed span. The first-person 'my main priority will be' is kept verbatim rather than paraphrased.

### Matthew Dunlap · social-security · `f1779f00-7205-4945-a312-e6cddaaf49b2`
- quote_text: 'I am in favor of busting the cap on payroll taxes...We need to eliminate that cap to ensure the wealthiest are paying their fair share.'
- OLD blind: 'There is support for busting the cap on payroll taxes...the cap should be eliminated to ensure the wealthiest are paying their fair share.'
- **NEW blind: 'I am in favor of busting the cap on payroll taxes...We need to eliminate that cap to ensure the wealthiest are paying their fair share.'**
- why: Verbatim first-person statement. No identity leaks. The broken text converted it to third-person summary; verbatim quote stands with no redaction needed.

### Matthew Dunlap · tariffs · `25d5b545-e8cd-4721-bb00-c122590c04b5`
- quote_text: "While removing unnecessary blanket tariffs from the Trump Administration, I'll support strategically applying tariffs to hold foreign companies and countries accountable for unfair trade practices that harm our homegrown industries."
- OLD blind: 'While removing unnecessary blanket tariffs from the Trump Administration, there is support for strategically applying tariffs to hold foreign companies and countries accountable for unfair trade practices that harm homegrown industries.'
- **NEW blind: "While removing unnecessary blanket tariffs from the … Administration, I'll support strategically applying tariffs to hold foreign companies and countries accountable for unfair trade practices that harm our homegrown industries."**
- why: Verbatim first-person statement. 'Trump' is a partisan/side tell and is redacted with ellipsis (leaving 'the … Administration' preserves the anti-blanket-tariff meaning without naming). First-person 'I'll support' kept verbatim.

### Matthew Dunlap · ukraine-support · `a00ccab5-04bc-4fd8-98ec-a282e8d30bf8`
- quote_text: 'I support our ally Ukraine. I would support continued aid to them in their fight against Russia.'
- OLD blind: 'There is support for our ally Ukraine, including continued aid to them in their fight against Russia.'
- **NEW blind: 'I support our ally Ukraine. I would support continued aid to them in their fight against Russia.'**
- why: Verbatim first-person statement. No identity leaks. The broken text was a third-person summary; verbatim quote stands as-is.

### Chaz Molder · healthcare · `bb5cb456-6b74-450b-852a-146231bb3c36`
- quote_text: 'We must work to eliminate barriers to care, expand health insurance coverage, and invest in preventive services to keep our communities healthy. Together, we can build a healthcare system that prioritizes patients over profits and ensures that everyone has the care they deserve.'
- OLD blind: 'A healthcare system should eliminate barriers to care, expand coverage, and invest in preventive services -- prioritizing patients over profits so everyone has the care they deserve.'
- **NEW blind: 'We must work to eliminate barriers to care, expand health insurance coverage, and invest in preventive services to keep our communities healthy. Together, we can build a healthcare system that prioritizes patients over profits and ensures that everyone has the care they deserve.'**
- why: Verbatim first-person statement. No identity leaks. The broken text was a condensed rewrite; restoring the full verbatim quote is correct with no redaction needed.

### Terri A. Sewell · climate-change · `3a5f4be6-84f8-414f-9cfa-7609c601f393`
- quote_text: 'As a Representative of constituents disproportionately impacted by environmental injustices, I am proud to fight for stronger environmental protections.'
- OLD blind: 'As a Representative of constituents disproportionately impacted by environmental injustices, the work continues to fight for stronger environmental protections.'
- **NEW blind: 'As a … of constituents disproportionately impacted by environmental injustices, I am proud to fight for stronger environmental protections.'**
- why: Verbatim first-person statement. 'Representative' is a speaker self-ID revealing a sitting member of Congress and must be dropped; ellipsis marks the removed span. The rest is kept verbatim including first-person 'I am proud to fight.'

### Adam D. Austill · campaign-finance · `eb1edccc-fb07-4e57-abb7-0031d285b94d`
- quote_text: 'We must limit the ability of money to purchase power in Congress. To move this nation towards a less corrupt Congress, I propose: funding elections with public money (as long as private money is allowed to flow through politics, the golden rule will remain, "He who has the gold makes the rule")'
- OLD blind: 'He proposes public campaign financing, arguing that as long as private money is allowed to flow through politics, "he who has the gold makes the rule."'
- **NEW blind: 'We must limit the ability of money to purchase power in Congress. To move this nation towards a less corrupt Congress, I propose: funding elections with public money (as long as private money is allowed to flow through politics, the golden rule will remain, "He who has the gold makes the rule")'**
- why: Verbatim first-person statement. No identity leaks (no self-ID, geography, or party). The broken text was a third-person curator paraphrase; the verbatim quote stands as-is.

### Dewey Gordon Bryan · tariffs · `8b51f692-9acf-4caf-8e62-ed8d2858fff3`
- quote_text: 'Idiotic and mathematically flawed tariff policies raised prices for EVERY American on EVERYTHING.'
- OLD blind: 'Recent tariff policies raised prices for every American on everything, and farmers have been hurt by them repeatedly.'
- **NEW blind: 'Idiotic and mathematically flawed tariff policies raised prices for EVERY American on EVERYTHING.'**
- why: Verbatim first-person statement. No identity leaks. The broken text softened the tone and fabricated an added farmers claim; restoring the verbatim quote removes the fabrication and needs no redaction.

### Hakeem S. Jeffries · healthcare · `88da6371-4927-4fbc-b68c-fff31c8f1b6e`
- quote_text: '"GOP can\'t beat ObamaCare, so they pretend it\'s a \'disaster\'."'
- OLD blind: '"The other party can\'t beat [this law], so they pretend it\'s a \'disaster\'."'
- **NEW blind: '"… can\'t beat [this law], so they pretend it\'s a \'disaster\'."'**
- why: Verbatim quote. 'GOP' is a partisan/side tell; per policy, prefer dropping rather than an awkward swap ('the other party' leaks the speaker's defending side). Dropping the subject to ellipsis and bracketing 'ObamaCare'→[this law] removes the side tell while keeping the defended-law substance.

### Jimmy Patronis · healthcare · `92f9fd33-83ad-4251-8946-17b27aa91344`
- quote_text: 'This is what the Democrats are spun up on. They want the enhanced subsidies.'
- OLD blind: 'This is what the other side is spun up on. They want the enhanced subsidies.'
- **NEW blind: 'This is what … are spun up on. They want the enhanced subsidies.'**
- why: Verbatim first-person statement. 'the Democrats' is a partisan/side tell; dropping it to ellipsis (rather than 'the other side', which still tells the speaker's opposing side) removes the tell while keeping the substance about enhanced subsidies.

### Tyrone Muhammad · immigration · `b78991cd-d93c-4736-82b3-d5db8df262d7`
- quote_text: 'The very notion that anyone will have the audacity to think that a non-citizen should go before the Black community and the most impoverished communities is asinine.'
- OLD blind: 'The very notion that anyone would think a non-citizen should go before this community and the most impoverished communities is asinine.'
- **NEW blind: 'The very notion that anyone will have the audacity to think that a non-citizen should go before the Black community and the most impoverished communities is asinine.'**
- why: Verbatim first-person statement. No identity leaks (no self-ID, geography, or party; 'the Black community' is substantive content, not a speaker tell). The broken text paraphrased and softened; restoring the verbatim quote is correct.

### Tyrone Muhammad · deportation · `c4af5bc3-3bed-4528-8864-5d34363d31de`
- quote_text: "We're going to be on the ground with him to remove these illegals and these migrants and identifying where they are because they have greatly diminished our way of life here in Chicago."
- OLD blind: "We're going to help remove these illegals and these migrants and identify where they are because they have greatly diminished our way of life in this city."
- **NEW blind: "We're going to be on the ground with … to remove these illegals and these migrants and identifying where they are because they have greatly diminished our way of life here in [this city]."**
- why: Verbatim first-person statement. 'him' points to a coordinated named actor and is dropped to ellipsis; 'Chicago' is a geographic tell replaced with [this city]. The coordinated-action content ('on the ground with … to remove') is kept verbatim rather than silently rewritten to 'help remove.'

## REMOVE — set live=false (quote_text is a summary/fragment, not a real quote)

- **Matthew D. Klein** · medicare/aid · `c2e7f7d1-0320-4a09-9d0b-7a9940f893a3` — 'one trillion dollars in cuts to Medical Assistance coming from HR 1'
    - The deidentified_text is a paraphrase that adds a clause ('is going to be a real cost crisis for everyone') not present in quote_text. The quote_text itself ('one trillion dollars in cuts to Medical Assistance coming from HR 1') is a telegraphic fragment, not a first-person verbatim statement; cannot be salvaged by editing the blind text.
- **Vivek Malek** · taxes · `924267aa-673e-4631-9983-3d57363c7499` — 'protect taxpayer money from liberal waste fraud and abuse'
    - The quote_text ('protect taxpayer money from liberal waste fraud and abuse') is a telegraphic platform fragment, not a first-person verbatim statement. It cannot be salvaged, and the partisan 'liberal' tell is load-bearing to the fragment; unpublish.
- **Harriet M. Hageman** · climate-change · `cb117702-4f85-40ae-99f5-0598121f6a9f` — 'coal is an ""affordable'
    - quote_text is garbled/truncated ('coal is an ""affordable') and the deidentified_text is a single stray word ('clean'). Not a coherent verbatim first-person statement; cannot be salvaged. Unpublish.
- **Robin L. Kelly** · healthcare · `a0614b46-4329-403c-ae0a-2616dd86d659` — "GOP can't beat ObamaCare, so they pretend it's a 'disaster'"
    - quote_text ('GOP can't beat ObamaCare, so they pretend it's a "disaster"') is a third-person headline fragment, not a first-person verbatim statement. The partisan 'GOP' tell is load-bearing to the sentence's meaning; cannot be salvaged by masking. Unpublish.
- **Paige Beauchemin** · healthcare · `549a2478-3250-4f33-adc7-eb654e77b6d0` — '"Government-managed single-payer Medicare for all, to address prescription drug prices, dental, hearing, and durable equipment."'
    - quote_text is a platform bullet fragment ('Government-managed single-payer Medicare for all, to address prescription drug prices, dental, hearing, and durable equipment.'), not a first-person verbatim statement, and the deidentified_text is an explicit curator paraphrase ('The candidate's platform calls for...'). Cannot be salvaged; unpublish.
- **Paige Beauchemin** · medicare/aid · `7bbf4960-1e9c-4387-ba4e-a3635ab3d352` — '"Government-managed single-payer Medicare for all, to address prescription drug prices, dental, hearing, and durable equipment."'
    - Same platform bullet fragment as the prior quote, not a first-person verbatim statement, and the deidentified_text is a curator paraphrase ('The candidate's platform calls for...'). Cannot be salvaged; unpublish.
- **Jillian Balow** · fossil-fuels · `05b76220-68c1-4761-b703-611046561cf2` — 'Repeal burdensome energy regulations. Pass the PERMIT and SPEED Acts. Expand energy production, revenue streams, and innovation.'
    - Not a verbatim first-person statement — three telegraphic imperative platform planks ('Repeal... Pass... Expand...'), a bullet-list platform summary rather than something the candidate is quoted saying. Cannot be salvaged by blind-text editing.
- **Frank Chapman** · fossil-fuels · `966e32a4-392e-4b1b-91b5-817dd7fd39cd` — 'Frank knows removing government obstacles from producing American energy, while protecting our environment, is the most sensible approach to reducing our reliance on foreign oil.'
    - Third-person curator copy ('Frank knows removing government obstacles ... is the most sensible approach'), not a first-person quote. The impersonal de-id was a paraphrase; cannot be fixed by identity marking.
- **Peter Burgelis** · tariffs · `e736d7f6-4228-4dda-882d-fe1d3dc23601` — "repeal Trump's tariffs"
    - Telegraphic three-word headline fragment / policy-plank label ('repeal Trump's tariffs'), not a verbatim first-person statement. Too fragmentary to be a genuine quote.
- **Ashtyn Kennedy** · healthcare · `bd0de3a2-48a8-485d-9f1a-ab9f9ffe5516` — 'Pro choice, universal healthcare, expanded Medicaid, and a government that stays out of the exam room.'
    - Not a verbatim statement — a noun-phrase platform bullet list ('Pro choice, universal healthcare, expanded Medicaid...'). The 'The platform calls for...' framing confirms curator summary; cannot be salvaged by identity marking.
- **Ashtyn Kennedy** · medicare/aid · `581cccf0-ca79-40e0-9beb-edec323b434f` — 'Pro choice, universal healthcare, expanded Medicaid, and a government that stays out of the exam room.'
    - Duplicate of the same noun-phrase platform bullet list ('Pro choice, universal healthcare, expanded Medicaid...'), a curator summary rather than a verbatim quote. Remove.
- **Nancy Wallace** · healthcare · `e7275e36-f697-41d8-bd9b-b840f34e3594` — 'Enact a universal, comprehensive, national single-payer health plan that will provide the following with no increase in cost:'
    - quote_text is a truncated bullet-list lead-in ending in a colon ('...that will provide the following with no increase in cost:'), an imperative platform header, not a verbatim first-person spoken statement. Cannot be salvaged by editing; the broken version fabricated a completion. Unpublish.
- **Nancy Wallace** · medicare/aid · `148a7d7d-42ea-42ff-bbac-13360dfc3be4` — 'Enact a universal, comprehensive, national single-payer health plan that will provide the following with no increase in cost:'
    - Identical truncated colon-ending bullet-list lead-in as quote 4. Imperative platform header, not a verbatim first-person statement; the broken version fabricated a different completion. Unpublish.
- **Keith Arnold** · deportation · `362af3ee-72bd-4327-bc54-7043d326dee3` — 'Immigration laws should be enforced focusing on those who hire (and exploit) persons here illegally.'
    - quote_text ('Immigration laws should be enforced focusing on those who hire...') is an impersonal prescriptive platform statement, not a verbatim first-person spoken quote. The broken version fabricated first-person voice ('I believe') and added a substantive claim ('not on deporting the workers themselves'). Not a verbatim quote; unpublish.
- **Doug Ollivant** · trans-athletes · `0c6aff0c-2c73-4168-9c5c-37c10b6f2805` — 'Protecting Girls in Sports and Spaces: Safeguarding fairness, privacy, and safety. As the father of two daughters, Doug understands the importance of this issue.'
    - quote_text is a third-person platform blurb with a headline fragment ('Protecting Girls in Sports and Spaces: Safeguarding fairness, privacy, and safety') plus a third-person bio sentence naming the candidate ('As the father of two daughters, Doug understands...'). Not a verbatim first-person spoken quote; it is curator/campaign copy. Unpublish.
- **Kshama Sawant** · campaign-finance · `95a66db1-4cb1-46a1-ac7c-81d871b842b4` — 'Paid for by Kshama For Congress, not corporate cash.'
    - Not a substantive candidate position — it is a 'paid for by' campaign-finance disclaimer/tagline. The committee name is the entire content, so the identity is load-bearing and cannot be salvaged by editing.
- **Kshama Sawant** · ukraine-support · `13aaf7a5-5c88-4a0d-8f94-9b0115f93049` — 'Kshama is fighting to end all military aid to both the Israeli state and the bloody inter-imperialist proxy war in Ukraine.'
    - Third-person curator/campaign copy about the candidate ('Kshama is fighting...'), not a first-person verbatim statement; cannot be salvaged by editing the blind text.
- **Delia C. Ramirez** · social-security · `864cc1c0-6ef7-43f4-a769-4be3852d0c8c` — "Democrats won't let Social Security and Medicare get cut"
    - The partisan subject 'Democrats' is load-bearing: the entire claim is 'Democrats won't let [X] get cut.' Removing the party tell would erase who is the actor and change the statement's substance, so it cannot be honestly de-identified. Remove.
- **Mark Baisley** · voting-rights · `ff129886-05cb-4a13-af90-8dc89cd8d69b` — "I applaud President Trump's executive order requiring proof of U.S. citizenship to register to vote. This is the very foundation of democracy, and it deserves that level of integrity."
    - The praise of 'President Trump' is load-bearing: the statement is fundamentally 'I applaud President Trump's executive order.' Redacting the named third party/side tell would gut the object of the applause and change the substance, so it cannot be salvaged by marking. Remove.