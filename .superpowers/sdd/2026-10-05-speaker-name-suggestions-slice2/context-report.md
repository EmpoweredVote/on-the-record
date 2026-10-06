# Report: intro + meeting context sent to researcher

## Changes
- spec: privacy rule rewritten; dated note under "Slice 2 decisions".
- src/name_lookup.py: build_prompt(intro=, context=) (400/200 char caps, JSON-quoted, control chars stripped, "exactly as printed" instruction); research() forwards both; ResearchCache.key -> "v2|" + 12-hex sha256 of intro+context.
- src/name_suggest.py: meeting_context(meeting); per-candidate intro = E1 evidence quote; both passed to researcher as keywords and into cache key.
- scripts/eval_name_lookup.py: rows carry intro/context; passed to research() and key; console line unchanged (no intro text printed).
- Tests: 14 new tests across test_name_lookup.py, test_name_suggest.py, test_name_lookup_eval.py; existing fake researchers now accept **kw; v1 -> v2 prefix assertion.

## Commands and output
Red first: collection ImportError (meeting_context) then 3 failures; fixed.

$ .venv/bin/python -m pytest tests/test_name_lookup.py tests/test_name_suggest.py tests/test_name_lookup_eval.py -q -p no:cacheprovider
120 passed in 0.41s

$ .venv/bin/python -m pytest -q -p no:cacheprovider
3244 passed, 8 skipped in 24.24s

## Smoke (one real call)
research("Vicki Venker", None, None, "Bloomington, IN", intro="My name is Vicki Venker. I am the mayor of Bloomington Sibling City, Palo Alto, California ...", context="Regular Session (council; Bloomington Common Council)")
-> {'name': 'Vicki Veenker', 'affiliation': 'Mayor of Palo Alto', 'url': 'https://www.paloalto.gov/News-Articles/City-Manager/Palo-Alto-Council-Selects-2026-Mayor-and-Vice-Mayor'}
