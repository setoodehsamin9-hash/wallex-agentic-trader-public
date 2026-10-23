from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from wallex_agent.contracts import (
    AgentAction,
    AgentDecision,
    Confidence,
    ReasonCode,
    RegimeLabel,
    RiskOutcome,
    RiskVerdict,
    SignalCandidate,
)

T0 = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def _decision(**overrides: object) -> AgentDecision:
    base: dict[str, object] = {
        "snapshot_id": "snap-1",
        "action": AgentAction.WAIT,
        "regime_assessment": RegimeLabel.UNCERTAIN,
        "confidence": Confidence.LOW,
        "reason_codes": (ReasonCode.CONFLICTING_SIGNALS,),
        "brief_rationale": "breakout margin below cost threshold",
    }
    base.update(overrides)
    return AgentDecision.model_validate(base)


def test_wait_decision_is_valid() -> None:
    assert _decision().action is AgentAction.WAIT


def test_enter_requires_candidate() -> None:
    with pytest.raises(ValidationError):
        _decision(action=AgentAction.ENTER)
    assert _decision(action=AgentAction.ENTER, selected_candidate_id="c1").selected_candidate_id == "c1"


def test_wait_cannot_smuggle_a_candidate() -> None:
    with pytest.raises(ValidationError):
        _decision(selected_candidate_id="c1")


def test_model_cannot_add_size_or_price_fields() -> None:
    for field in ("qty", "price", "stop_price", "symbol", "risk_budget"):
        with pytest.raises(ValidationError):
            _decision(action=AgentAction.ENTER, selected_candidate_id="c1", **{field: "1"})


def test_reason_codes_bounded() -> None:
    with pytest.raises(ValidationError):
        _decision(reason_codes=())
    with pytest.raises(ValidationError):
        _decision(reason_codes=("MADE_UP_CODE",))


def test_contracts_are_frozen() -> None:
    decision = _decision()
    with pytest.raises(ValidationError):
        decision.action = AgentAction.ENTER  # type: ignore[misc]


def _candidate(**overrides: object) -> SignalCandidate:
    base: dict[str, object] = {
        "candidate_id": "c1",
        "snapshot_id": "snap-1",
        "strategy_id": "C1_breakout",
        "strategy_version": "0.1.0",
        "symbol": "BTCTMN",
        "reference_price": Decimal("22123973858"),
        "stop_price": Decimal("21900000000"),
        "take_profit_price": Decimal("22500000000"),
        "max_holding_seconds": 3600,
        "exit_policy_id": "triple-barrier-v1",
        "estimated_round_trip_cost_bps": Decimal("45"),
        "evidence_feature_ids": ("BTCTMN.breakout_margin_atr.20",),
        "created_at": T0,
        "expires_at": T0 + timedelta(minutes=2),
    }
    base.update(overrides)
    return SignalCandidate.model_validate(base)


def test_candidate_geometry_enforced() -> None:
    assert _candidate().side == "BUY"
    with pytest.raises(ValidationError):
        _candidate(stop_price=Decimal("22200000000"))
    with pytest.raises(ValidationError):
        _candidate(take_profit_price=Decimal("22000000000"))


def test_naive_and_non_utc_timestamps_rejected() -> None:
    with pytest.raises(ValidationError):
        _candidate(created_at=datetime(2026, 10, 22, 9, 0), expires_at=datetime(2026, 10, 22, 9, 2))
    tehran = timezone(timedelta(hours=3, minutes=30))
    with pytest.raises(ValidationError):
        _candidate(created_at=T0.astimezone(tehran))


def test_reject_verdict_carries_no_quantity() -> None:
    common: dict[str, object] = {
        "verdict_id": "v1",
        "intent_hash": "h",
        "checks": (),
        "limits_version": "limits-0",
        "evaluated_at": T0,
        "expires_at": T0 + timedelta(seconds=30),
    }
    assert RiskVerdict.model_validate({**common, "outcome": RiskOutcome.REJECT}).approved_qty is None
    with pytest.raises(ValidationError):
        RiskVerdict.model_validate({**common, "outcome": RiskOutcome.REJECT, "approved_qty": Decimal("0.1")})
    with pytest.raises(ValidationError):
        RiskVerdict.model_validate({**common, "outcome": RiskOutcome.APPROVE})
