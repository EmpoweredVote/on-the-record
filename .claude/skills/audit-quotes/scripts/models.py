# .claude/skills/audit-quotes/scripts/models.py
"""Shared types/constants for the audit-quotes skill."""
from dataclasses import dataclass, asdict
from typing import Optional

SEVERITIES = ("high", "medium", "low")
FIX_CLASSES = ("mechanical", "guided", "decision-required")
LEVELS = ("quote", "topic", "portfolio")

@dataclass
class Finding:
    check_id: str          # e.g. "note-missing"
    level: str             # quote | topic | portfolio
    principle: str         # short human phrase, e.g. "editor_note required"
    severity: str          # high | medium | low
    fix_class: str         # mechanical | guided | decision-required
    what: str              # what's wrong, human-readable
    suggested_fix: str     # human-readable proposed fix
    quote_id: Optional[str] = None
    topic_key: Optional[str] = None
    race_id: Optional[str] = None
    candidate: Optional[str] = None
    fix_op: Optional[dict] = None   # mechanical fixes only

    def __post_init__(self):
        assert self.severity in SEVERITIES, self.severity
        assert self.fix_class in FIX_CLASSES, self.fix_class
        assert self.level in LEVELS, self.level

    def to_dict(self):
        return asdict(self)
