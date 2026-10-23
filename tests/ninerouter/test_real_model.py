"""Layer 3 (opt-in): `uv run pytest -m ninerouter`. Real model through the owner's NineRouter only."""

import os

import pytest

from wallex_agent.agent.ninerouter import NineRouterNotConfigured, resolve_api_key

pytestmark = pytest.mark.ninerouter


def _configured() -> bool:
    try:
        resolve_api_key()
        return True
    except NineRouterNotConfigured:
        return False


@pytest.mark.skipif(not _configured(), reason="NineRouter credentials not configured (Layer 3 only)")
def test_real_model_smoke_structured_and_disciplined(tmp_path: object) -> None:
    from pathlib import Path

    from wallex_agent.agent.evaluation import run_eval

    out = run_eval(models=[os.environ.get("NINEROUTER_MODEL", "cx/gpt-6.1-sol")], repeats=1, out_path=Path(str(tmp_path)) / "e.json",
                   mcp=False, n_cases=2)
    agg = next(iter(out["aggregate"].values()))
    assert agg["candidate_discipline"] and agg["tools_within_allowlist"] and agg["all_failures_waited"]
    assert agg["valid"] >= 1
