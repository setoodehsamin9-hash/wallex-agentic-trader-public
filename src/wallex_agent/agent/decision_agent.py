"""Bounded LLM decision agent (PydanticAI). The model sees one snapshot, its features and the deterministic
candidates, may drill down with READ-ONLY tools, and must return an `AgentDecision`.

Bounds: `UsageLimits(request_limit, tool_calls_limit)` + wall-clock deadline + small output retry count. Tools: internal
read tools over the frozen cycle context, plus (SHADOW/LIVE, if enabled) the Tool Gateway allowlist of public Wallex
MCP reads. No write tool exists anywhere in this module. Every failure -> WAIT with a ValidationStatus.
Every tool call and the raw output are recorded in the journal for audit and for replay without a model.
"""

import json
import time
from collections.abc import AsyncIterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import resources
from typing import Any, cast

from pydantic_ai import Agent, RunContext, capture_run_messages
from pydantic_ai.capabilities.hooks import Hooks
from pydantic_ai.exceptions import ModelConnectionError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.usage import UsageLimits

from wallex_agent.contracts import (
    AgentDecision,
    DecisionProvenance,
    DecisionSource,
    Environment,
    ToolCallRecord,
    ValidatedDecision,
    ValidationStatus,
)
from wallex_agent.contracts.hashing import canonical_json, content_id, sha256_hex
from wallex_agent.journal.store import JournalStore
from wallex_agent.runtime.config import AgentSettings
from wallex_agent.tools import Consumer, exposed_tools

from .context import DecisionContext
from .validator import validate

INTERNAL_TOOLS = ("get_risk_status", "get_open_positions", "get_recent_decisions")
OUTPUT_TOOL_PREFIX = "final_result"
"""PydanticAI's structured-output channel; it is the answer, not tool use, so it is not recorded as a tool call."""


def _tool_payload_failed(value: Any) -> bool:
    if isinstance(value, str):
        try:
            return _tool_payload_failed(json.loads(value))
        except (ValueError, TypeError):
            return False
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        if mapping.get("isError") is True or mapping.get("success") is False or mapping.get("s") == "error":
            return True
        return any(_tool_payload_failed(mapping[k]) for k in ("content", "text") if k in mapping)
    if isinstance(value, list):
        return any(_tool_payload_failed(item) for item in cast(list[Any], value))
    return False


def load_prompt(version: str) -> tuple[str, str]:
    text = resources.files("wallex_agent.agent").joinpath(f"prompts/{version}.md").read_text()
    return text, sha256_hex(text.encode())


@dataclass
class _Deps:
    ctx: DecisionContext
    offered_tools: set[str] = field(default_factory=lambda: set())


@dataclass
class RecordedDecision:
    output: dict[str, Any] | None
    status: str
    detail: str | None
    responding_model: str | None
    latency_ms: int
    tool_calls: list[dict[str, Any]] = field(default_factory=lambda: [])
    offered_tools: list[str] = field(default_factory=lambda: [])


def _render_with_brief(ctx: DecisionContext) -> str:
    """Strategy-aware view: the brief, only its evidence features, and the candidate's real exit design."""
    b = ctx.strategy_brief
    assert b is not None
    s = ctx.snapshot
    shown = set(b.evidence_feature_ids)
    feats = [{"feature_id": f.feature_id, "value": None if f.value is None else f"{f.value:.6g}", "unit": f.unit}
             for f in ctx.features.features if f.feature_id in shown]
    atr = next((f.value for f in ctx.features.features if f.feature_id.startswith(f"{s.symbol}.atr.") and f.value), None)
    cands: list[dict[str, Any]] = []
    for c in ctx.candidates:
        cands.append({"candidate_id": c.candidate_id, "strategy": f"{c.strategy_id}@{c.strategy_version}", "symbol": c.symbol,
                      "reference_price": str(c.reference_price), "stop_price": str(c.stop_price),
                      "stop_distance_atr": None if not atr else f"{(c.reference_price - c.stop_price) / atr:.2f}",
                      "trail_distance": None if c.trail_distance is None else f"{c.trail_distance:.6g}",
                      "exit_policy_id": c.exit_policy_id, "max_holding_minutes": c.max_holding_seconds // 60,
                      "estimated_round_trip_cost_bps": str(c.estimated_round_trip_cost_bps),
                      "expires_at": c.expires_at.isoformat(), "evidence_feature_ids": list(c.evidence_feature_ids)})
    state = {k: ctx.risk_status[k] for k in ("kill_switch", "unresolved_orders", "recovery_outcome", "entries_allowed")
             if k in ctx.risk_status}
    body = {"snapshot_id": s.snapshot_id, "symbol": s.symbol, "as_of_utc": s.as_of.isoformat(),
            "bar_interval_minutes": s.bar_interval_seconds // 60, "quality_flags": sorted(f.value for f in s.quality_flags),
            "book_top": None if s.book is None else {"bid": str(s.book.bid), "ask": str(s.book.ask)},
            "STRATEGY": b.model_dump(mode="json"), "FEATURES": feats, "CANDIDATES": cands, "EXECUTION_STATE": state,
            "open_positions": len(ctx.positions)}
    return "Decide for this snapshot. Data (JSON):\n" + json.dumps(body, ensure_ascii=False)


def render_user_prompt(ctx: DecisionContext) -> str:
    if ctx.strategy_brief is not None:
        return _render_with_brief(ctx)
    s = ctx.snapshot
    feats = [{"feature_id": f.feature_id, "value": None if f.value is None else f"{f.value:.6g}", "unit": f.unit} for f in ctx.features.features]
    cands: list[dict[str, Any]] = []
    for c in ctx.candidates:
        edge = (c.take_profit_price - c.reference_price) / c.reference_price * 10000 if c.take_profit_price else None
        cands.append({"candidate_id": c.candidate_id, "strategy": f"{c.strategy_id}@{c.strategy_version}", "symbol": c.symbol,
                      "reference_price": str(c.reference_price), "stop_price": str(c.stop_price),
                      "take_profit_price": str(c.take_profit_price), "max_holding_minutes": c.max_holding_seconds // 60,
                      "target_edge_bps": None if edge is None else f"{edge:.1f}",
                      "estimated_round_trip_cost_bps": str(c.estimated_round_trip_cost_bps),
                      "expires_at": c.expires_at.isoformat(), "evidence_feature_ids": list(c.evidence_feature_ids)})
    body = {"snapshot_id": s.snapshot_id, "symbol": s.symbol, "as_of_utc": s.as_of.isoformat(),
            "bar_interval_minutes": s.bar_interval_seconds // 60, "deterministic_regime": ctx.features.deterministic_regime.value,
            "book_top": None if s.book is None else {"bid": str(s.book.bid), "ask": str(s.book.ask)},
            "FEATURES": feats, "CANDIDATES": cands, "open_positions": len(ctx.positions)}
    return "Decide for this snapshot. Data (JSON):\n" + json.dumps(body, ensure_ascii=False)


CONNECT_RETRIES = 2
"""Retries of a model call after a router connection error (transient NineRouter outage seen in LIVE, 2026-10-23)."""
RETRY_MIN_LEFT_S = 15


class LlmDecisionProvider:
    source_name = "LLM"

    def __init__(self, model: Model, *, model_label: str, settings: AgentSettings, store: JournalStore | None,
                 mcp_toolset: AbstractToolset[Any] | None = None, stream: bool = True,
                 secret_literals: Sequence[str] = (), environment: Environment = Environment.BACKTEST,
                 provider_label: str = "ninerouter") -> None:
        self.model = model
        self.model_label = model_label
        self.provider_label = provider_label
        """Journaled as `<provider>:<model>`; the real path is NineRouter, test doubles must say what they are."""
        self.settings = settings
        self.store = store
        self.stream = stream
        self._secrets = tuple(s for s in secret_literals if s)
        self.prompt, self.prompt_sha256 = load_prompt(settings.prompt_version)
        self.settings_sha256 = content_id({"model": model_label, "s": settings.model_settings,
                                           "limits": [settings.max_model_requests, settings.max_tool_calls, settings.output_retries]})
        hooks = Hooks()

        @hooks.on.before_model_request
        def audit_tools(rc: RunContext[_Deps], request: ModelRequestContext) -> ModelRequestContext:  # pyright: ignore[reportUnusedFunction]
            rc.deps.offered_tools.update(t.name for t in request.model_request_parameters.function_tools)
            return request

        allowed = exposed_tools(Consumer.DECISION_AGENT, environment)
        toolsets: list[AbstractToolset[Any]] = [mcp_toolset.filtered(lambda rc, tool: tool.name in allowed)] if mcp_toolset is not None else []
        self.agent: Agent[_Deps, AgentDecision] = Agent(model, name="wallex_decision", output_type=AgentDecision, instructions=self.prompt,
                                                         deps_type=_Deps, retries=settings.output_retries, toolsets=toolsets,
                                                         capabilities=[hooks],
                                                         model_settings=settings.model_settings or None)  # type: ignore[arg-type]

        @self.agent.tool
        def get_risk_status(rc: RunContext[_Deps]) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
            """Account/risk summary: equity, drawdown, exposure, open positions, kill switch, trading window.
            Takes no arguments: call it with {}."""
            return rc.deps.ctx.risk_status

        @self.agent.tool
        def get_open_positions(rc: RunContext[_Deps]) -> list[dict[str, Any]]:  # pyright: ignore[reportUnusedFunction]
            """Open positions with fixed stop/take-profit/deadline and unrealized P&L. Takes no arguments: call it with {}."""
            return [p.__dict__ for p in rc.deps.ctx.positions]

        @self.agent.tool
        def get_recent_decisions(rc: RunContext[_Deps]) -> list[dict[str, Any]]:  # pyright: ignore[reportUnusedFunction]
            """The last few decisions for this symbol (status, action, reason codes). Takes no arguments: call it with {}."""
            return list(rc.deps.ctx.recent_decisions)

    def _scrub(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, "***")
        return text

    def _run(self, deps: _Deps) -> Any:
        async def drain(_: RunContext[_Deps], events: AsyncIterable[Any]) -> None:
            async for _e in events:
                pass

        limits = UsageLimits(request_limit=self.settings.max_model_requests, tool_calls_limit=self.settings.max_tool_calls)
        return self.agent.run_sync(render_user_prompt(deps.ctx), deps=deps, usage_limits=limits,
                                   timeout=self.settings.decision_deadline_s,
                                   event_stream_handler=drain if self.stream else None)

    def _run_with_retry(self, deps: _Deps, t0: float) -> Any:
        """A router connection blip is retried (a model call is read-only; nothing trades), at most CONNECT_RETRIES times
        and only while >= RETRY_MIN_LEFT_S of the decision deadline remain. Still failing -> PROVIDER_ERROR -> WAIT."""
        for attempt in range(CONNECT_RETRIES + 1):
            try:
                return self._run(deps)
            except ModelConnectionError:
                left = self.settings.decision_deadline_s - (time.monotonic() - t0)
                if attempt == CONNECT_RETRIES or left < RETRY_MIN_LEFT_S:
                    raise
                time.sleep(min(2.0 * (attempt + 1), left / 4))
        raise AssertionError("unreachable")

    def _tool_records(self, messages: Sequence[Any]) -> tuple[list[ToolCallRecord], list[dict[str, Any]]]:
        calls: dict[str, ToolCallPart] = {}
        completed: set[str] = set()
        records: list[ToolCallRecord] = []
        raw: list[dict[str, Any]] = []

        def record(call_id: str, name: str, value: Any, error: bool) -> None:
            call = calls.get(call_id)
            try:
                args = call.args_as_dict() if call is not None else {}
            except (ValueError, TypeError):
                args = {"invalid_args": str(call.args) if call else ""}
            safe_args = json.loads(self._scrub(json.dumps(args, default=str, ensure_ascii=False)))
            content = self._scrub(json.dumps(value, default=str, ensure_ascii=False))
            ref = self.store.put_payload(content.encode()) if self.store else None
            records.append(ToolCallRecord(seq=len(records), tool_name=name, args_sha256=sha256_hex(canonical_json(safe_args)),
                                          result_sha256=sha256_hex(content.encode()), result_ref=ref,
                                          is_error=error, latency_ms=0))
            raw.append({"tool": name, "args": safe_args, "result_ref": ref, "is_error": error})
            completed.add(call_id)

        for m in messages:
            for p in getattr(m, "parts", []):
                if isinstance(p, ToolCallPart) and not p.tool_name.startswith(OUTPUT_TOOL_PREFIX):
                    calls[p.tool_call_id] = p
                elif isinstance(p, ToolReturnPart) and not p.tool_name.startswith(OUTPUT_TOOL_PREFIX):
                    record(p.tool_call_id, p.tool_name, p.content, p.outcome != "success" or _tool_payload_failed(p.content))
                elif isinstance(p, RetryPromptPart) and p.tool_name and not p.tool_name.startswith(OUTPUT_TOOL_PREFIX):
                    record(p.tool_call_id, p.tool_name, p.content, True)
        for cid, call in calls.items():
            if cid not in completed:
                record(cid, call.tool_name, {"error": "tool unavailable or call did not complete"}, True)
        return records, raw

    def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
        started = datetime.now(UTC)
        t0 = time.monotonic()
        output: AgentDecision | None = None
        failure, detail = ValidationStatus.PROVIDER_ERROR, None
        messages: Sequence[Any] = ()
        requests, tin, tout, responder = 0, None, None, None
        deps = _Deps(ctx)
        try:
            with capture_run_messages() as captured:
                messages = captured
                result = self._run_with_retry(deps, t0)
                output = result.output
                u = result.usage
                requests, tin, tout = u.requests, u.input_tokens, u.output_tokens
        except TimeoutError:
            failure, detail = ValidationStatus.TIMEOUT, f"no answer within {self.settings.decision_deadline_s}s"
        except UsageLimitExceeded as exc:
            failure, detail = ValidationStatus.BUDGET_EXCEEDED, self._scrub(str(exc))
        except UnexpectedModelBehavior as exc:
            failure, detail = ValidationStatus.INVALID_SCHEMA, self._scrub(str(exc))[:300]
        except Exception as exc:  # provider/network faults must never crash the trading loop
            failure, detail = ValidationStatus.PROVIDER_ERROR, self._scrub(f"{type(exc).__name__}: {exc}")[:300]
        latency_ms = int((time.monotonic() - t0) * 1000)
        if not requests:
            requests = sum(isinstance(m, ModelRequest) for m in messages)
            responses = [m for m in messages if isinstance(m, ModelResponse)]
            tin = sum(m.usage.input_tokens for m in responses) if responses else None
            tout = sum(m.usage.output_tokens for m in responses) if responses else None
        for m in messages:
            if isinstance(m, ModelResponse) and m.model_name:
                responder = m.model_name
        tools, raw_tools = self._tool_records(messages)
        received = ctx.now + timedelta(milliseconds=latency_ms)
        validated = validate(output, ctx, received_at=received, exit_enabled=self.settings.allow_exit_position,
                             failure=failure, detail=detail, role=self.settings.role)
        rec = RecordedDecision(output=output.model_dump(mode="json") if output else None, status=validated.status.value,
                               detail=validated.detail, responding_model=responder, latency_ms=latency_ms, tool_calls=raw_tools,
                               offered_tools=sorted(deps.offered_tools))
        raw_ref = None
        if self.store is not None:
            raw_ref = self.store.put_ref(ctx.run_id, f"llm:{ctx.decision_key}", rec.__dict__)
        prov = DecisionProvenance(source=DecisionSource.LLM, model_id=f"{self.provider_label}:{responder or self.model_label}",
                                  model_settings_sha256=self.settings_sha256, prompt_version=self.settings.prompt_version,
                                  prompt_sha256=self.prompt_sha256, tool_calls=tuple(tools), model_requests=requests,
                                  input_tokens=tin, output_tokens=tout, latency_ms=latency_ms, raw_output_ref=raw_ref,
                                  started_at=started, finished_at=datetime.now(UTC))
        return validated, prov

