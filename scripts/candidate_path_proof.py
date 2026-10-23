"""Integrated candidate-path evidence (Phase 03 integration): writes reports/phase-03/ai/INTEGRATED_CANDIDATE_PATH.json.

  uv run python scripts/candidate_path_proof.py [OUT.json]

Cases: B0 deterministic selector; SCRIPTED model through the real PydanticAI agent wrapper (labelled, never an AI
decision); real NineRouter agent only when NINEROUTER_API_KEY[_FILE] is configured (otherwise recorded as NOT_RUN).
No Wallex client is constructed; execution is the SHADOW simulator only. Same scenario as tests/runtime/test_candidate_path.py.
"""

import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from support.candidate_path import dataset, run_case, scripted_selector, shadow_cfg  # noqa: E402

from wallex_agent.agent.baseline import BaselineSelector  # noqa: E402
from wallex_agent.agent.context import DecisionProvider  # noqa: E402
from wallex_agent.agent.decision_agent import LlmDecisionProvider  # noqa: E402
from wallex_agent.agent.ninerouter import NineRouterNotConfigured, build_model, load_settings, resolve_api_key  # noqa: E402
from wallex_agent.contracts import Environment  # noqa: E402
from wallex_agent.journal.export import SECRET_PATTERNS, known_secret_literals  # noqa: E402
from wallex_agent.journal.store import JournalStore  # noqa: E402
from wallex_agent.runtime.config import AgentSettings  # noqa: E402


def main() -> int:
    out: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat(), "scenario": "synthetic BTCTMN C1 breakout at 2026-10-03T06:15Z, "
                           "SHADOW config, ShadowVenue IOC fills on a 1-level book", "cases": []}
    used_keys: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ds = dataset(root)

        def b0(store: JournalStore) -> DecisionProvider:
            return BaselineSelector()

        def scripted(store: JournalStore) -> DecisionProvider:
            return LlmDecisionProvider(scripted_selector(), model_label="scripted-selector", provider_label="scripted", store=store,
                                       stream=False,
                                       settings=AgentSettings(decision_provider="LLM", model_id="scripted"), environment=Environment.SHADOW)

        out["cases"].append(run_case(shadow_cfg(), ds, root / "b0.sqlite", "b0", b0))
        out["cases"].append({**run_case(shadow_cfg(), ds, root / "scripted.sqlite", "scripted-llm", scripted),
                             "note": "SCRIPTED FunctionModel selecting the first offered candidate; proves the execution path, not AI judgement"})
        try:
            key = resolve_api_key()
        except NineRouterNotConfigured as exc:
            out["cases"].append({"label": "ninerouter", "status": "NOT_RUN", "reason": str(exc)})
        else:
            used_keys.append(key)
            model_id = os.environ.get("NINEROUTER_MODEL", "cx/gpt-6.1-sol")  # Phase 05: GPT-6.1 Sol only, no fallback
            cfg = shadow_cfg(**{"agent.decision_provider": "LLM", "agent.model_id": model_id})
            settings = load_settings(model_id=model_id, timeout_s=cfg.agent.decision_deadline_s)

            def real(store: JournalStore) -> DecisionProvider:
                return LlmDecisionProvider(build_model(settings, key), model_label=settings.model_id, settings=cfg.agent, store=store,
                                           stream=settings.stream, secret_literals=(key,), environment=Environment.SHADOW)

            out["cases"].append(run_case(cfg, ds, root / "ninerouter.sqlite", "ninerouter", real))
    text = json.dumps(out, ensure_ascii=False, indent=1)
    literals = [*known_secret_literals(), *used_keys]
    if any(lit in text for lit in literals) or any(p.search(text) for p in SECRET_PATTERNS):
        raise SystemExit("refusing to write evidence: secret material detected")
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "reports" / "phase-03" / "ai" / "INTEGRATED_CANDIDATE_PATH.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
