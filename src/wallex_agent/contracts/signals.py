"""Deterministic strategy output. The AI chooses among these; it never invents one."""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import SCHEMA_VERSION, Contract, UtcDatetime


class CandidateKind(StrEnum):
    ENTRY = "ENTRY"


class StrategyBrief(Contract):
    """Versioned, deterministic description of the ACTIVE strategy handed to the decision agent (Phase 05). It tells the
    model which hypothesis the candidate was generated under, which evidence is relevant, what would invalidate it and
    which criteria do NOT apply. It carries no tradable parameter the model could change."""

    brief_version: str
    strategy_id: str
    strategy_version: str
    timeframe: str
    hypothesis: str
    entry_rule: str
    exit_rule: str
    regime_interpretation: str
    evidence_feature_ids: tuple[str, ...]
    invalidating_evidence: tuple[str, ...]
    not_applicable: tuple[str, ...]
    candidate_reason: str


class SignalCandidate(Contract):
    """A bounded, fully specified trade opportunity. Spot, long-only entries in TMN markets.

    Exit parameters are fixed here by the strategy, so neither the AI nor a human approval can
    loosen them afterwards.
    """

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    candidate_id: str
    snapshot_id: str
    strategy_id: str
    """e.g. 'B0_breakout_rule', 'C1_breakout'."""
    strategy_version: str
    kind: CandidateKind = CandidateKind.ENTRY
    symbol: str
    side: Literal["BUY"] = "BUY"
    reference_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    take_profit_price: Decimal | None = None
    max_holding_seconds: int = Field(gt=0)
    trail_distance: Decimal | None = Field(default=None, gt=0)
    """Chandelier trail in TMN (fixed at entry): stop = max(stop_price, highest high since entry - trail). Only
    ever tightens the approved stop; None = fixed stop."""
    exit_policy_id: str
    estimated_round_trip_cost_bps: Decimal = Field(ge=0)
    evidence_feature_ids: tuple[str, ...]
    created_at: UtcDatetime
    expires_at: UtcDatetime

    @model_validator(mode="after")
    def _check_geometry(self) -> "SignalCandidate":
        if self.stop_price >= self.reference_price:
            raise ValueError("long entry requires stop_price < reference_price")
        if self.take_profit_price is not None and self.take_profit_price <= self.reference_price:
            raise ValueError("long entry requires take_profit_price > reference_price")
        if self.expires_at <= self.created_at:
            raise ValueError("candidate must expire after it is created")
        return self
