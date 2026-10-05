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
CONFLICT_TIED_NAMES = "tied_names"
CAMPAIGN_KINDS = {"debate", "forum"}
STAFF_CUES = (
    "legislative services agency", "committee counsel", "committee attorney", "fiscal analyst",
    "city staff", "staff attorney", "clerk's office", "legal counsel", "nonpartisan staff",
    "city clerk", "county clerk",
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


def _full_key(name: str) -> str:
    """Full name key: " ".join(significant_tokens(name))."""
    return " ".join(significant_tokens(name))


def _surname(name: str) -> str:
    """Last significant token; fallback to normalize."""
    toks = significant_tokens(name)
    return toks[-1] if toks else normalize(name)


def _norm_title(title: Optional[str]) -> str:
    return (title or "").rstrip(".").lower()


_OFFICE_TITLE_SET = {_norm_title(t) for t in OFFICE_TITLES}


def _is_partial_by_tokens(name: str) -> bool:
    """Partial for grouping: single significant token (e.g., 'Brown', 'Senator Brown')."""
    return len(significant_tokens(name)) == 1


def _tier(kinds: set[str]) -> Optional[str]:
    if "E1" in kinds and ({"E2", "E3"} & kinds):
        return "strong"
    if "E1" in kinds or {"E2", "E3"} <= kinds:
        return "medium"
    if {"E2", "E3"} & kinds:
        return "weak"
    return None


def _best_name(items: list[Evidence]) -> Evidence:
    """Most significant tokens wins; E1 breaks ties."""
    return max(items, key=lambda e: (len(significant_tokens(e.name)), e.kind == "E1"))


def _role(titled: bool, affiliation: Optional[str], e1_quotes: str, event_kind: Optional[str]) -> Optional[str]:
    if event_kind in CAMPAIGN_KINDS:
        return None
    if titled:
        return "official"
    hay = f"{affiliation or ''} {e1_quotes}".lower()
    if any(cue in hay for cue in STAFF_CUES):
        return "staff"
    if affiliation:
        return "presenter"
    return "public_comment"


def build_candidates(evidence: list[Evidence], event_kind: Optional[str] = None) -> dict[str, Candidate]:
    """One Candidate per speaker label that has E1-E3 evidence.

    Groups by full name key (significant tokens), with partial names joining
    only if exactly one full-name group shares their surname.
    """
    # Separate full and partial names (by significant tokens), group full by key
    full_by_label: dict[str, dict[str, list[Evidence]]] = defaultdict(lambda: defaultdict(list))
    partial_by_label: dict[str, list[Evidence]] = defaultdict(list)

    for e in evidence:
        if not significant_tokens(e.name):
            continue  # honorific-only ("Chair"): no usable name, would form an empty-key group
        if _is_partial_by_tokens(e.name):
            partial_by_label[e.label].append(e)
        else:
            full_by_label[e.label][_full_key(e.name)].append(e)

    # Build candidates from full-name groups
    out: dict[str, Candidate] = {}
    for label, full_groups in full_by_label.items():
        scored = []
        for key, items in full_groups.items():
            tier = _tier({e.kind for e in items})
            if tier:
                scored.append((TIER_RANK[tier], len(items), key, tier, items))

        # Attach partial names to groups: join if exactly one full group with that surname exists
        partial_items = partial_by_label.get(label, [])
        for p in partial_items:
            p_surname = _surname(p.name)
            matching_groups = [k for k in full_groups.keys() if _surname(k) == p_surname]
            if len(matching_groups) == 1:
                # Exactly one match: add to that group
                matching_key = matching_groups[0]
                full_groups[matching_key].append(p)
                # Recalculate tier for this group
                items = full_groups[matching_key]
                tier = _tier({e.kind for e in items})
                # Remove old entry and add new
                scored = [(r, c, k, t, it) for (r, c, k, t, it) in scored if k != matching_key]
                if tier:
                    scored.append((TIER_RANK[tier], len(items), matching_key, tier, items))
            elif len(matching_groups) == 0:
                # No full-name groups with this surname: partial forms its own weak group
                tier = _tier({p.kind})
                if tier:
                    scored.append((TIER_RANK[tier], 1, _surname(p.name), tier, [p]))
            # else: 2+ full-name groups share the surname; partial is ambiguous and stays out

        if not scored:
            continue

        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        _, _, _, tier, items = scored[0]
        best = _best_name(items)
        title = next((e.title for e in items if e.title), None)
        affiliation = next((e.affiliation for e in items if e.kind == "E1" and e.affiliation), None)
        titled = _norm_title(title) in _OFFICE_TITLE_SET

        # Gather only E1 quotes for role inference
        e1_quotes = " ".join(e.quote for e in items if e.kind == "E1")

        cand = Candidate(
            label=label, name=best.name, tier=tier,
            role=_role(titled, affiliation, e1_quotes, event_kind),
            titled=titled, partial=len(significant_tokens(best.name)) < 2, affiliation=affiliation,
            evidence=[e for g in full_groups.values() for e in g] + partial_items,
        )

        # X5: conflict if 2+ full-name groups at medium or better
        full_name_groups_scored = [s for s in scored if s[3] in ("strong", "medium")]
        if len(full_name_groups_scored) >= 2:
            cand.conflict = CONFLICT_TWO_NAMES
        elif len(scored) >= 2 and scored[0][0] == scored[1][0]:
            cand.conflict = CONFLICT_TIED_NAMES  # distinct names tie at the top tier

        out[label] = cand

    # Handle labels with only partial names (group by surname)
    for label, partial_items in partial_by_label.items():
        if label in out:
            continue  # Already processed

        # Group partial items by surname
        by_surname: dict[str, list[Evidence]] = defaultdict(list)
        for p in partial_items:
            by_surname[_surname(p.name)].append(p)

        scored = []
        for surname, items in by_surname.items():
            tier = _tier({e.kind for e in items})
            if tier:
                scored.append((TIER_RANK[tier], len(items), surname, tier, items))

        if not scored:
            continue
        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        _, _, _, tier, items = scored[0]
        best = _best_name(items)
        title = next((e.title for e in items if e.title), None)
        affiliation = next((e.affiliation for e in items if e.kind == "E1" and e.affiliation), None)
        titled = _norm_title(title) in _OFFICE_TITLE_SET
        e1_quotes = " ".join(e.quote for e in items if e.kind == "E1")
        cand = Candidate(
            label=label, name=best.name, tier=tier,
            role=_role(titled, affiliation, e1_quotes, event_kind),
            titled=titled, partial=len(significant_tokens(best.name)) < 2, affiliation=affiliation,
            evidence=partial_items,
        )
        if len(scored) >= 2 and scored[0][0] == scored[1][0]:
            cand.conflict = CONFLICT_TIED_NAMES
        out[label] = cand

    # X4: same full key on two labels → conflict
    by_full_key: dict[str, list[str]] = defaultdict(list)
    for label, cand in out.items():
        if cand.name and not cand.partial:
            by_full_key[_full_key(cand.name)].append(label)
    for labels in by_full_key.values():
        if len(labels) > 1:
            for label in labels:
                out[label].conflict = out[label].conflict or CONFLICT_TWO_LABELS

    return out
