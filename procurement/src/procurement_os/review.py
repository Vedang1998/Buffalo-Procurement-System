from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from enum import Enum

class DecisionScope(str,Enum):
    RUN_ONLY="RUN_ONLY"; TEMPORARY="TEMPORARY"; PERMANENT="PERMANENT"

@dataclass(frozen=True)
class ReviewDecision:
    decision_type: str
    action: str
    scope: DecisionScope
    comment: str | None=None
    effective_from: date | None=None
    effective_through: date | None=None
    def validate(self):
        if self.scope==DecisionScope.TEMPORARY and not self.effective_through:
            raise ValueError("TEMPORARY decisions require effective_through")
        if self.scope==DecisionScope.RUN_ONLY and (self.effective_from or self.effective_through):
            raise ValueError("RUN_ONLY decisions cannot carry effective dates")

def writeback_kind(d: ReviewDecision)->str:
    d.validate()
    if d.scope==DecisionScope.RUN_ONLY: return "RUN_DECISION"
    if d.scope==DecisionScope.TEMPORARY: return "MANUAL_OVERRIDE"
    return "POLICY_OR_ALIAS"
