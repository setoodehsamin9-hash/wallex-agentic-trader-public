"""Deterministic decision validator (ADR-002 steps 1-2). Any failure -> effective action WAIT, with a status.

Checks: output present; snapshot id is the one shown; cited candidate/position/feature ids were all in the input;
the candidate has not expired; the answer arrived before the deadline; the action is enabled by config.

VETO role (Phase 05, `agent.role = "VETO"`): the deterministic choice (`deterministic_choice`, the B0 rule) stands.
The model may confirm it (ENTER that id; any other id is UNKNOWN_REFERENCE -> WAIT) or veto it with WAIT citing only
VETO_REASONS and at least one shown evidence id (not needed for EXECUTION_STATE_UNSAFE, nor for DATA_QUALITY when the
snapshot itself carries quality flags, which have no feature id). Any other WAIT is
VETO_UNSUPPORTED: the deterministic candidate is entered and the model's output stays in the journal. Every failure
(no output, late, unknown reference, expired candidate) is still WAIT; the model never changes a parameter.
"""

from datetime import datetime

from typing import Literal

from wallex_agent.contracts import AgentAction, AgentDecision, ReasonCode, SignalCandidate, ValidatedDecision, ValidationStatus

from .context import DecisionContext


VETO_REASONS = frozenset({ReasonCode.DATA_QUALITY, ReasonCode.REGIME_UNFAVORABLE, ReasonCode.CONFLICTING_SIGNALS,
                          ReasonCode.LOW_LIQUIDITY, ReasonCode.EXECUTION_STATE_UNSAFE})


def deterministic_choice(candidates: tuple[SignalCandidate, ...]) -> SignalCandidate | None:
    """B0 rule (agent/baseline.py): largest target edge after costs, ties by id; candidates without a target tie."""
    def edge(c: SignalCandidate) -> float:
        if c.take_profit_price is None:
            return float("-inf")
        return float((c.take_profit_price - c.reference_price) / c.reference_price * 10000 - c.estimated_round_trip_cost_bps)
    return sorted(candidates, key=lambda c: (-edge(c), c.candidate_id))[0] if candidates else None


def _wait(status: ValidationStatus, decision: AgentDecision | None, detail: str) -> ValidatedDecision:
    return ValidatedDecision(status=status, decision=decision, effective_action=AgentAction.WAIT, detail=detail)


def validate(decision: AgentDecision | None, ctx: DecisionContext, *, received_at: datetime, exit_enabled: bool,
             failure: ValidationStatus = ValidationStatus.INVALID_SCHEMA, detail: str | None = None,
             role: Literal["SELECTOR", "VETO"] = "SELECTOR") -> ValidatedDecision:
    if decision is None:
        return _wait(failure, None, detail or "no valid structured output")
    if received_at > ctx.deadline:
        return _wait(ValidationStatus.STALE, decision, f"answered {received_at.isoformat()} after deadline {ctx.deadline.isoformat()}")
    if decision.snapshot_id != ctx.snapshot.snapshot_id:
        return _wait(ValidationStatus.UNKNOWN_REFERENCE, decision, "snapshot_id not the one provided")
    known_features = {f.feature_id for f in ctx.features.features}
    unknown = [f for f in decision.evidence_feature_ids if f not in known_features]
    if unknown:
        return _wait(ValidationStatus.UNKNOWN_REFERENCE, decision, f"unknown evidence ids {unknown[:3]}")
    chosen = deterministic_choice(ctx.candidates) if role == "VETO" else None
    if role == "VETO" and decision.action is AgentAction.ENTER and (chosen is None or decision.selected_candidate_id != chosen.candidate_id):
        return _wait(ValidationStatus.UNKNOWN_REFERENCE, decision, "VETO role: only the deterministic candidate may be confirmed")
    if role == "VETO" and decision.action is AgentAction.WAIT and chosen is not None:
        permitted = bool(decision.reason_codes) and set(decision.reason_codes) <= VETO_REASONS
        evidenced = (bool(decision.evidence_feature_ids) or ReasonCode.EXECUTION_STATE_UNSAFE in decision.reason_codes
                     or (ReasonCode.DATA_QUALITY in decision.reason_codes and bool(ctx.snapshot.quality_flags)))
        if not (permitted and evidenced):
            if received_at >= chosen.expires_at:
                return _wait(ValidationStatus.STALE, decision, "candidate expired")
            kept = decision.model_copy(update={"action": AgentAction.ENTER, "selected_candidate_id": chosen.candidate_id})
            why = [r.value for r in decision.reason_codes]
            return ValidatedDecision(status=ValidationStatus.VETO_UNSUPPORTED, decision=kept, effective_action=AgentAction.ENTER,
                                     detail=f"veto reasons {why} not permitted or not evidenced; deterministic candidate kept")
    if decision.action is AgentAction.ENTER:
        cand = next((c for c in ctx.candidates if c.candidate_id == decision.selected_candidate_id), None)
        if cand is None:
            return _wait(ValidationStatus.UNKNOWN_REFERENCE, decision, "selected_candidate_id was not offered")
        if received_at >= cand.expires_at:
            return _wait(ValidationStatus.STALE, decision, "candidate expired")
    if decision.action is AgentAction.EXIT_POSITION:
        if not exit_enabled:
            return _wait(ValidationStatus.ACTION_DISABLED, decision, "EXIT_POSITION disabled by config")
        if decision.position_id not in {p.position_id for p in ctx.positions}:
            return _wait(ValidationStatus.UNKNOWN_REFERENCE, decision, "position_id not open")
    return ValidatedDecision(status=ValidationStatus.VALID, decision=decision, effective_action=decision.action)
