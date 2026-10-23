"""Integrated proof that a real C1 candidate travels the whole safe path to a SHADOW simulated fill, is journaled,
rebuilds from the state log, and replays identically. Deterministic B0 and a SCRIPTED model through the real
PydanticAI agent wrapper; the real-NineRouter variant lives in tests/ninerouter (opt-in)."""

from collections.abc import Callable
from pathlib import Path

import pytest
from support.candidate_path import dataset, run_case, scripted_selector

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.agent.context import DecisionProvider
from wallex_agent.agent.decision_agent import LlmDecisionProvider
from wallex_agent.contracts import Environment
from wallex_agent.journal.store import JournalStore
from wallex_agent.runtime.config import AgentSettings, RunConfig


@pytest.mark.parametrize("label", ["b0", "scripted-llm"])
def test_candidate_reaches_shadow_fill_and_replays(make_cfg: Callable[..., RunConfig], tmp_path: Path, label: str) -> None:
    cfg = make_cfg(**{"run.environment": Environment.SHADOW})

    def decider(store: JournalStore) -> DecisionProvider:
        if label == "b0":
            return BaselineSelector()
        return LlmDecisionProvider(scripted_selector(), model_label="scripted-selector", provider_label="scripted", store=store,
                                   stream=False,
                                   settings=AgentSettings(decision_provider="LLM", model_id="scripted"),
                                   environment=Environment.SHADOW)

    ev = run_case(cfg, dataset(tmp_path), tmp_path / "proof.sqlite", label, decider)
    src = ev["source"]
    assert src["candidates"], "the scenario must contain a real C1 candidate"
    assert src["selected_candidate_id"] in src["candidates"]
    assert src["model_id"] is None if label == "b0" else src["model_id"] == "scripted:scripted-selector"
    assert src["validation"] == "VALID" and src["risk_outcome"] in ("APPROVE", "REDUCE") and not src["risk_failed_checks"]
    assert src["status"] == "FILLED" and src["execution_states"][:2] == ["INTENT", "SUBMITTING"]
    assert src["venue_environment"] == "SHADOW" and src["venue_fill_source"] == "SHADOW_SIM"
    assert src["simulated_submissions"] == 1 and src["position_qty"] and src["stop_price"]
    assert src["state_log_replays_to_ledger"]
    assert ev["replay"]["provenance"] == "REPLAY" and ev["replay_matches"], ev
    assert ev["real_wallex_writes"] == 0
