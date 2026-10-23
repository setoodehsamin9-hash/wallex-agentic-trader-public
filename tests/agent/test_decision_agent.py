"""Layer 1 (no network, no real model): scripted PydanticAI FunctionModel drives the real LlmDecisionProvider."""

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from wallex_agent.agent.context import DecisionContext
from wallex_agent.agent.decision_agent import LlmDecisionProvider, render_user_prompt
from wallex_agent.agent.ninerouter import NineRouterNotConfigured, load_settings, resolve_api_key
from wallex_agent.agent.replay import ReplayDecisionProvider
from wallex_agent.contracts import (
    AgentAction,
    DataRef,
    DecisionSource,
    FeatureSet,
    FeatureValue,
    MarketMeta,
    MarketSnapshot,
    RegimeLabel,
    SignalCandidate,
    ValidationStatus,
)
from wallex_agent.journal.store import JournalStore
from wallex_agent.runtime.config import AgentSettings

T = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FAKE_KEY = "sk-test-secret-value-1234567890"
META = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=6, min_qty=Decimal("0.000001"),
                  min_notional=Decimal(50000), market_order_enabled=True, metadata_version="t")
SNAP = MarketSnapshot(snapshot_id="snap-1", symbol="BTCTMN", as_of=T, bar_interval_seconds=900,
                      last_closed_bar_open=T - timedelta(seconds=900),
                      candles=DataRef(dataset_id="d", content_sha256="0" * 64, source="FIXTURE", symbol="BTCTMN", first_ts=T,
                                      last_ts=T, resolution_seconds=900), book=None, meta=META, universe_version="u")
FS = FeatureSet(snapshot_id="snap-1", feature_engine_version="f", deterministic_regime=RegimeLabel.TRENDING_UP,
                features=(FeatureValue(feature_id="BTCTMN.breakout_margin_atr.16", name="breakout_margin_atr", value=Decimal("0.8"),
                                       unit="ATR", window_bars=16),))
CAND = SignalCandidate(candidate_id="c1-abc", snapshot_id="snap-1", strategy_id="C1_breakout", strategy_version="v", symbol="BTCTMN",
                       reference_price=Decimal(100), stop_price=Decimal(95), take_profit_price=Decimal(110), max_holding_seconds=3600,
                       exit_policy_id="tb", estimated_round_trip_cost_bps=Decimal(60),
                       evidence_feature_ids=("BTCTMN.breakout_margin_atr.16",), created_at=T, expires_at=T + timedelta(minutes=10))


def _ctx(deadline_s: int = 60) -> DecisionContext:
    return DecisionContext(snapshot=SNAP, features=FS, candidates=(CAND,), positions=(), risk_status={"equity_tmn": "5000000"},
                           recent_decisions=(), now=T, deadline=T + timedelta(seconds=deadline_s), run_id="run-a",
                           decision_key="BTCTMN@2026-10-22T09:00:00+00:00")


def _decision(cid: str | None = "c1-abc", action: str = "ENTER") -> dict[str, Any]:
    return {"snapshot_id": "snap-1", "action": action, "selected_candidate_id": cid, "regime_assessment": "TRENDING_UP",
            "confidence": "MEDIUM", "reason_codes": ["BREAKOUT_CONFIRMED"], "evidence_feature_ids": ["BTCTMN.breakout_margin_atr.16"],
            "brief_rationale": "شکست سقف با حاشیه کافی"}


def _provider(fn: Any, store: JournalStore | None = None, **settings: Any) -> LlmDecisionProvider:
    s = AgentSettings(decision_provider="LLM", model_id="fake", **settings)
    return LlmDecisionProvider(FunctionModel(fn), model_label="fake", settings=s, store=store, stream=False,
                               secret_literals=(FAKE_KEY,))


def _script(*steps: Any) -> Any:
    state = {"i": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = steps[min(state["i"], len(steps) - 1)]
        state["i"] += 1
        return step(messages, info)

    return fn


def _final(payload: dict[str, Any]) -> Any:
    def step(m: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    return step


def _text(text: str) -> Any:
    def step(m: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    return step


def _risk_tool(m: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart("get_risk_status", {})])


def test_valid_decision_with_tool_use_is_recorded(tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    seen: dict[str, list[str]] = {}

    def look(m: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["tools"] = sorted(t.name for t in info.function_tools)
        return ModelResponse(parts=[ToolCallPart("get_risk_status", {})])

    v, prov = _provider(_script(look, _final(_decision())), store).decide(_ctx())
    assert v.status is ValidationStatus.VALID and v.effective_action is AgentAction.ENTER
    assert prov.source is DecisionSource.LLM and [t.tool_name for t in prov.tool_calls] == ["get_risk_status"]
    assert seen["tools"] == ["get_open_positions", "get_recent_decisions", "get_risk_status"]  # read-only only
    recorded = store.get_ref("run-a", "llm:BTCTMN@2026-10-22T09:00:00+00:00")
    assert recorded is not None and recorded["output"]["selected_candidate_id"] == "c1-abc"
    assert recorded["offered_tools"] == seen["tools"]


def test_invented_candidate_becomes_wait() -> None:
    v, _ = _provider(_script(_final(_decision(cid="c1-INVENTED")))).decide(_ctx())
    assert v.status is ValidationStatus.UNKNOWN_REFERENCE and v.effective_action is AgentAction.WAIT


def test_parameter_invention_is_rejected_as_schema_error() -> None:
    bad = {**_decision(), "qty": "5", "stop_price": "1"}
    v, _ = _provider(_script(_final(bad)), output_retries=1).decide(_ctx())
    assert v.status is ValidationStatus.INVALID_SCHEMA and v.effective_action is AgentAction.WAIT


def test_malformed_text_output_becomes_wait() -> None:
    v, _ = _provider(_script(_text("BUY BTC NOW 100%")), output_retries=1).decide(_ctx())
    assert v.effective_action is AgentAction.WAIT and v.status in (ValidationStatus.INVALID_SCHEMA, ValidationStatus.BUDGET_EXCEEDED)


def test_usage_limits_stop_runaway_tool_loop() -> None:
    v, _ = _provider(_script(_risk_tool), max_tool_calls=2, max_model_requests=3).decide(_ctx())
    assert v.status is ValidationStatus.BUDGET_EXCEEDED and v.effective_action is AgentAction.WAIT


def test_budget_failure_preserves_completed_read_tool_audit(tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    v, prov = _provider(_script(_risk_tool), store, max_tool_calls=2, max_model_requests=3).decide(_ctx())
    assert v.status is ValidationStatus.BUDGET_EXCEEDED and v.effective_action is AgentAction.WAIT
    assert prov.model_requests >= 1
    assert prov.tool_calls and prov.tool_calls[0].tool_name == "get_risk_status"
    assert prov.tool_calls[0].result_ref is not None
    assert store.get_payload(prov.tool_calls[0].result_ref) is not None


def test_read_payload_failure_is_marked_as_tool_error(tmp_path: Path) -> None:
    from dataclasses import replace

    ctx = replace(_ctx(), risk_status={"s": "error", "errmsg": "fixture unavailable"})
    v, prov = _provider(_script(_risk_tool, _final(_decision(action="WAIT", cid=None))),
                        JournalStore(tmp_path / "j.sqlite")).decide(ctx)
    assert v.effective_action is AgentAction.WAIT
    assert prov.tool_calls[0].is_error


def test_unknown_write_tool_is_unavailable_and_audited_as_failure(tmp_path: Path) -> None:
    seen: list[str] = []

    def attempt(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(t.name for t in info.function_tools)
        return ModelResponse(parts=[ToolCallPart("wallex_spot_post_account_orders", {"symbol": "BTCTMN", "side": "BUY"})])

    v, prov = _provider(attempt, JournalStore(tmp_path / "j.sqlite"), output_retries=0, max_model_requests=2).decide(_ctx())
    assert v.effective_action is AgentAction.WAIT
    assert "wallex_spot_post_account_orders" not in seen
    assert any(t.tool_name == "wallex_spot_post_account_orders" and t.is_error for t in prov.tool_calls)


def test_injected_mcp_toolset_is_filtered_again_at_agent_boundary(tmp_path: Path) -> None:
    from pydantic_ai.toolsets import FunctionToolset

    calls: list[str] = []
    seen: list[str] = []
    toolset: Any = FunctionToolset()

    def forbidden() -> str:
        calls.append("write")
        return "this must never run"

    toolset.add_function(forbidden, name="wallex_spot_post_account_orders")

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(t.name for t in info.function_tools)
        return _final(_decision(action="WAIT", cid=None))(messages, info)

    provider = LlmDecisionProvider(FunctionModel(fn), model_label="fake", settings=AgentSettings(),
                                    store=JournalStore(tmp_path / "j.sqlite"), mcp_toolset=toolset,
                                    stream=False)
    v, _ = provider.decide(_ctx())
    assert v.effective_action is AgentAction.WAIT and calls == []
    assert "wallex_spot_post_account_orders" not in seen


def test_malformed_tool_arguments_wait_and_remain_auditable(tmp_path: Path) -> None:
    def malformed(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("get_risk_status", "not-json")])

    v, prov = _provider(malformed, JournalStore(tmp_path / "j.sqlite"), output_retries=0).decide(_ctx())
    assert v.effective_action is AgentAction.WAIT
    assert prov.tool_calls and prov.tool_calls[0].is_error


def test_timeout_becomes_wait() -> None:
    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(3)
        return _final(_decision())(messages, info)

    v, _ = _provider(slow, decision_deadline_s=1).decide(_ctx(deadline_s=1))
    assert v.status is ValidationStatus.TIMEOUT and v.effective_action is AgentAction.WAIT


def test_repeated_decisions_keep_loop_bound_provider_usable() -> None:
    loops: list[asyncio.AbstractEventLoop] = []

    async def loop_bound(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        loop = asyncio.get_running_loop()
        if loops and loop is not loops[0]:
            raise RuntimeError("loop-bound provider client was reused after its loop closed")
        loops.append(loop)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _decision())])

    provider = _provider(loop_bound)
    first, _ = provider.decide(_ctx())
    second, _ = provider.decide(_ctx())
    assert first.status is ValidationStatus.VALID and second.status is ValidationStatus.VALID


def test_provider_error_becomes_wait_and_secret_is_scrubbed() -> None:
    def boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ConnectionError(f"upstream refused key {FAKE_KEY}")

    v, prov = _provider(boom).decide(_ctx())
    assert v.status is ValidationStatus.PROVIDER_ERROR and v.effective_action is AgentAction.WAIT
    assert FAKE_KEY not in (v.detail or "") and FAKE_KEY not in prov.model_dump_json()


def test_prompt_contains_no_secret_and_only_offered_ids() -> None:
    text = render_user_prompt(_ctx())
    assert FAKE_KEY not in text and "c1-abc" in text and "snap-1" in text


def test_replay_reproduces_decision_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    live, _ = _provider(_script(_final(_decision())), store).decide(_ctx())

    def no_network(*a: Any, **k: Any) -> Any:
        raise AssertionError("network used during replay")

    monkeypatch.setattr(socket, "socket", no_network)
    replayed, prov = ReplayDecisionProvider(store, "run-a", exit_enabled=False).decide(_ctx())
    assert replayed.decision == live.decision and replayed.effective_action is AgentAction.ENTER
    assert prov.source is DecisionSource.REPLAY


def test_key_loader_refuses_repo_path_and_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "secrets").mkdir(parents=True)
    inside = repo / "secrets" / "k.txt"
    inside.write_text(FAKE_KEY)
    with pytest.raises(NineRouterNotConfigured, match="inside the repository"):
        resolve_api_key({"NINEROUTER_API_KEY_FILE": str(inside)}, repo_root=repo)
    outside = tmp_path / "outside.txt"
    outside.write_text(FAKE_KEY + "\n")
    assert resolve_api_key({"NINEROUTER_API_KEY_FILE": str(outside)}, repo_root=repo) == FAKE_KEY
    with pytest.raises(NineRouterNotConfigured):
        resolve_api_key({}, repo_root=repo)


def test_settings_model_not_hard_coded() -> None:
    with pytest.raises(NineRouterNotConfigured):
        load_settings(model_id=None, timeout_s=30, env={})
    s = load_settings(model_id="cfg-model", timeout_s=30, env={"NINEROUTER_MODEL": "env-model", "NINEROUTER_BASE_URL": "http://x:1/v1/"})
    assert s.model_id == "env-model" and s.base_url == "http://x:1/v1"
