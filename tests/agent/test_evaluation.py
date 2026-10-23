from pathlib import Path
from typing import Any
import os

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from support.agent_context import decision_context

from wallex_agent.agent.decision_agent import LlmDecisionProvider
from wallex_agent.journal.store import JournalStore
from wallex_agent.runtime.config import AgentSettings


def test_native_mlflow_evaluation_persists_dataset_scorers_and_safe_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_DISABLE_TELEMETRY", "true")
    mlflow: Any = pytest.importorskip("mlflow")
    from wallex_agent.agent.evaluation import evaluate_provider, evaluation_records

    def no_async_exporter() -> None:
        raise RuntimeError("native local evaluation logs synchronously; no async exporter exists")

    monkeypatch.setattr(mlflow, "flush_trace_async_logging", no_async_exporter)

    monkeypatch.setenv("MLFLOW_GENAI_EVAL_MAX_WORKERS", "1")
    ctx = decision_context()

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returned: list[ToolReturnPart] = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
        if not returned:
            return ModelResponse(parts=[ToolCallPart("get_risk_status", {})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
            "snapshot_id": "qa-snapshot", "action": "ENTER", "selected_candidate_id": "qa-candidate",
            "regime_assessment": "TRENDING_UP", "confidence": "MEDIUM", "reason_codes": ["BREAKOUT_CONFIRMED"],
            "evidence_feature_ids": ["BTCTMN.margin.16"], "brief_rationale": "Supplied breakout evidence; virtual risk checked."})])

    store = JournalStore(tmp_path / "journal.sqlite")
    provider = LlmDecisionProvider(FunctionModel(fn), model_label="scripted", settings=AgentSettings(), store=store, stream=False)
    records = evaluation_records([ctx])[:1]
    result = evaluate_provider(provider, records, store=store, state_dir=tmp_path / "mlflow", phase="unit", model_id="scripted")
    assert result["dataset_id"]
    assert result["trace_count"] >= 1
    assert set(result["scorers"]) >= {"output_contract", "candidate_discipline", "evidence_references", "tool_boundary", "failure_to_wait"}
    assert all(value is not None for value in result["metrics"].values())
    assert result["rows"][0]["action"] == "ENTER"
    assert result["rows"][0]["tools"][0]["name"] == "get_risk_status"
    assert result["rows"][0]["model_requests"] == 2
    assert len(result["rows"]) == 1  # evaluating a dataset must not secretly run first prediction twice
    datasets = mlflow.genai.datasets.search_datasets(experiment_ids=[result["experiment_id"]])
    assert any(d.dataset_id == result["dataset_id"] for d in datasets)


def test_evaluation_disables_background_network_before_importing_mlflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from wallex_agent.agent import evaluation

    monkeypatch.delenv("MLFLOW_DISABLE_TELEMETRY", raising=False)

    def guarded_import(name: str) -> Any:
        disabled = os.environ.get("MLFLOW_DISABLE_TELEMETRY") == "true"
        assert disabled, "MLflow background telemetry must be disabled before importing it"
        raise ImportError("simulated optional dependency absence")

    monkeypatch.setattr(evaluation, "import_module", guarded_import)
    with pytest.raises(RuntimeError, match="evaluation needs"):
        evaluation.evaluate_provider(None, [], store=None, state_dir=tmp_path, phase="offline", model_id="scripted")  # type: ignore[arg-type]


def test_operational_dataset_variants_are_explicit_and_do_not_mutate_source() -> None:
    from wallex_agent.agent.evaluation import evaluation_records

    source = decision_context()
    before = source.candidates[0].model_dump_json()
    records = evaluation_records([source])
    assert {r["expectations"]["scenario"] for r in records} >= {"clear", "conflicting", "weak", "stale", "read_tool", "write_temptation"}
    assert source.candidates[0].model_dump_json() == before
    conflicting = next(r for r in records if r["expectations"]["scenario"] == "conflicting")
    assert len(conflicting["inputs"]["context"]["candidates"]) == 2


def test_summary_counts_invalid_raw_candidates_even_when_validator_waits() -> None:
    from wallex_agent.agent.evaluation import summarize_evaluation

    rows = [{"status": "VALID", "schema_valid": True, "candidate": "c1", "offered_ids": ["c1"], "action": "ENTER",
             "baseline_action": "ENTER", "baseline_candidate": "c1", "latency_ms": 100, "model_requests": 1,
             "tools": [{"name": "get_risk_status", "is_error": False}], "write_tools_exposed": [], "responding_model": "qa"},
            {"status": "UNKNOWN_REFERENCE", "schema_valid": True, "candidate": "invented", "offered_ids": ["c1"], "action": "WAIT",
             "baseline_action": "ENTER", "baseline_candidate": "c1", "latency_ms": 300, "model_requests": 2,
             "tools": [], "write_tools_exposed": [], "responding_model": "qa"}]
    summary = summarize_evaluation(rows)
    assert summary["calls"] == 2 and summary["valid"] == 1
    assert summary["candidate_discipline_rate"] == 0.5
    assert summary["invalid_reference_count"] == 1 and summary["wait_rate"] == 0.5
    assert summary["baseline_disagreements"] == 1
    assert summary["approximate_model_requests"] == 3
