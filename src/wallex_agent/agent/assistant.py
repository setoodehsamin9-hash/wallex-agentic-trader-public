"""Read-only assistant (Persian Q&A) over recorded state: status, decisions, trades, risk events, performance and the
trading window. It runs through NineRouter like the decision agent.

It has NO tool that approves, executes, changes mode, resets the kill switch or changes limits: approvals and the
kill switch are separate CLI commands, so a chat answer can never become an order.
"""

import asyncio
import json
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from wallex_agent.contracts import AutonomyMode, Environment, PolicyOutcome, RiskOutcome
from wallex_agent.journal.store import JournalStore
from wallex_agent.policy import ActionKind, PolicyContext, decide

ASSISTANT_TOOLS = ("get_status", "get_recent_decisions", "get_decision", "get_recent_trades", "get_risk_events",
                   "what_would_autonomous_do")

INSTRUCTIONS = """You are the read-only assistant of an automated Wallex trading system. Answer the owner's question in
clear, short Persian for a non-trader, using ONLY facts returned by your tools (status, journal decisions, trades, risk
events). Explain trading terms briefly. If the data does not answer the question, say so. You cannot approve, execute,
change modes, reset the kill switch or change limits; if asked, explain which CLI command the operator uses instead
(wallex-agent approve/reject/kill/reset-kill). Never promise profit or give personal investment advice. Cite decision
ids you relied on."""


class AssistantAnswer(BaseModel):
    answer_fa: str = Field(min_length=1, max_length=2500)
    cited_decision_ids: list[str] = Field(default_factory=lambda: [])


@dataclass
class _Deps:
    store: JournalStore
    run_id: str | None


def _brief(d: Any) -> dict[str, Any]:
    v = d.validated.decision if d.validated and d.validated.decision else None
    return {"decision_id": d.decision_id, "at": d.recorded_at.isoformat(), "symbol": d.symbol, "status": d.status.value,
            "action": d.effective_action.value, "block_reason": d.block_reason, "candidates": len(d.candidates),
            "ai_source": d.provenance.source.value if d.provenance else None,
            "ai_status": d.validated.status.value if d.validated else None,
            "reasons": [r.value for r in v.reason_codes] if v else [], "rationale": v.brief_rationale if v else None,
            "risk_failed": [f"{c.name}: {c.detail}" for c in d.risk_verdict.checks if not c.passed] if d.risk_verdict else [],
            "next_entry_at": d.next_entry_at.isoformat() if d.next_entry_at else None}


def build_assistant(model: Model) -> Agent[_Deps, AssistantAnswer]:
    agent: Agent[_Deps, AssistantAnswer] = Agent(model, output_type=AssistantAnswer, instructions=INSTRUCTIONS, deps_type=_Deps, retries=1)

    def _run_id(rc: RunContext[_Deps]) -> str | None:
        return rc.deps.run_id or rc.deps.store.latest_run_id()

    @agent.tool
    def get_status(rc: RunContext[_Deps]) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Live status: active, environment, autonomy, entries allowed + reason, equity, realized P&L, return %, drawdown,
        open positions, kill switch, next trading-window boundary, NineRouter health, last decision."""
        return rc.deps.store.get_status(_run_id(rc)) or {"note": "no status recorded"}

    @agent.tool
    def get_recent_decisions(rc: RunContext[_Deps], limit: int = 8, symbol: str | None = None) -> list[dict[str, Any]]:  # pyright: ignore[reportUnusedFunction]
        """Most recent decision records (newest first), optionally for one symbol; includes WAIT/blocked/rejected cycles."""
        rid = _run_id(rc)
        recs = rc.deps.store.decisions(rid, limit=200, newest_first=True) if rid else []
        recs = [r for r in recs if symbol is None or r.symbol == symbol]
        interesting = [r for r in recs if r.status.value != "NO_CANDIDATES"] or recs
        return [_brief(r) for r in interesting[: max(1, min(limit, 20))]]

    @agent.tool
    def get_decision(rc: RunContext[_Deps], decision_id: str) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Full detail of one decision: candidates, AI rationale/evidence, policy outcome, risk checks, order intent."""
        d = rc.deps.store.decision(decision_id)
        if d is None:
            return {"error": "unknown decision id"}
        out = _brief(d)
        out["policy_outcome"] = d.policy_outcome.value if d.policy_outcome else None
        out["order_intent"] = json.loads(d.order_intent.model_dump_json()) if d.order_intent else None
        return out

    @agent.tool
    def get_recent_trades(rc: RunContext[_Deps], limit: int = 10) -> list[dict[str, Any]]:  # pyright: ignore[reportUnusedFunction]
        """Closed trades (entry/exit price, fees, net P&L in TMN, exit reason, decision id)."""
        rid = _run_id(rc)
        return (rc.deps.store.trades(rid) if rid else [])[-max(1, min(limit, 30)):]

    @agent.tool
    def get_risk_events(rc: RunContext[_Deps], limit: int = 10) -> list[dict[str, Any]]:  # pyright: ignore[reportUnusedFunction]
        """Recent risk events: rejections with failed checks, window closures, AI breaker, approvals refused."""
        rid = _run_id(rc)
        return (rc.deps.store.risk_events(rid) if rid else [])[-max(1, min(limit, 30)):]

    @agent.tool
    def what_would_autonomous_do(rc: RunContext[_Deps]) -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        """Counterfactual from the latest decision that had candidates: what AUTONOMOUS mode would have done with it
        (policy outcome + the recorded risk verdict/intent). Read-only: re-runs nothing and sends nothing."""
        rid = _run_id(rc)
        recs = rc.deps.store.decisions(rid, limit=300, newest_first=True) if rid else []
        latest = next((r for r in recs if r.candidates), None)
        if latest is None:
            return {"answer": "no recent cycle had a trade candidate"}
        pol = decide(ActionKind.AI_ENTRY, PolicyContext(AutonomyMode.AUTONOMOUS, latest.environment,
                                                       rc.deps.store.kill_state().latched, False))
        risk_ok = latest.risk_verdict is not None and latest.risk_verdict.outcome is not RiskOutcome.REJECT and latest.order_intent is not None
        would_execute = latest.effective_action.value == "ENTER" and pol.outcome is PolicyOutcome.EXECUTE and risk_ok
        if latest.effective_action.value == "WAIT":
            would = "WAIT (the recorded decision did not enter)"
        elif would_execute:
            would = "send the risk-approved entry automatically, based on the recorded checks"
        else:
            would = f"not send it ({pol.reason})" if pol.outcome is not PolicyOutcome.EXECUTE else "not send it (no risk-approved intent was recorded)"
        if latest.environment is Environment.LIVE:
            would += " — LIVE autonomous also needs the operator gate (U04)"
        return {"decision": _brief(latest), "autonomous_policy": pol.outcome.value, "would": would, "would_execute": would_execute,
                "basis": "recorded intent and risk verdict; current market and balances have not been rechecked",
                "risk_outcome": latest.risk_verdict.outcome.value if latest.risk_verdict else None,
                "intent": json.loads(latest.order_intent.model_dump_json()) if latest.order_intent else None}

    return agent


def ask(model: Model, store: JournalStore, question: str, *, run_id: str | None = None, stream: bool = True,
        timeout_s: float = 90) -> AssistantAnswer:
    agent = build_assistant(model)

    async def drain(_: RunContext[_Deps], events: AsyncIterable[Any]) -> None:
        async for _e in events:
            pass

    async def go() -> AssistantAnswer:
        res = await agent.run(question, deps=_Deps(store, run_id), usage_limits=UsageLimits(request_limit=6, tool_calls_limit=6),
                              event_stream_handler=drain if stream else None)
        return res.output

    return asyncio.run(asyncio.wait_for(go(), timeout=timeout_s))
