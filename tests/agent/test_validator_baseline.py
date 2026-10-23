from datetime import UTC, datetime, timedelta
from decimal import Decimal

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.agent.context import DecisionContext
from wallex_agent.agent.validator import validate
from wallex_agent.contracts import (
    AgentAction,
    AgentDecision,
    Confidence,
    DataRef,
    DecisionSource,
    FeatureSet,
    FeatureValue,
    MarketMeta,
    MarketSnapshot,
    ReasonCode,
    RegimeLabel,
    SignalCandidate,
    ValidationStatus,
)

T = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
META = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=6, min_qty=Decimal("0.000001"),
                  min_notional=Decimal(50000), market_order_enabled=True, metadata_version="t")
SNAP = MarketSnapshot(snapshot_id="snap", symbol="BTCTMN", as_of=T, bar_interval_seconds=900,
                      last_closed_bar_open=T - timedelta(seconds=900),
                      candles=DataRef(dataset_id="d", content_sha256="0" * 64, source="FIXTURE", symbol="BTCTMN", first_ts=T,
                                      last_ts=T, resolution_seconds=900), book=None, meta=META, universe_version="u")
FS = FeatureSet(snapshot_id="snap", feature_engine_version="f", deterministic_regime=RegimeLabel.TRENDING_UP,
                features=(FeatureValue(feature_id="BTCTMN.breakout_margin_atr.16", name="breakout_margin_atr", value=Decimal("0.8"),
                                       unit="ATR", window_bars=16),))


def _cand(cid: str, tp: str) -> SignalCandidate:
    return SignalCandidate(candidate_id=cid, snapshot_id="snap", strategy_id="C1_breakout", strategy_version="v", symbol="BTCTMN",
                           reference_price=Decimal(100), stop_price=Decimal(95), take_profit_price=Decimal(tp),
                           max_holding_seconds=3600, exit_policy_id="tb", estimated_round_trip_cost_bps=Decimal(60),
                           evidence_feature_ids=("BTCTMN.breakout_margin_atr.16",), created_at=T, expires_at=T + timedelta(minutes=10))


def _dec(**kw: object) -> AgentDecision:
    base: dict[str, object] = {"snapshot_id": "snap", "action": AgentAction.ENTER, "selected_candidate_id": "c1",
                               "regime_assessment": RegimeLabel.TRENDING_UP, "confidence": Confidence.MEDIUM,
                               "reason_codes": (ReasonCode.BREAKOUT_CONFIRMED,), "brief_rationale": "ok",
                               "evidence_feature_ids": ("BTCTMN.breakout_margin_atr.16",)}
    base.update(kw)
    return AgentDecision.model_validate(base)


def _ctx() -> DecisionContext:
    return DecisionContext(snapshot=SNAP, features=FS, candidates=(_cand("c1", "110"), _cand("c2", "120")), positions=(),
                           risk_status={}, recent_decisions=(), now=T, deadline=T + timedelta(seconds=60))


def test_valid_entry_passes() -> None:
    v = validate(_dec(), _ctx(), received_at=T + timedelta(seconds=5), exit_enabled=False)
    assert v.status is ValidationStatus.VALID and v.effective_action is AgentAction.ENTER


def test_unknown_candidate_id_waits() -> None:
    v = validate(_dec(selected_candidate_id="c-invented"), _ctx(), received_at=T, exit_enabled=False)
    assert v.status is ValidationStatus.UNKNOWN_REFERENCE and v.effective_action is AgentAction.WAIT


def test_wrong_snapshot_or_unknown_evidence_waits() -> None:
    assert validate(_dec(snapshot_id="other"), _ctx(), received_at=T, exit_enabled=False).status is ValidationStatus.UNKNOWN_REFERENCE
    v = validate(_dec(evidence_feature_ids=("made.up.1",)), _ctx(), received_at=T, exit_enabled=False)
    assert v.status is ValidationStatus.UNKNOWN_REFERENCE and v.effective_action is AgentAction.WAIT


def test_late_answer_is_stale_wait() -> None:
    v = validate(_dec(), _ctx(), received_at=T + timedelta(seconds=61), exit_enabled=False)
    assert v.status is ValidationStatus.STALE and v.effective_action is AgentAction.WAIT


def test_missing_output_maps_to_given_failure_status() -> None:
    v = validate(None, _ctx(), received_at=T, exit_enabled=False, failure=ValidationStatus.PROVIDER_ERROR, detail="boom")
    assert v.status is ValidationStatus.PROVIDER_ERROR and v.effective_action is AgentAction.WAIT


def test_exit_position_disabled_by_default() -> None:
    d = _dec(action=AgentAction.EXIT_POSITION, selected_candidate_id=None, position_id="pos-1")
    v = validate(d, _ctx(), received_at=T, exit_enabled=False)
    assert v.status is ValidationStatus.ACTION_DISABLED and v.effective_action is AgentAction.WAIT


def test_wait_is_always_valid() -> None:
    d = _dec(action=AgentAction.WAIT, selected_candidate_id=None, reason_codes=(ReasonCode.WEAK_EVIDENCE,))
    assert validate(d, _ctx(), received_at=T, exit_enabled=False).effective_action is AgentAction.WAIT


def test_baseline_picks_highest_edge_candidate_deterministically() -> None:
    sel = BaselineSelector()
    v, prov = sel.decide(_ctx())
    assert v.effective_action is AgentAction.ENTER and v.decision is not None and v.decision.selected_candidate_id == "c2"
    assert prov.source is DecisionSource.BASELINE and prov.model_id is None
    assert sel.decide(_ctx())[0] == v
