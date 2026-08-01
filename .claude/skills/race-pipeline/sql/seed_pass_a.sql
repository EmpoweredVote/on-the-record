with cov as (
  select rc.race_id,
         count(distinct q.politician_id) filter (where q.readrank_selected) as quoted_cands
  from essentials.race_candidates rc
  join essentials.quotes q on q.politician_id = rc.politician_id
  group by rc.race_id
),
cand as (
  select race_id,
         count(*) filter (where coalesce(candidate_status,'active')
                          not in ('withdrawn','removed')) as active_cands
  from essentials.race_candidates group by race_id
),
base as (
  select r.id as race_id, r.position_name, e.state, e.election_date, e.election_type,
    case
      when e.state='IN' and r.position_name ~* '(monroe|bloomington)' then 'local_bloomington'
      when e.state='CA' and r.position_name ~* '(los angeles|\mLA\M|LAUSD)' then 'local_la'
      when r.position_name ~* 'governor' and r.position_name !~* 'lieutenant' then 'governor'
      when r.position_name ~* '(u\.?s\.?|united states).*(senate|senator)' then 'us_senate'
      when r.position_name ~* '(u\.?s\.?|united states).*(represent|house)'
        or r.position_name ~* 'congressional district' then 'us_house'
    end as office_category,
    coalesce(cov.quoted_cands,0) as qc,
    coalesce(cand.active_cands,0) as ac
  from essentials.races r
  join essentials.elections e on e.id = r.election_id
  left join cov  on cov.race_id  = r.id
  left join cand on cand.race_id = r.id
  where e.election_date >= current_date          -- past primaries are done; skip
    and e.election_date <= '2026-11-03'
)
insert into essentials.readrank_race_pipeline
  (race_id, race_label, state, office_category, election_date, election_kind,
   priority_tier, status, quoted_candidates)
select race_id,
  position_name || ' (' || state || ', ' || to_char(election_date,'YYYY-MM-DD') || ')',
  state, office_category, election_date,
  case when election_type = 'primary' then 'primary' else 'general' end,
  case
    when election_type = 'primary' then 1                                -- imminent primary
    when office_category in ('local_bloomington','local_la') then 2
    when office_category = 'governor' then 3
    when office_category = 'us_senate' then 4
    when office_category = 'us_house' and qc >= 2 then 5                 -- provably contested
    when office_category = 'us_house' then 6                             -- until lane 5 classifies
  end,
  case
    when qc >= 2 then 'published'    -- live quotes exist; goes straight to audit
    when ac >= 2 then 'needs_quotes'
    else 'needs_roster'
  end,
  qc
from base
where office_category is not null
on conflict (race_id) do nothing;
