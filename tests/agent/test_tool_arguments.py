"""Zero-argument read tools: the model is told plainly to call them with {}, and an extra argument is STILL rejected.

Evidence (reports/phase-03/ai/NINEROUTER_FINAL.json): gemini-3.8-flash called get_risk_status/get_open_positions with an
invented `reason` argument; strict validation correctly refused every call. The fix is clearer instructions only.
"""

from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, RetryPromptPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo

from agent.test_decision_agent import _ctx, _decision, _provider  # type: ignore[import-not-found]

READ_TOOLS = {"get_risk_status", "get_open_positions", "get_recent_decisions"}


def test_read_tools_are_strict_and_say_they_take_no_arguments() -> None:
    seen: dict[str, Any] = {}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.update({t.name: t for t in info.function_tools})
        seen["instructions"] = info.instructions
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _decision())])

    _provider(fn).decide(_ctx())
    for name in READ_TOOLS:
        tool = seen[name]
        assert tool.parameters_json_schema == {"additionalProperties": False, "properties": {}, "type": "object"}
        assert "no arguments" in (tool.description or "").lower() and "{}" in (tool.description or "")
    assert "no arguments" in seen["instructions"].lower()


def test_extra_argument_is_still_rejected_and_never_executed() -> None:
    calls = {"n": 0}
    retry: list[RetryPromptPart] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            return ModelResponse(parts=[ToolCallPart("get_risk_status", {"reason": "check risk"})])
        retry.extend(p for p in messages[-1].parts if isinstance(p, RetryPromptPart))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, _decision())])

    validated, prov = _provider(fn).decide(_ctx())
    assert [(c.tool_name, c.is_error) for c in prov.tool_calls] == [("get_risk_status", True)]
    assert retry and "extra_forbidden" in str(retry[0].content)
    assert validated.status.value == "VALID"


def test_evaluated_prompt_is_frozen_and_the_fix_is_a_new_version() -> None:
    """decision_v1 is exactly what the published NineRouter evaluation used; the no-argument wording lives in v2."""
    import hashlib

    from wallex_agent.agent.decision_agent import load_prompt
    from wallex_agent.runtime.config import AgentSettings

    v1, v1_sha = load_prompt("decision_v1")
    assert hashlib.sha256(v1.encode()).hexdigest() == v1_sha
    assert v1_sha == "4627b301b1cba7cad68438beb66375dd8cbba8ce145c2df0d49a0d9382e659c1"
    assert "no arguments" not in v1
    assert AgentSettings(decision_provider="LLM", model_id="x").prompt_version == "decision_v2"
    assert "no arguments" in load_prompt("decision_v2")[0]
