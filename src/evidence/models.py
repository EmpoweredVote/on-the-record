from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class SourceType(str, Enum):
    PRIMARY = "primary"
    POINTER = "pointer"
    SECONDARY_LEAD = "secondary_lead"
    SCORECARD_QUIZ = "scorecard_quiz"
    VOTE_RECORD = "vote_record"
    VIDEO_UNFETCHED = "video_unfetched"
    DEAD = "dead"


class Status(str, Enum):
    GREEN = "green"
    FLAGGED = "flagged"
    DROPPED = "dropped"


@dataclass
class GateResults:
    verbatim: bool
    own_words: Optional[bool] = None
    in_context: Optional[bool] = None
    primary: Optional[bool] = None
    tag_agree: Optional[bool] = None
    judge_tag_ok: Optional[float] = None
    judge_context_sufficient: Optional[float] = None
    judge_dispute_risk: Optional[float] = None


@dataclass
class QuoteCandidate:
    text: str
    context: str
    issue: str
    date: Optional[str] = None
    setting: Optional[str] = None
    is_own_words: bool = True
    is_primary_venue: bool = True
    reported_event: Optional[str] = None   # set when quote is reported from a spoken event
    primary_handle: Optional[str] = None


@dataclass
class CrossCheckVerdict:
    own_words: bool
    in_context: bool
    primary: bool
    tag_agree: bool
    issue: Optional[str] = None
    notes: str = ""


@dataclass
class JudgeScores:
    tag_ok: float
    context_sufficient: float
    dispute_risk: float
    notes: str = ""


def _enumval(v):
    return v.value if isinstance(v, Enum) else v


@dataclass
class EvidenceItem:
    politician_id: str
    issue: str
    evidence_type: str
    verbatim_text: str
    source_url: str
    cited_via: Optional[str]
    context: str
    deep_link: str
    source_type: str
    gates: GateResults
    status: str
    status_reasons: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        d["source_type"] = _enumval(self.source_type)
        d["status"] = _enumval(self.status)
        return d


@dataclass
class Lead:
    politician_id: str
    reported_text: str
    issue: str
    event: str
    secondary_url: str
    primary_handle: Optional[str]
    note: str = "reported by secondary; NOT verified against a primary — chase to confirm"

    def to_json(self) -> dict:
        return asdict(self)
