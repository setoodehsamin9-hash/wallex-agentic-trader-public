"""Phase 05 — GPT-6.1 Sol agent QA through the owner's NineRouter ONLY (opt-in, real model, zero Wallex writes).

  NINEROUTER_API_KEY_FILE=/path/outside/repo uv run --group evaluation python scripts/phase05_gpt61_qa.py

Strict model pinning: the exact id `cx/gpt-6.1-sol` must be listed by the router's own `GET /v1/models`; any
NINEROUTER_MODEL override that differs is refused; every responding model must be GPT-6.1 Sol, else the run is
FALLBACK_DETECTED and fails. Unavailable model -> UNAVAILABLE (no silent fallback). The key is read by the existing
loader (outside the repo only) and never printed, journaled or written; the artifact is secret-scanned.
Reuses the existing bounded harness (`agent/evaluation.py`: valid selection, WAIT, stale/unknown references, read-only
public tools incl. zero-argument tools, write temptation, timeout/network/unknown-model faults -> WAIT).
"""

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wallex_agent.agent.evaluation import run_eval  # noqa: E402
from wallex_agent.agent.ninerouter import DEFAULT_BASE_URL  # noqa: E402

MODEL = "cx/gpt-6.1-sol"
OUT = REPO / "reports" / "phase-05" / "ai" / "GPT61_NINEROUTER_EVAL.json"


def catalog_ids(base: str) -> list[str]:
    with urllib.request.urlopen(f"{base}/models", timeout=30) as resp:  # public listing, no credential sent
        data: dict[str, Any] = json.loads(resp.read())
    return [str(m["id"]) for m in data.get("data", [])]


def main() -> int:
    override = os.environ.get("NINEROUTER_MODEL")
    if override and override != MODEL:
        print(f"refusing: NINEROUTER_MODEL={override!r} differs from the pinned {MODEL}", file=sys.stderr)
        return 2
    base = os.environ.get("NINEROUTER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    ids = catalog_ids(base)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if MODEL not in ids:
        OUT.write_text(json.dumps({"model_id": MODEL, "status": "UNAVAILABLE", "catalog_size": len(ids)}, indent=1) + "\n")
        print(f"{MODEL} not in the NineRouter catalog: UNAVAILABLE (no fallback)")
        return 3
    result = run_eval(models=[MODEL], repeats=1, out_path=OUT, mcp=True, n_cases=4)
    agg = result["aggregate"][MODEL]
    responders = [r for r in agg.get("responders", [])]
    fallback = [r for r in responders if "gpt-6.1-sol" not in r]
    result["pinning"] = {"requested": MODEL, "in_catalog": True, "responders": responders,
                         "status": "FALLBACK_DETECTED" if fallback or not responders else "PINNED"}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n")
    keys = ("calls", "valid", "structured_output_rate", "invalid_reference_count", "wait_rate", "latency_ms_p50",
            "latency_ms_p95", "tool_calls", "tool_errors", "tools_within_allowlist", "all_failures_waited", "status_counts")
    print(json.dumps({"model_id": MODEL, **{k: agg.get(k) for k in keys}, "faults": agg.get("faults"),
                      "pinning": result["pinning"]}, indent=1, default=str))
    return 0 if result["pinning"]["status"] == "PINNED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
