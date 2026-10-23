"""Layer 3 (opt-in, `uv run pytest -m ninerouter`): the integrated candidate path with the REAL NineRouter agent.

The model may legitimately WAIT; that is recorded, never overridden. What must hold either way: it only selects an
offered candidate, risk decides, execution is the SHADOW simulator, and the recorded output replays identically.
"""

import os
from pathlib import Path

import pytest
from support.candidate_path import dataset, run_case, shadow_cfg

from wallex_agent.agent.context import DecisionProvider
from wallex_agent.agent.ninerouter import NineRouterNotConfigured, resolve_api_key
from wallex_agent.contracts import Environment
from wallex_agent.journal.store import JournalStore

pytestmark = pytest.mark.ninerouter


def _configured() -> bool:
    try:
        resolve_api_key()
        return True
    except NineRouterNotConfigured:
        return False


@pytest.mark.skipif(not _configured(), reason="NineRouter credentials not configured (Layer 3 only)")
def test_real_agent_on_a_real_candidate_is_safe_and_replayable(tmp_path: Path) -> None:
    from wallex_agent.agent.decision_agent import LlmDecisionProvider
    from wallex_agent.agent.ninerouter import build_model, load_settings

    model_id = os.environ.get("NINEROUTER_MODEL", "cx/gpt-6.1-sol")
    cfg = shadow_cfg(**{"agent.decision_provider": "LLM", "agent.model_id": model_id})
    settings = load_settings(model_id=model_id, timeout_s=cfg.agent.decision_deadline_s)
    key = resolve_api_key()

    def decider(store: JournalStore) -> DecisionProvider:
        return LlmDecisionProvider(build_model(settings, key), model_label=settings.model_id, settings=cfg.agent, store=store,
                                   stream=settings.stream, secret_literals=(key,), environment=Environment.SHADOW)

    ev = run_case(cfg, dataset(tmp_path), tmp_path / "proof.sqlite", "ninerouter", decider)
    src = ev["source"]
    assert src["candidates"] and src["provenance"] == "LLM"
    assert src["selected_candidate_id"] in (None, *src["candidates"])
    if src["effective_action"] == "ENTER":
        assert src["risk_outcome"] is not None and src["venue_fill_source"] == "SHADOW_SIM"
    else:
        assert src["simulated_submissions"] == 0
    assert ev["replay_matches"] and ev["real_wallex_writes"] == 0
    assert key not in (tmp_path / "proof.sqlite").read_bytes().decode(errors="ignore")
