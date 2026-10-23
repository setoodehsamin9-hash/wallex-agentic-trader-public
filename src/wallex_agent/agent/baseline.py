"""B0 — deterministic baseline selector over the SAME candidates the AI sees (Phase 01 ablation definition).

Rule: take the candidate with the largest target edge after estimated costs; ties by candidate_id. No regime
judgement, no waiting when a valid candidate exists. It is the non-AI benchmark, never presented as the AI.
"""

from wallex_agent.contracts import (
    AgentAction,
    AgentDecision,
    Confidence,
    DecisionProvenance,
    DecisionSource,
    ReasonCode,
    RiskPosture,
    ValidatedDecision,
    SignalCandidate,
    ValidationStatus,
)

from .context import DecisionContext


def _edge_bps(c: SignalCandidate) -> float:
    if c.take_profit_price is None:
        return float("-inf")
    return float((c.take_profit_price - c.reference_price) / c.reference_price * 10000 - c.estimated_round_trip_cost_bps)


class BaselineSelector:
    source_name = "BASELINE"

    def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
        now = ctx.now
        if ctx.candidates:
            best = sorted(ctx.candidates, key=lambda c: (-_edge_bps(c), c.candidate_id))[0]
            decision = AgentDecision(snapshot_id=ctx.snapshot.snapshot_id, action=AgentAction.ENTER,
                                     selected_candidate_id=best.candidate_id, regime_assessment=ctx.features.deterministic_regime,
                                     confidence=Confidence.MEDIUM, risk_posture=RiskPosture.NORMAL,
                                     reason_codes=(ReasonCode.BEST_OF_CANDIDATES,), evidence_feature_ids=best.evidence_feature_ids,
                                     brief_rationale="B0 rule: highest target edge after estimated costs.")
        else:
            decision = AgentDecision(snapshot_id=ctx.snapshot.snapshot_id, action=AgentAction.WAIT,
                                     regime_assessment=ctx.features.deterministic_regime, confidence=Confidence.MEDIUM,
                                     reason_codes=(ReasonCode.NO_CANDIDATES,), brief_rationale="B0 rule: no candidate.")
        validated = ValidatedDecision(status=ValidationStatus.VALID, decision=decision, effective_action=decision.action)
        prov = DecisionProvenance(source=DecisionSource.BASELINE, model_id=None, model_settings_sha256=None, prompt_version=None,
                                  prompt_sha256=None, model_requests=0, latency_ms=0, raw_output_ref=None,
                                  started_at=now, finished_at=now)
        return validated, prov
