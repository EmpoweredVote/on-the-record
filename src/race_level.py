"""Race-level classifier over position_name.

Pure heuristic used to group races and to gate local-type hub searches. It is
easy to extend; misgrouping a race is cosmetic for the coverage view and, for
the hub gate, only widens/narrows which speculative local searches run.
"""
from __future__ import annotations

import re

_FEDERAL = re.compile(r"\b(president|u\.?s\.?|united states|congress)\b", re.I)
_STATE = re.compile(
    r"\b(governor|lieutenant governor|attorney general|secretary of state|"
    r"state\s+(?:senat\w*|represent\w*|assembly|house)|comptroller|treasurer|"
    r"superintendent)\b", re.I)
_SCHOOL = re.compile(r"\b(school board|board of education|school district)\b", re.I)
_COUNTY = re.compile(r"\bcounty\b", re.I)

LEVEL_ORDER = ("federal", "state", "county", "local", "school")


def race_level(position_name: "str | None") -> str:
    name = position_name or ""
    if _FEDERAL.search(name):
        return "federal"
    if _STATE.search(name):
        return "state"
    if _SCHOOL.search(name):
        return "school"
    if _COUNTY.search(name):
        return "county"
    return "local"
