"""Per-speaker name candidates from Evidence (tiers, conflicts, role guess).

Pure. Implements tiers, X4, X5 and the X6 "titled" flag of
docs/superpowers/specs/2026-10-02-speaker-name-suggestions-design.md.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .name_evidence import OFFICE_TITLES, Evidence
from .name_matching import normalize, significant_tokens

TIER_RANK = {"strong": 3, "medium": 2, "weak": 1}
CONFLICT_TWO_LABELS = "name_on_two_labels"
CONFLICT_TWO_NAMES = "two_names_one_label"
CAMPAIGN_KINDS = {"debate", "forum"}
STAFF_CUES = (
    "legislative services agency", "committee counsel", "committee attorney", "fiscal analyst",
    "city staff", "staff attorney", "clerk's office", "legal counsel", "nonpartisan staff",
)


@dataclass
class Candidate:
    label: str
    name: Optional[str]
    tier: Optional[str]
    role: Optional[str]
    titled: bool
    partial: bool
    affiliation: Optional[str]
    evidence: list[Evidence] = field(default_factory=list)
    conflict: Optional[str] = None

    @property
    def prefill_name(self) -> Optional[str]:
        """The name review/eval may pre-fill; None for conflicts and partial names."""
        if self.conflict or self.partial or not self.name:
            return None
        return self.name


def _surname_key(name: str) -> str:
    toks = significant_tokens(name)
    return toks[-1] if toks else normalize(name)


def _tier(kinds: set[str]) -> Optional[str]:
    if "E1" in kinds and ({"E2", "E3"} & kinds):
        return "strong"
    if "E1" in kinds or {"E2", "E3"} <= kinds:
        return "medium"
    if {"E2", "E3"} & kinds:
        return "weak"
    return None


def _best_name(items: list[Evidence]) -> Evidence:
    """Most tokens wins; E1 breaks ties."""
    return max(items, key=lambda e: (len(e.name.split()), e.kind == "E1"))


def _role(titled: bool, affiliation: Optional[str], quotes: str, event_kind: Optional[str]) -> Optional[str]:
    if event_kind in CAMPAIGN_KINDS:
        return None
    if titled:
        return "official"
    hay = f"{affiliation or ''} {quotes}".lower()
    if any(cue in hay for cue in STAFF_CUES):
        return "staff"
    if affiliation:
        return "presenter"
    return "public_comment"


def build_candidates(evidence: list[Evidence], event_kind: Optional[str] = None) -> dict[str, Candidate]:
    """One Candidate per speaker label that has E1-E3 evidence."""
    by_label: dict[str, dict[str, list[Evidence]]] = defaultdict(lambda: defaultdict(list))
    for e in evidence:
        by_label[e.label][_surname_key(e.name)].append(e)

    out: dict[str, Candidate] = {}
    for label, groups in by_label.items():
        scored = []
        for key, items in groups.items():
            tier = _tier({e.kind for e in items})
            if tier:
                scored.append((TIER_RANK[tier], len(items), key, tier, items))
        if not scored:
            continue
        scored.sort(reverse=True)
        _, _, _, tier, items = scored[0]
        best = _best_name(items)
        title = next((e.title for e in items if e.title), None)
        affiliation = next((e.affiliation for e in items if e.kind == "E1" and e.affiliation), None)
        titled = title in OFFICE_TITLES
        cand = Candidate(
            label=label, name=best.name, tier=tier,
            role=_role(titled, affiliation, " ".join(e.quote for e in items), event_kind),
            titled=titled, partial=best.partial, affiliation=affiliation,
            evidence=[e for g in groups.values() for e in g],
        )
        strong_or_medium = [s for s in scored if s[0] >= TIER_RANK["medium"]]
        if len(strong_or_medium) >= 2:  # X5
            cand.conflict = CONFLICT_TWO_NAMES
        out[label] = cand

    by_name: dict[str, list[str]] = defaultdict(list)  # X4
    for label, cand in out.items():
        if cand.name and not cand.partial:
            by_name[normalize(cand.name)].append(label)
    for labels in by_name.values():
        if len(labels) > 1:
            for label in labels:
                out[label].conflict = out[label].conflict or CONFLICT_TWO_LABELS
    return out
