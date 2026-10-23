"""F2-aware agent (Phase 05 final pre-LIVE): versioned strategy brief, strategy-relevant evidence only, and a bounded
VETO role in which the deterministic F2 candidate stands unless the model vetoes for a permitted, evidenced reason.

Pinned rules: the brief never exposes breakout/take-profit/volume criteria for F2; contexts without a brief render
exactly as before; in the VETO role the model can only confirm the deterministic candidate or veto with reasons from
VETO_REASONS citing evidence; any other WAIT keeps the deterministic candidate (VETO_UNSUPPORTED); every model
failure is still WAIT; the model can never pick another candidate or change parameters.
"""

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from wallex_agent.agent.context import DecisionContext
from wallex_agent.agent.decision_agent import load_prompt, render_user_prompt
from wallex_agent.agent.validator import VETO_REASONS, validate
from wallex_agent.contracts import (
    AgentAction,
    AgentDecision,
    Confidence,
    DataRef,
    FeatureSet,
    FeatureValue,
    MarketMeta,
    MarketSnapshot,
    ReasonCode,
    RegimeLabel,
    SignalCandidate,
    StrategyBrief,
    ValidationStatus,
)
from wallex_agent.runtime.config import CostModel, ExecutionSettings, F2Params, FeatureSettings
from wallex_agent.strategies.f2_tsm import F2Momentum

T = datetime(2026, 10, 23, 8, 0, tzinfo=UTC)
META = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=6, min_qty=Decimal("0.000001"),
                  min_notional=Decimal(50000), market_order_enabled=True, metadata_version="t")
FS_CFG = FeatureSettings(atr_bars=14, trend_bars=24, volume_bars=20, regime_trend_atr=Decimal("1"), regime_shock_atr_pct=Decimal("0.02"),
                         ema_bars=48, regime_er_bars=42, regime_rank_bars=1080)
F2P = F2Params(version="f2-0.1.0", entry_band_atr=Decimal("0.5"), stop_atr=Decimal("5"), trail_atr=Decimal("5"), max_holding_s=604800,
               candidate_ttl_s=900, regime_filter=True)
F2 = F2Momentum(F2P, CostModel(fee_rate=Decimal("0.0025"), backtest_slippage_bps=Decimal(5)),
                ExecutionSettings(entry_limit_slippage_bps=Decimal(20), exit_limit_slippage_bps=Decimal(30), entry_ttl_s=900), FS_CFG)
SNAP = MarketSnapshot(snapshot_id="snap", symbol="BTCTMN", as_of=T, bar_interval_seconds=14400, last_closed_bar_open=T - timedelta(hours=4),
                      candles=DataRef(dataset_id="d", content_sha256="0" * 64, source="FIXTURE", symbol="BTCTMN", first_ts=T,
                                      last_ts=T, resolution_seconds=14400), book=None, meta=META, universe_version="u")


def _fv(fid: str, v: str | None) -> FeatureValue:
    return FeatureValue(feature_id=f"BTCTMN.{fid}", name=fid.split(".")[0], value=None if v is None else Decimal(v), unit="", window_bars=0)


FEATS = FeatureSet(snapshot_id="snap", feature_engine_version="features-1.1.0", deterministic_regime=RegimeLabel.RANGE, features=(
    _fv("close.1", "110"), _fv("atr.14", "4"), _fv("ema.48", "100"), _fv("close_lag.48", "105"), _fv("er_rank.42", "0.26"),
    _fv("vol_rank.14", "0.46"), _fv("close_lag.42", "104"), _fv("spread_bps.0", None), _fv("prior_high.48", "116"),
    _fv("breakout_margin_atr.48", "-1.6"), _fv("volume_ratio.20", "0.57"), _fv("sma.24", "108"), _fv("trend_dist_atr.24", "0.5")))
CAND = SignalCandidate(candidate_id="f2-a", snapshot_id="snap", strategy_id="F2_tsm", strategy_version="f2-0.1.0", symbol="BTCTMN",
                       reference_price=Decimal(110), stop_price=Decimal(90), take_profit_price=None, max_holding_seconds=604800,
                       trail_distance=Decimal(20), exit_policy_id="chandelier-signal-v1", estimated_round_trip_cost_bps=Decimal(60),
                       evidence_feature_ids=("BTCTMN.ema.48", "BTCTMN.close_lag.48", "BTCTMN.atr.14"), created_at=T,
                       expires_at=T + timedelta(minutes=15))


def _ctx(brief: StrategyBrief | None = None, candidates: tuple[SignalCandidate, ...] = (CAND,)) -> DecisionContext:
    return DecisionContext(snapshot=SNAP, features=FEATS, candidates=candidates, positions=(), risk_status={"kill_switch": False},
                           recent_decisions=(), now=T, deadline=T + timedelta(seconds=90), strategy_brief=brief)


def _dec(**kw: object) -> AgentDecision:
    base: dict[str, object] = {"snapshot_id": "snap", "action": AgentAction.ENTER, "selected_candidate_id": "f2-a",
                               "regime_assessment": RegimeLabel.RANGE, "confidence": Confidence.MEDIUM,
                               "reason_codes": (ReasonCode.TREND_CONFIRMED,), "brief_rationale": "ok",
                               "evidence_feature_ids": ("BTCTMN.ema.48",)}
    base.update(kw)
    return AgentDecision.model_validate(base)


# ------------------------------------------------------------------------------------------------ the brief
def test_f2_brief_states_its_hypothesis_and_disowns_breakout_criteria() -> None:
    b = F2.brief("BTCTMN", 14400)
    assert b.strategy_id == "F2_tsm" and b.strategy_version == "f2-0.1.0" and b.brief_version.startswith("f2-brief-")
    assert set(b.evidence_feature_ids) == {"BTCTMN.close.1", "BTCTMN.ema.48", "BTCTMN.close_lag.48", "BTCTMN.atr.14",
                                           "BTCTMN.spread_bps.0", "BTCTMN.vol_rank.14", "BTCTMN.er_rank.42", "BTCTMN.close_lag.42"}
    text = " ".join(b.not_applicable).lower()
    assert "breakout" in text and "take-profit" in text and "volume" in text
    assert "4h" in b.timeframe and "5 ATR" in b.exit_rule and b.invalidating_evidence


def test_prompt_with_brief_shows_only_strategy_evidence_and_the_exit_design() -> None:
    body = json.loads(render_user_prompt(_ctx(F2.brief("BTCTMN", 14400))).split("\n", 1)[1])
    shown = {f["feature_id"] for f in body["FEATURES"]}
    assert "BTCTMN.prior_high.48" not in shown and "BTCTMN.breakout_margin_atr.48" not in shown
    assert "BTCTMN.volume_ratio.20" not in shown and "BTCTMN.sma.24" not in shown and "BTCTMN.ema.48" in shown
    [c] = body["CANDIDATES"]
    assert "target_edge_bps" not in c and "take_profit_price" not in c
    assert c["trail_distance"] == "20" and c["stop_distance_atr"] == "5.00" and c["exit_policy_id"] == "chandelier-signal-v1"
    assert body["STRATEGY"]["strategy_id"] == "F2_tsm" and "deterministic_regime" not in body
    assert body["EXECUTION_STATE"] == {"kill_switch": False}


def test_prompt_without_brief_is_unchanged() -> None:
    body = json.loads(render_user_prompt(_ctx(None)).split("\n", 1)[1])
    assert set(body) == {"snapshot_id", "symbol", "as_of_utc", "bar_interval_minutes", "deterministic_regime", "book_top",
                         "FEATURES", "CANDIDATES", "open_positions"}
    assert "BTCTMN.prior_high.48" in {f["feature_id"] for f in body["FEATURES"]}


def test_strategy_aware_prompts_exist_and_are_versioned() -> None:
    sel, sel_hash = load_prompt("decision_v3")
    veto, veto_hash = load_prompt("veto_v1")
    assert "STRATEGY" in sel and "STRATEGY" in veto and sel_hash != veto_hash
    assert "not_applicable" in sel and "VETO" in veto


# ------------------------------------------------------------------------------------------ the VETO role
def test_veto_role_confirming_keeps_the_deterministic_candidate() -> None:
    v = validate(_dec(), _ctx(), received_at=T, exit_enabled=False, role="VETO")
    assert v.status is ValidationStatus.VALID and v.effective_action is AgentAction.ENTER


def test_veto_role_honours_a_permitted_evidenced_veto() -> None:
    d = _dec(action=AgentAction.WAIT, selected_candidate_id=None, reason_codes=(ReasonCode.REGIME_UNFAVORABLE,),
             evidence_feature_ids=("BTCTMN.vol_rank.14",))
    v = validate(d, _ctx(), received_at=T, exit_enabled=False, role="VETO")
    assert v.status is ValidationStatus.VALID and v.effective_action is AgentAction.WAIT
    e = _dec(action=AgentAction.WAIT, selected_candidate_id=None, reason_codes=(ReasonCode.EXECUTION_STATE_UNSAFE,),
             evidence_feature_ids=())
    assert validate(e, _ctx(), received_at=T, exit_enabled=False, role="VETO").effective_action is AgentAction.WAIT
    assert ReasonCode.WEAK_EVIDENCE not in VETO_REASONS and ReasonCode.MOMENTUM_FADING not in VETO_REASONS


def test_veto_role_accepts_a_data_quality_veto_on_snapshot_flags_without_feature_ids() -> None:
    # found by the fresh evaluation: snapshot-level quality flags have no feature id the model could cite
    from wallex_agent.contracts import QualityFlag

    flagged = dataclasses.replace(_ctx(), snapshot=SNAP.model_copy(update={"quality_flags": frozenset({QualityFlag.STALE})}))
    d = _dec(action=AgentAction.WAIT, selected_candidate_id=None, reason_codes=(ReasonCode.DATA_QUALITY,), evidence_feature_ids=())
    v = validate(d, flagged, received_at=T, exit_enabled=False, role="VETO")
    assert v.status is ValidationStatus.VALID and v.effective_action is AgentAction.WAIT
    assert validate(d, _ctx(), received_at=T, exit_enabled=False, role="VETO").status is ValidationStatus.VETO_UNSUPPORTED


def test_veto_role_overrules_an_unsupported_veto_and_records_it() -> None:
    for d in (_dec(action=AgentAction.WAIT, selected_candidate_id=None, reason_codes=(ReasonCode.WEAK_EVIDENCE,)),
              _dec(action=AgentAction.WAIT, selected_candidate_id=None, reason_codes=(ReasonCode.CONFLICTING_SIGNALS,),
                   evidence_feature_ids=())):  # permitted reason but no cited evidence
        v = validate(d, _ctx(), received_at=T, exit_enabled=False, role="VETO")
        assert v.status is ValidationStatus.VETO_UNSUPPORTED and v.effective_action is AgentAction.ENTER
        assert v.decision is not None and v.decision.selected_candidate_id == "f2-a" and v.detail


def test_veto_role_never_lets_the_model_pick_another_candidate_and_failures_still_wait() -> None:
    other = SignalCandidate.model_validate({**CAND.model_dump(), "candidate_id": "f2-b"})
    ctx = _ctx(candidates=(CAND, other))
    v = validate(_dec(selected_candidate_id="f2-b"), ctx, received_at=T, exit_enabled=False, role="VETO")
    assert v.status is ValidationStatus.UNKNOWN_REFERENCE and v.effective_action is AgentAction.WAIT
    assert validate(None, ctx, received_at=T, exit_enabled=False, role="VETO").effective_action is AgentAction.WAIT
    late = validate(_dec(), ctx, received_at=T + timedelta(hours=1), exit_enabled=False, role="VETO")
    assert late.effective_action is AgentAction.WAIT
    expired_ctx = _ctx(candidates=(SignalCandidate.model_validate({**CAND.model_dump(), "created_at": T - timedelta(minutes=20),
                                                                   "expires_at": T - timedelta(seconds=1)}),))
    unsupported = _dec(action=AgentAction.WAIT, selected_candidate_id=None, reason_codes=(ReasonCode.WEAK_EVIDENCE,))
    assert validate(unsupported, expired_ctx, received_at=T, exit_enabled=False, role="VETO").effective_action is AgentAction.WAIT


def test_selector_role_is_unchanged() -> None:
    d = _dec(action=AgentAction.WAIT, selected_candidate_id=None, reason_codes=(ReasonCode.WEAK_EVIDENCE,))
    v = validate(d, _ctx(), received_at=T, exit_enabled=False)
    assert v.status is ValidationStatus.VALID and v.effective_action is AgentAction.WAIT


# ------------------------------------------------------------------------------- transient router outages
def _flaky(failures: int) -> tuple[Any, dict[str, int]]:
    from pydantic_ai.exceptions import ModelConnectionError
    from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo

    calls = {"n": 0}

    def fn(_: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] <= failures:
            raise ModelConnectionError("router", "Connection error.", phase="connect")
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _dec().model_dump(mode="json"))])
    return fn, calls


def test_a_transient_router_connection_error_is_retried_within_the_deadline(monkeypatch: Any) -> None:
    from pydantic_ai.models.function import FunctionModel

    from wallex_agent.agent.decision_agent import LlmDecisionProvider
    from wallex_agent.runtime.config import AgentSettings

    def no_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("time.sleep", no_sleep)
    settings = AgentSettings(decision_provider="LLM", model_id="fake", role="VETO", prompt_version="veto_v1")
    ctx = dataclasses.replace(_ctx(F2.brief("BTCTMN", 14400)), now=datetime.now(UTC), deadline=datetime.now(UTC) + timedelta(seconds=60))
    ok = dataclasses.replace(ctx, candidates=(CAND.model_copy(update={"created_at": ctx.now, "expires_at": ctx.now + timedelta(minutes=15)}),))
    fn, calls = _flaky(2)
    v, _ = LlmDecisionProvider(FunctionModel(fn), model_label="fake", settings=settings, store=None, stream=False).decide(ok)
    assert calls["n"] == 3 and v.effective_action is AgentAction.ENTER  # two blips, then the real answer
    fn, calls = _flaky(10)
    v, _ = LlmDecisionProvider(FunctionModel(fn), model_label="fake", settings=settings, store=None, stream=False).decide(ok)
    assert calls["n"] == 3 and v.status is ValidationStatus.PROVIDER_ERROR and v.effective_action is AgentAction.WAIT  # bounded
