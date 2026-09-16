"""Coverage view data layer for the discovery reorg: a pure level classifier
plus DB aggregation (added in Task 4). Best-effort like gui/discovery.py.

`race_level` is a heuristic over position_name. It is used only for grouping and
is easy to extend; misgrouping a race is cosmetic, never unsafe.
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
