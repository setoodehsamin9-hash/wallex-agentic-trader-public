from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from wallex_agent.agent.assistant import ASSISTANT_TOOLS, ask
from wallex_agent.contracts import AgentAction, AutonomyMode, CandidateSummary, CodeVersion, CycleStatus, DecisionRecord, Environment, RiskCheck, RiskOutcome, RiskVerdict
from wallex_agent.journal.store import JournalStore

T = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def _store(tmp: Path) -> JournalStore:
    s = JournalStore(tmp / "j.sqlite")
    s.record_decision(DecisionRecord(decision_id="d-wait", run_id="r", cycle_id="c", recorded_at=T, environment=Environment.SHADOW,
                                     autonomy=AutonomyMode.ADVISOR, symbol="BTCTMN", snapshot_id="s", data_refs=(), features_ref=None,
                                     deterministic_regime=None, candidates=(), provenance=None, validated=None,
                                     effective_action=AgentAction.WAIT, policy_outcome=None, risk_verdict=None, approval=None,
                                     order_intent=None, status=CycleStatus.BLOCKED, block_reason="OUTSIDE_TRADING_WINDOW",
                                     next_entry_at=T, config_sha256="c", code=CodeVersion(git_commit="a" * 40, dirty=False)))
    s.set_status("r", T, {"active": True, "entries_allowed": False})
    return s


def test_assistant_tools_are_read_only_and_answer_from_journal(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen["tools"] = sorted(t.name for t in info.function_tools)
        returned: list[ToolReturnPart] = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
        if not returned:
            return ModelResponse(parts=[ToolCallPart("get_recent_decisions", {"limit": 3})])
        seen["data"] = returned[-1].content
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                                                 {"answer_fa": "الان خارج از ساعت مجاز معامله هستیم.", "cited_decision_ids": ["d-wait"]})])

    answer = ask(FunctionModel(fn), _store(tmp_path), "چرا معامله نکردی؟", run_id="r", stream=False)
    assert answer.cited_decision_ids == ["d-wait"]
    assert seen["tools"] == sorted(ASSISTANT_TOOLS)
    assert not any(w in t for t in seen["tools"] for w in ("approve", "execute", "kill", "reset", "submit", "order", "mode", "limit"))
    assert "OUTSIDE_TRADING_WINDOW" in str(seen["data"])


def test_counterfactual_never_claims_execution_for_risk_rejected_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = store.decision("d-wait")
    assert source is not None
    store.record_decision(source.model_copy(update={
        "decision_id": "d-rejected", "recorded_at": T + timedelta(seconds=1), "effective_action": AgentAction.ENTER,
        "status": CycleStatus.RISK_REJECTED,
        "candidates": (CandidateSummary(candidate_id="c1", strategy_id="C1", strategy_version="v", symbol="BTCTMN"),),
        "risk_verdict": RiskVerdict(verdict_id="rejected", intent_hash="h", outcome=RiskOutcome.REJECT, approved_qty=None,
                                    checks=(RiskCheck(name="kill", passed=False, detail="operator stop"),),
                                    limits_version="test", evaluated_at=T, expires_at=T + timedelta(seconds=30))}))
    seen: dict[str, Any] = {}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returned: list[ToolReturnPart] = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
        if not returned:
            return ModelResponse(parts=[ToolCallPart("what_would_autonomous_do", {})])
        seen["data"] = returned[-1].content
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"answer_fa": "ریسک رد شده است."})])

    ask(FunctionModel(fn), store, "در حالت خودکار چه می‌کردی؟", run_id="r", stream=False)
    assert seen["data"]["risk_outcome"] == "REJECT"
    assert "send the risk-approved entry automatically" not in seen["data"]["would"]
    assert seen["data"]["would_execute"] is False
