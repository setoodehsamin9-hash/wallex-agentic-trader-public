"""Phase 05 — GPT-6.1 Sol on REAL F2 candidates (NineRouter only; no venue; zero Wallex writes; opt-in real model).

  NINEROUTER_API_KEY_FILE=/path/outside/repo uv run python scripts/phase05_gpt61_f2_cases.py

The existing QA contexts are C1 15m candidates (economically poor). This adds the CURRENT strategy: every F2
candidate the kernel produced on the latest VALIDATION slices (V6+V7, 2026-08-20..10-13, BASELINE backtest on the
hash-checked 1h pack), each sent once to `cx/gpt-6.1-sol` through the unchanged LlmDecisionProvider/validator path.
Records action, chosen candidate, validation status, latency, tool calls, responder (pinning), the deterministic
baseline's choice and the realized outcome of the baseline trade (descriptive only; tiny sample). Prompts unchanged.
"""

import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wallex_agent.agent.baseline import BaselineSelector  # noqa: E402
from wallex_agent.agent.context import DecisionContext  # noqa: E402
from wallex_agent.agent.decision_agent import LlmDecisionProvider  # noqa: E402
from wallex_agent.agent.ninerouter import build_model, load_settings, resolve_api_key  # noqa: E402
from wallex_agent.contracts import Environment, FeatureSet, MarketSnapshot, SignalCandidate  # noqa: E402
from wallex_agent.journal.store import JournalStore  # noqa: E402
from wallex_agent.market.dataset import load_dataset  # noqa: E402
from wallex_agent.runtime.backtest import run_backtest  # noqa: E402
from wallex_agent.runtime.config import AgentSettings, load_run_config  # noqa: E402

MODEL = "cx/gpt-6.1-sol"
OUT = REPO / "reports" / "phase-05" / "ai" / "GPT61_F2_CASES.json"


def f2_contexts() -> tuple[list[DecisionContext], dict[str, Any]]:
    cfg = load_run_config(REPO / "config/experiments/phase04r2/base-4h-f2.toml")
    ds = load_dataset(REPO / "data/raw/historical-pack/export/datasets/1h", expected_resolution_seconds=3600)
    store = JournalStore(Path(tempfile.mkdtemp()) / "f2.sqlite")
    rt = run_backtest(cfg, ds, store, BaselineSelector(), run_id="f2-src",
                      period=(datetime(2026, 8, 20, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC))).runtime
    outcomes = {t.decision_id: {"net_pnl_tmn": str(t.net_pnl_tmn), "exit_reason": t.exit_reason,
                                "move_bps": str(((t.avg_exit_price / t.avg_entry_price) - 1) * 10000)} for t in rt.ledger.trades}
    out: list[DecisionContext] = []
    for d in store.decisions("f2-src"):
        if not d.candidates or d.features_ref is None:
            continue
        raw = store.get_payload(d.features_ref)
        if raw is None:
            continue
        c = json.loads(raw)
        snap = MarketSnapshot.model_validate(c["snapshot"])
        out.append(DecisionContext(snapshot=snap, features=FeatureSet.model_validate(c["features"]),
                                   candidates=tuple(SignalCandidate.model_validate(x) for x in c["candidates"]), positions=(),
                                   risk_status={"note": "f2 qa replay"}, recent_decisions=(), now=snap.as_of,
                                   deadline=snap.as_of + timedelta(seconds=90), run_id="f2-qa",
                                   decision_key=f"{snap.symbol}@{snap.as_of.isoformat()}"))
    by_key = {f"{d.symbol}@{d.cycle_id.split('@')[1]}": outcomes.get(d.decision_id) for d in store.decisions("f2-src")
              if d.candidates}
    return out, by_key


def main() -> int:
    key = resolve_api_key()
    settings = load_settings(model_id=MODEL, timeout_s=60)
    if settings.model_id != MODEL:
        print("refusing: model override differs from the pinned id", file=sys.stderr)
        return 2
    contexts, outcomes = f2_contexts()
    store = JournalStore(Path(tempfile.mkdtemp()) / "qa.sqlite")
    agent = AgentSettings(decision_provider="LLM", model_id=MODEL, decision_deadline_s=60, max_model_requests=4,
                          max_tool_calls=4, output_retries=1, use_wallex_mcp_tools=False)
    provider = LlmDecisionProvider(build_model(settings, key), model_label=MODEL, settings=agent, store=store, mcp_toolset=None,
                                   stream=settings.stream, secret_literals=(key,), environment=Environment.BACKTEST)
    rows: list[dict[str, Any]] = []
    for ctx in contexts:
        v, prov = provider.decide(ctx)
        dec = v.decision
        offered = [c.candidate_id for c in ctx.candidates]
        rows.append({"case": ctx.decision_key, "status": v.status.value, "action": v.effective_action.value,
                     "candidate": dec.selected_candidate_id if dec else None, "offered": offered,
                     "invalid_reference": bool(dec and dec.selected_candidate_id and dec.selected_candidate_id not in offered),
                     "latency_ms": prov.latency_ms, "tool_calls": len(prov.tool_calls), "responder": prov.model_id,
                     "rationale": (dec.brief_rationale if dec else None), "baseline_choice": offered[0],
                     "baseline_trade_outcome": outcomes.get(ctx.decision_key)})
    n = len(rows)
    lat = sorted(r["latency_ms"] for r in rows)
    responders = sorted({r["responder"] for r in rows if r["responder"]})
    result = {"model_id": MODEL, "cases": n, "valid": sum(r["status"] == "VALID" for r in rows),
              "entries_selected": sum(r["action"] == "ENTER" for r in rows), "wait_rate": sum(r["action"] == "WAIT" for r in rows) / n if n else None,
              "invalid_references": sum(r["invalid_reference"] for r in rows), "tool_calls": sum(r["tool_calls"] for r in rows),
              "latency_ms_p50": lat[n // 2] if n else None, "latency_ms_max": lat[-1] if n else None, "responders": responders,
              "pinning": "PINNED" if responders and all("gpt-6.1-sol" in r for r in responders) else "FALLBACK_DETECTED",
              "rows": rows, "real_wallex_writes": 0, "private_wallex_calls": 0,
              "note": "descriptive: tiny sample; baseline outcome is the realized backtest trade of the deterministic selector"}
    text = json.dumps(result, ensure_ascii=False, indent=1, default=str)
    if key in text:
        raise RuntimeError("credential redaction failed; artifact refused")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=1, default=str))
    for r in rows:
        print(r["case"], r["status"], r["action"], r["latency_ms"], r["baseline_trade_outcome"], "|", (r["rationale"] or "")[:110])
    return 0 if result["pinning"] == "PINNED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
