"""Phase 05 final pre-LIVE — F2-aware agent: fresh evaluation, role selection, one-shot 417 holdout (zero Wallex writes).

  uv run python scripts/phase05_f2_alignment.py build                 # fresh case set from VALIDATION V1..V5 (no model)
  uv run python scripts/phase05_f2_alignment.py faults                # scripted failure cases for both roles (no network)
  NINEROUTER_API_KEY_FILE=/outside/repo uv run python scripts/phase05_f2_alignment.py run --role VETO --shard 0 --shards 2
  uv run python scripts/phase05_f2_alignment.py summarize             # selection rule fixed in the case file -> FROZEN.json
  NINEROUTER_API_KEY_FILE=/outside/repo uv run python scripts/phase05_f2_alignment.py holdout --shard 0 --shards 4
  uv run python scripts/phase05_f2_alignment.py holdout-summary       # changed decisions + counterfactual replay P&L

Real calls: NineRouter only, exact model `cx/gpt-6.1-sol` (refused otherwise), BACKTEST environment, no MCP tools, no venue.
The 417 F2 cases of V6+V7 (2026-08-20..10-13, scripts/phase05_gpt61_f2_cases.py) are a HOLDOUT: they were not used to
write the prompts, the brief or the selection rule, `holdout` refuses to run before FROZEN.json exists and refuses to
overwrite a shard result (run once), and nothing is tuned afterwards.

Case labels are ex-ante (strategy rules and feature values only, never realized outcomes):
  valid (expected ENTER): real F2 candidates, classed by margin over the entry band and context
  invalid (expected WAIT): the same kind of real candidate with one supplied fact made invalid (stale/missing data,
  entry rule broken, shock-edge volatility with a falling close, unsafe execution state), or no candidate at all.
"""

import argparse
import dataclasses
import hashlib
import json
import random
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Literal

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from wallex_agent.agent.baseline import BaselineSelector  # noqa: E402
from wallex_agent.agent.context import DecisionContext  # noqa: E402
from wallex_agent.agent.decision_agent import LlmDecisionProvider, load_prompt  # noqa: E402
from wallex_agent.agent.replay import ReplayDecisionProvider  # noqa: E402
from wallex_agent.contracts import (  # noqa: E402
    AgentDecision,
    Environment,
    FeatureSet,
    MarketSnapshot,
    QualityFlag,
    SignalCandidate,
    StrategyBrief,
)
from wallex_agent.journal.store import JournalStore  # noqa: E402
from wallex_agent.market.dataset import load_dataset  # noqa: E402
from wallex_agent.runtime.backtest import run_backtest  # noqa: E402
from wallex_agent.runtime.config import AgentSettings, load_run_config  # noqa: E402
from wallex_agent.strategies import BriefingStrategy  # noqa: E402

MODEL = "cx/gpt-6.1-sol"
CFG = REPO / "config/experiments/phase04r2/base-4h-f2.toml"
PACK_1H = REPO / "data/raw/historical-pack/export/datasets/1h"
OUT = REPO / "reports/phase-05/ai/f2-alignment"
CASES = OUT / "FRESH_CASES.json"
FROZEN = OUT / "FROZEN.json"
SEED = 20261023
FRESH_SLICES = [("V1", "2025-03-01", "2025-08-01"), ("V2", "2025-08-01", "2026-01-01"), ("V3", "2026-01-01", "2026-06-01"),
                ("V4", "2026-06-01", "2026-07-24"), ("V5", "2026-07-24", "2026-08-20")]
HOLDOUT = (datetime(2026, 8, 20, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC))
PROMPTS = {"SELECTOR": "decision_v3", "VETO": "veto_v1"}
RISK_STATUS = {"kill_switch": False, "entries_allowed": True}
VALID_QUOTA = {"strong": 10, "typical": 6, "weak": 10, "conflicting": 10, "entry_point": 8}
MUTATIONS = ("stale_flags", "missing_evidence", "entry_rule_broken", "regime_conflict", "execution_unsafe")
PER_MUTATION, NO_CANDIDATE = 4, 3
SELECTION_RULE = (
    "Fixed before any real call. Hard gates for a role: structured output on every answered call, candidate discipline "
    "(only offered ids), zero silent fallback (every responder is gpt-6.1-sol), every failure WAITs. Score = "
    "(valid-entry preservation + invalid-entry veto rate) / 2 on effective actions. Pick the higher score; within 0.05 "
    "pick VETO (smaller authority). If the picked role vetoes < 50% of invalid cases, the AI is reported as not causal "
    "enough (PARTIAL).")


def _d(x: str) -> Decimal:
    return Decimal(x)


def _brief_sha(briefs: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(briefs, sort_keys=True).encode()).hexdigest()


def _fmap(fs: FeatureSet) -> dict[str, Decimal | None]:
    return {f.feature_id: f.value for f in fs.features}


def _ctx_from(raw: dict[str, Any], brief: StrategyBrief | None, *, run_id: str, risk: dict[str, Any] | None = None) -> DecisionContext:
    snap = MarketSnapshot.model_validate(raw["snapshot"])
    return DecisionContext(snapshot=snap, features=FeatureSet.model_validate(raw["features"]),
                           candidates=tuple(SignalCandidate.model_validate(x) for x in raw["candidates"]), positions=(),
                           risk_status=dict(risk if risk is not None else RISK_STATUS), recent_decisions=(), now=snap.as_of,
                           deadline=snap.as_of + timedelta(seconds=90), run_id=run_id,
                           decision_key=f"{snap.symbol}@{snap.as_of.isoformat()}", strategy_brief=brief)


def source_contexts(period: tuple[datetime, datetime], run_id: str) -> tuple[list[dict[str, Any]], dict[str, StrategyBrief]]:
    """Every F2 candidate context the BASELINE backtest produced in `period`, with its strategy brief and entry flag."""
    cfg = load_run_config(CFG)
    ds = load_dataset(PACK_1H, expected_resolution_seconds=3600)
    store = JournalStore(Path(tempfile.mkdtemp()) / "src.sqlite")
    rt = run_backtest(cfg, ds, store, BaselineSelector(), run_id=run_id, period=period).runtime
    entered = {t.decision_id for t in rt.ledger.trades}
    strategy = rt.strategy
    assert isinstance(strategy, BriefingStrategy)
    briefs = {s: strategy.brief(s, cfg.run.bar_interval_seconds) for s in cfg.run.symbols}
    out: list[dict[str, Any]] = []
    for d in store.decisions(run_id):
        if not d.candidates or d.features_ref is None:
            continue
        raw = store.get_payload(d.features_ref)
        if raw is None:
            continue
        c = json.loads(raw)
        snap = MarketSnapshot.model_validate(c["snapshot"])
        out.append({"raw": {k: c[k] for k in ("snapshot", "features", "candidates")}, "symbol": d.symbol,
                    "key": f"{snap.symbol}@{snap.as_of.isoformat()}", "baseline_entered": d.decision_id in entered,
                    "decision_id": d.decision_id})
    return out, briefs


def _klass(raw: dict[str, Any]) -> tuple[str, dict[str, str]]:
    fs = FeatureSet.model_validate(raw["features"])
    sym = raw["snapshot"]["symbol"]
    f = _fmap(fs)
    close, ema, atr = f[f"{sym}.close.1"], f[f"{sym}.ema.48"], f[f"{sym}.atr.14"]
    vol, lag42 = f[f"{sym}.vol_rank.14"], f[f"{sym}.close_lag.42"]
    assert close is not None and ema is not None and atr and vol is not None and lag42 is not None
    margin = (close - ema) / atr - _d("0.5")
    facts = {"margin_over_band_atr": f"{margin:.2f}", "vol_rank": f"{vol:.2f}", "close_vs_lag42": "below" if close < lag42 else "above"}
    if close < lag42 or vol >= _d("0.80"):
        return "conflicting", facts
    if margin >= 1:
        return "strong", facts
    return ("weak" if margin < _d("0.25") else "typical"), facts


def _set_feature(raw: dict[str, Any], fid: str, value: Decimal | None) -> None:
    for f in raw["features"]["features"]:
        if f["feature_id"] == fid:
            f["value"] = None if value is None else str(value)
            return
    raise KeyError(fid)


def mutate(raw: dict[str, Any], kind: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    raw = json.loads(json.dumps(raw))
    sym = raw["snapshot"]["symbol"]
    f = _fmap(FeatureSet.model_validate(raw["features"]))
    close, ema, atr = f[f"{sym}.close.1"], f[f"{sym}.ema.48"], f[f"{sym}.atr.14"]
    assert close is not None and ema is not None and atr is not None
    risk: dict[str, Any] | None = None
    if kind == "stale_flags":
        raw["snapshot"]["quality_flags"] = [QualityFlag.STALE.value, QualityFlag.GAP.value]
    elif kind == "missing_evidence":
        _set_feature(raw, f"{sym}.ema.48", None)
    elif kind == "entry_rule_broken":  # the shown close no longer clears EMA48 + 0.5 ATR
        _set_feature(raw, f"{sym}.close.1", (ema + _d("0.2") * atr).quantize(Decimal(1)))
    elif kind == "regime_conflict":  # shock-edge volatility with a falling close (allowed by the code filter, invalid in the brief)
        _set_feature(raw, f"{sym}.vol_rank.14", _d("0.93"))
        _set_feature(raw, f"{sym}.er_rank.42", _d("0.30"))
        _set_feature(raw, f"{sym}.close_lag.42", (close * _d("1.03")).quantize(Decimal(1)))
    elif kind == "execution_unsafe":
        risk = {**RISK_STATUS, "unresolved_orders": 1, "recovery_outcome": "RECONCILIATION_PENDING"}
    elif kind == "no_candidate":
        raw["candidates"] = []
    else:
        raise ValueError(kind)
    return raw, risk


def cmd_build(_: argparse.Namespace) -> int:
    rng = random.Random(SEED)
    pools: dict[str, list[dict[str, Any]]] = {k: [] for k in ("strong", "typical", "weak", "conflicting", "entry_point")}
    briefs: dict[str, StrategyBrief] = {}
    coverage: dict[str, int] = {}
    for name, a, b in FRESH_SLICES:
        t0 = time.monotonic()
        rows, briefs = source_contexts((datetime.fromisoformat(a).replace(tzinfo=UTC), datetime.fromisoformat(b).replace(tzinfo=UTC)),
                                       f"fresh-{name}")
        coverage[name] = len(rows)
        for r in rows:
            k, facts = _klass(r["raw"])
            item = {**r, "slice": name, "facts": facts, "natural_class": k}
            pools["entry_point" if r["baseline_entered"] else k].append(item)
        print(name, len(rows), f"{time.monotonic() - t0:.0f}s", flush=True)
    cases: list[dict[str, Any]] = []
    used: set[str] = set()
    for klass, n in VALID_QUOTA.items():
        pick = rng.sample(pools[klass], min(n, len(pools[klass])))
        for p in pick:
            used.add(p["key"])
            cases.append({"case_id": f"{klass}:{p['key']}", "group": "valid", "klass": klass, "expected": "ENTER", "slice": p["slice"],
                          "key": p["key"], "facts": p["facts"], "baseline_entered": p["baseline_entered"], "mutation": None,
                          "raw": p["raw"], "risk": RISK_STATUS})
    rest = [p for k in ("strong", "typical", "weak") for p in pools[k] if p["key"] not in used]
    base = rng.sample(rest, len(MUTATIONS) * PER_MUTATION + NO_CANDIDATE)
    kinds = [m for m in MUTATIONS for _ in range(PER_MUTATION)] + ["no_candidate"] * NO_CANDIDATE
    for p, kind in zip(base, kinds, strict=True):
        raw, risk = mutate(p["raw"], kind)
        cases.append({"case_id": f"{kind}:{p['key']}", "group": "invalid", "klass": kind, "expected": "WAIT", "slice": p["slice"],
                      "key": p["key"], "facts": p["facts"], "baseline_entered": p["baseline_entered"], "mutation": kind,
                      "raw": raw, "risk": risk or RISK_STATUS})
    doc = {"purpose": "fresh F2 agent-alignment evaluation set (not used to design prompts; holdout is V6+V7)",
           "seed": SEED, "slices": FRESH_SLICES, "holdout_excluded": [h.isoformat() for h in HOLDOUT],
           "candidate_contexts_per_slice": coverage, "pool_sizes": {k: len(v) for k, v in pools.items()},
           "valid_quota": VALID_QUOTA, "mutations": MUTATIONS, "per_mutation": PER_MUTATION, "no_candidate": NO_CANDIDATE,
           "labels": "ex-ante only; realized outcomes are not used", "selection_rule": SELECTION_RULE,
           "briefs": {s: b.model_dump(mode="json") for s, b in briefs.items()}, "cases": cases}
    OUT.mkdir(parents=True, exist_ok=True)
    CASES.write_text(json.dumps(doc, ensure_ascii=False, indent=0, default=str) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k not in ("cases", "briefs")}, indent=1, default=str))
    print("cases", len(cases), Counter(c["klass"] for c in cases))
    return 0


QUALIFY_MODELS = ("cx/gpt-6.1-sol", "cx/gpt-6-astra")
"""Models that may be (re)qualified for the frozen role; every call still goes through NineRouter only."""
QUALIFY_RULE = ("Fixed before the run: the candidate model qualifies for the FROZEN role/prompt when it passes the same hard "
                "gates and its re-validated score is >= the frozen model's VETO score - 0.05. Prompt and brief are unchanged.")


def _out(model: str) -> Path:
    return OUT if model == MODEL else OUT / model.replace("/", "_")


def _provider(role: Literal["SELECTOR", "VETO"], store: JournalStore, model: str = MODEL) -> tuple[LlmDecisionProvider, str]:
    from wallex_agent.agent.ninerouter import build_model, load_settings, resolve_api_key

    if model not in QUALIFY_MODELS:
        raise SystemExit(f"refusing: {model} is not an allowed evaluation model")
    key = resolve_api_key()
    settings = load_settings(model_id=model, timeout_s=60)
    if settings.model_id != model:
        raise SystemExit("refusing: NINEROUTER_MODEL override differs from the requested id")
    agent = AgentSettings(decision_provider="LLM", model_id=model, role=role, prompt_version=PROMPTS[role], decision_deadline_s=60,
                          max_model_requests=4, max_tool_calls=4, output_retries=1, use_wallex_mcp_tools=False)
    return LlmDecisionProvider(build_model(settings, key), model_label=model, settings=agent, store=store, mcp_toolset=None,
                               stream=settings.stream, secret_literals=(key,), environment=Environment.BACKTEST), key


def _row(ctx: DecisionContext, provider: LlmDecisionProvider, store: JournalStore, run_id: str) -> dict[str, Any]:
    v, prov = provider.decide(ctx)
    dec = v.decision
    offered = [c.candidate_id for c in ctx.candidates]
    shown = {f.feature_id for f in ctx.features.features}
    rec: dict[str, Any] = store.get_ref(run_id, f"llm:{ctx.decision_key}") or {}
    raw_out: dict[str, Any] | None = rec.get("output")
    return {"status": v.status.value, "action": v.effective_action.value, "detail": v.detail,
            "model_action": raw_out["action"] if raw_out else None,
            "candidate": dec.selected_candidate_id if dec else None, "offered": offered,
            "schema_valid": raw_out is not None,
            "discipline": dec is None or dec.selected_candidate_id is None or dec.selected_candidate_id in offered,
            "evidence_ok": dec is None or set(dec.evidence_feature_ids) <= shown,
            "reason_codes": [r.value for r in dec.reason_codes] if dec else [],
            "evidence": list(dec.evidence_feature_ids) if dec else [], "latency_ms": prov.latency_ms,
            "tools": [{"name": t.tool_name, "is_error": t.is_error} for t in prov.tool_calls],
            "offered_tools": rec.get("offered_tools", []), "responder": prov.model_id,
            "rationale": dec.brief_rationale if dec else None, "raw_output": raw_out,
            "prompt_version": prov.prompt_version, "prompt_sha256": prov.prompt_sha256}


def _redacted(text: str, key: str) -> str:
    if key and key in text:
        raise RuntimeError("credential redaction failed; artifact refused")
    return text


def cmd_run(a: argparse.Namespace) -> int:
    doc = json.loads(CASES.read_text())
    role: Literal["SELECTOR", "VETO"] = a.role
    store = JournalStore(Path(tempfile.mkdtemp()) / "eval.sqlite")
    run_id = f"eval-{role}-{a.shard}"
    provider, key = _provider(role, store, a.model)
    rows: list[dict[str, Any]] = []
    for i, case in enumerate(doc["cases"]):
        if i % a.shards != a.shard:
            continue
        brief = StrategyBrief.model_validate(doc["briefs"][case["raw"]["snapshot"]["symbol"]])
        ctx = _ctx_from(case["raw"], brief, run_id=run_id, risk=case["risk"])
        row = {"case_id": case["case_id"], "group": case["group"], "klass": case["klass"], "expected": case["expected"],
               "slice": case["slice"], "baseline_entered": case["baseline_entered"], **_row(ctx, provider, store, run_id)}
        rows.append(row)
        print(role, case["case_id"], row["status"], row["action"], row["latency_ms"], row["reason_codes"], flush=True)
    out_dir = _out(a.model)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"run-{role}-{a.shard}of{a.shards}.json"
    out.write_text(_redacted(json.dumps({"role": role, "model_id": a.model, "rows": rows}, ensure_ascii=False, indent=0), key) + "\n")
    return 0


def cmd_faults(_: argparse.Namespace) -> int:
    """Scripted failure cases through the real LlmDecisionProvider + validator (FunctionModel; no network)."""
    from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    doc = json.loads(CASES.read_text())
    case = next(c for c in doc["cases"] if c["group"] == "valid")
    brief = StrategyBrief.model_validate(doc["briefs"][case["raw"]["snapshot"]["symbol"]])
    base = _ctx_from(case["raw"], brief, run_id="faults")
    cid = base.candidates[0].candidate_id
    sid = base.snapshot.snapshot_id

    def answer(**kw: Any) -> Any:
        out = {"snapshot_id": sid, "action": "ENTER", "selected_candidate_id": cid, "regime_assessment": "RANGE",
               "confidence": "MEDIUM", "reason_codes": ["TREND_CONFIRMED"], "evidence_feature_ids": [], "brief_rationale": "تست"}
        out.update(kw)

        def fn(_: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=out)])
        return fn

    def raises(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        raise ConnectionError("scripted provider outage")

    def prose(_: list[ModelMessage], __: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("I think you should buy.")])

    late = dataclasses.replace(base, deadline=base.now - timedelta(seconds=1))
    old = SignalCandidate.model_validate({**base.candidates[0].model_dump(), "created_at": base.now - timedelta(minutes=20),
                                          "expires_at": base.now - timedelta(seconds=1)})
    expired = dataclasses.replace(base, candidates=(old,))
    scenarios: list[tuple[str, Any, DecisionContext, str]] = [
        ("invalid_candidate_reference", answer(selected_candidate_id="f2-ghost"), base, "WAIT"),
        ("provider_failure", raises, base, "WAIT"),
        ("malformed_output", prose, base, "WAIT"),
        ("late_answer", answer(), late, "WAIT"),
        ("expired_candidate_enter", answer(), expired, "WAIT"),
        ("expired_candidate_unsupported_veto", answer(action="WAIT", selected_candidate_id=None, reason_codes=["WEAK_EVIDENCE"]),
         expired, "WAIT"),
        ("unknown_evidence_id", answer(evidence_feature_ids=["BTCTMN.made_up.1"]), base, "WAIT"),
        ("unsupported_veto", answer(action="WAIT", selected_candidate_id=None, reason_codes=["WEAK_EVIDENCE"]), base, "ROLE"),
    ]
    rows: list[dict[str, Any]] = []
    for role in ("SELECTOR", "VETO"):
        for name, fn, ctx, expected in scenarios:
            agent = AgentSettings(decision_provider="LLM", model_id="scripted", role=role, prompt_version=PROMPTS[role],
                                  decision_deadline_s=60, max_model_requests=4, max_tool_calls=4, output_retries=1)
            prov = LlmDecisionProvider(FunctionModel(fn), model_label="scripted", settings=agent, store=None, stream=False)
            v = prov.decide(ctx)[0]
            want = expected if expected != "ROLE" else ("WAIT" if role == "SELECTOR" else "ENTER")
            rows.append({"role": role, "case": name, "status": v.status.value, "action": v.effective_action.value,
                         "expected": want, "ok": v.effective_action.value == want})
            print(role, name, v.status.value, v.effective_action.value, "ok" if rows[-1]["ok"] else "MISMATCH")
    (OUT / "FAULTS.json").write_text(json.dumps({"network": False, "rows": rows, "all_ok": all(r["ok"] for r in rows)},
                                                ensure_ascii=False, indent=1) + "\n")
    return 0 if all(r["ok"] for r in rows) else 3


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if r["group"] == "valid"]
    invalid = [r for r in rows if r["group"] == "invalid"]
    answered = [r for r in rows if r["status"] not in ("PROVIDER_ERROR", "TIMEOUT", "BUDGET_EXCEEDED")]
    lat = sorted(r["latency_ms"] for r in rows)
    tools = [t for r in rows for t in r["tools"]]
    responders = sorted({r["responder"] for r in rows if r["responder"]})
    pres = sum(r["action"] == "ENTER" for r in valid) / len(valid) if valid else None
    veto = sum(r["action"] == "WAIT" for r in invalid) / len(invalid) if invalid else None
    by_class: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = by_class.setdefault(r["klass"], {"n": 0, "expected": r["expected"], "matched": 0, "model_wait": 0, "statuses": Counter()})
        b["n"] += 1
        b["matched"] += r["action"] == r["expected"]
        b["model_wait"] += r["model_action"] == "WAIT"
        b["statuses"][r["status"]] += 1
    entry_points = [r for r in valid if r["baseline_entered"]]
    return {
        "calls": len(rows), "structured_validity": sum(r["schema_valid"] for r in answered) / len(answered) if answered else None,
        "transport_failures": len(rows) - len(answered), "candidate_discipline": all(r["discipline"] for r in rows),
        "evidence_discipline": all(r["evidence_ok"] for r in rows), "wait_rate": sum(r["action"] == "WAIT" for r in rows) / len(rows),
        "model_wait_rate_on_valid": sum(r["model_action"] == "WAIT" for r in valid) / len(valid) if valid else None,
        "valid_entry_preservation": pres, "invalid_entry_veto": veto,
        "baseline_entry_points_preserved": f"{sum(r['action'] == 'ENTER' for r in entry_points)}/{len(entry_points)}",
        "veto_unsupported": sum(r["status"] == "VETO_UNSUPPORTED" for r in rows),
        "score": None if pres is None or veto is None else (pres + veto) / 2,
        "latency_ms_p50": int(median(lat)) if lat else None, "latency_ms_p95": lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))] if lat else None,
        "latency_ms_max": lat[-1] if lat else None, "tool_calls": len(tools), "tool_errors": sum(t["is_error"] for t in tools),
        "write_tools_offered": sorted({t for r in rows for t in r["offered_tools"] if t not in ("get_risk_status", "get_open_positions",
                                                                                               "get_recent_decisions")}),
        "responders": responders, "silent_fallback": not responders or any("gpt-6.1-sol" not in x for x in responders),
        "failures_waited": all(r["action"] == "WAIT" for r in rows if r["status"] not in ("VALID", "VETO_UNSUPPORTED")),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "veto_reasons": dict(Counter(c for r in rows if r["model_action"] == "WAIT" for c in r["reason_codes"])),
        "by_class": {k: {**v, "statuses": dict(v["statuses"])} for k, v in by_class.items()},
    }


def _revalidate(rows: list[dict[str, Any]], doc: dict[str, Any], role: Literal["SELECTOR", "VETO"]) -> list[dict[str, Any]]:
    """Re-check recorded model outputs offline with the CURRENT validator (no model call; outputs unchanged)."""
    from wallex_agent.agent.validator import validate

    cases = {c["case_id"]: c for c in doc["cases"]}
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["raw_output"] is None:
            out.append({**r, "status_recorded": r["status"], "action_recorded": r["action"]})
            continue
        case = cases[r["case_id"]]
        ctx = _ctx_from(case["raw"], StrategyBrief.model_validate(doc["briefs"][case["raw"]["snapshot"]["symbol"]]),
                        run_id="revalidate", risk=case["risk"])
        v = validate(AgentDecision.model_validate(r["raw_output"]), ctx, received_at=ctx.now + timedelta(milliseconds=r["latency_ms"]),
                     exit_enabled=False, role=role)
        out.append({**r, "status_recorded": r["status"], "action_recorded": r["action"], "status": v.status.value,
                    "action": v.effective_action.value, "detail": v.detail})
    return out


def cmd_summarize(_: argparse.Namespace) -> int:
    doc = json.loads(CASES.read_text())
    faults = json.loads((OUT / "FAULTS.json").read_text())
    result: dict[str, Any] = {"model_id": MODEL, "selection_rule": doc["selection_rule"], "roles": {}, "recorded_roles": {}}
    for role in ("SELECTOR", "VETO"):
        recorded = [r for p in sorted(OUT.glob(f"run-{role}-*.json")) for r in json.loads(p.read_text())["rows"]]
        if len(recorded) != len(doc["cases"]):
            raise SystemExit(f"{role}: {len(recorded)} rows for {len(doc['cases'])} cases; run every shard first")
        result["recorded_roles"][role] = {k: v for k, v in _metrics(recorded).items() if k != "by_class"}
        rows = _revalidate(recorded, doc, role)
        result.setdefault("revalidation_changes", {})[role] = [
            {"case_id": r["case_id"], "recorded": f"{r['status_recorded']}/{r['action_recorded']}", "now": f"{r['status']}/{r['action']}"}
            for r in rows if (r["status"], r["action"]) != (r["status_recorded"], r["action_recorded"])]
        m = _metrics(rows)
        frows = [f for f in faults["rows"] if f["role"] == role]
        m["scripted_faults_ok"] = all(f["ok"] for f in frows)
        m["hard_gates"] = bool(m["structured_validity"] == 1 and m["candidate_discipline"] and not m["silent_fallback"]
                               and m["failures_waited"] and m["scripted_faults_ok"] and not m["write_tools_offered"])
        result["roles"][role] = m
    sel, veto = result["roles"]["SELECTOR"], result["roles"]["VETO"]
    eligible = [r for r in ("SELECTOR", "VETO") if result["roles"][r]["hard_gates"]]
    if not eligible:
        choice, why = "VETO", "no role passed the hard gates; VETO keeps the smaller authority"
    elif len(eligible) == 1:
        choice, why = eligible[0], "only role passing the hard gates"
    elif sel["score"] > veto["score"] + 0.05:
        choice, why = "SELECTOR", "higher balanced score by more than 0.05"
    else:
        choice, why = "VETO", "VETO score within 0.05 of or above SELECTOR; smaller authority wins the tie"
    picked = result["roles"][choice]
    causal = picked["invalid_entry_veto"] is not None and picked["invalid_entry_veto"] >= 0.5
    prompt_text, prompt_sha = load_prompt(PROMPTS[choice])
    del prompt_text
    result |= {"choice": choice, "why": why, "ai_causal_enough": causal, "status": "PASS" if picked["hard_gates"] and causal else "PARTIAL"}
    (OUT / "EVAL_SUMMARY.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str) + "\n")
    frozen = {"frozen_at": datetime.now(UTC).isoformat(), "role": choice, "prompt_version": PROMPTS[choice], "prompt_sha256": prompt_sha,
              "brief_version": doc["briefs"]["BTCTMN"]["brief_version"], "brief_sha256": _brief_sha(doc["briefs"]), "model_id": MODEL,
              "note": "frozen before the 417-case holdout; not tuned afterwards"}
    FROZEN.write_text(json.dumps(frozen, indent=1) + "\n")
    (OUT / "AI_EVAL_EVIDENCE.json").write_text(json.dumps(  # readiness gate input (runtime/readiness.py AiEvalEvidence)
        {"role": choice, "prompt_version": PROMPTS[choice], "prompt_sha256": prompt_sha, "model_id": MODEL,
         "passed": result["status"] == "PASS", "source": "reports/phase-05/ai/f2-alignment/EVAL_SUMMARY.json"}, indent=1) + "\n")
    print(json.dumps({r: {k: v for k, v in m.items() if k != "by_class"} for r, m in result["roles"].items()}, indent=1, default=str))
    print("CHOICE", choice, why, "causal", causal)
    return 0


def cmd_qualify(a: argparse.Namespace) -> int:
    """Re-qualify the FROZEN role/prompt for another NineRouter model on the same fresh set (prompt unchanged)."""
    doc = json.loads(CASES.read_text())
    frozen = json.loads(FROZEN.read_text())
    role: Literal["SELECTOR", "VETO"] = frozen["role"]
    base = json.loads((OUT / "EVAL_SUMMARY.json").read_text())["roles"][role]
    faults = json.loads((OUT / "FAULTS.json").read_text())
    out_dir = _out(a.model)
    recorded = [r for p in sorted(out_dir.glob(f"run-{role}-*.json")) for r in json.loads(p.read_text())["rows"]]
    if len(recorded) != len(doc["cases"]):
        raise SystemExit(f"{len(recorded)} rows for {len(doc['cases'])} cases; run every shard first")
    m = _metrics(_revalidate(recorded, doc, role))
    m["scripted_faults_ok"] = all(f["ok"] for f in faults["rows"] if f["role"] == role)
    m["silent_fallback"] = not m["responders"] or any(a.model.split("/")[-1] not in x for x in m["responders"])
    m["hard_gates"] = bool(m["structured_validity"] == 1 and m["candidate_discipline"] and not m["silent_fallback"]
                           and m["failures_waited"] and m["scripted_faults_ok"] and not m["write_tools_offered"])
    passed = m["hard_gates"] and m["score"] is not None and m["score"] >= base["score"] - 0.05
    _, sha = load_prompt(frozen["prompt_version"])
    result = {"model_id": a.model, "role": role, "prompt_version": frozen["prompt_version"], "prompt_sha256": sha, "rule": QUALIFY_RULE,
              "frozen_model": frozen["model_id"], "frozen_model_score": base["score"], "passed": passed,
              "metrics": {k: v for k, v in m.items() if k != "by_class"}, "by_class": m["by_class"]}
    (out_dir / "QUALIFY.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str) + "\n")
    (out_dir / "AI_EVAL_EVIDENCE.json").write_text(json.dumps(
        {"role": role, "prompt_version": frozen["prompt_version"], "prompt_sha256": sha, "model_id": a.model, "passed": passed,
         "source": str((out_dir / "QUALIFY.json").relative_to(REPO))}, indent=1) + "\n")
    print(json.dumps({k: result[k] for k in ("model_id", "passed", "frozen_model_score")} | {
        k: m[k] for k in ("score", "valid_entry_preservation", "invalid_entry_veto", "hard_gates", "status_counts", "latency_ms_p50",
                          "latency_ms_max", "responders")}, indent=1, default=str))
    return 0 if passed else 5


def holdout_contexts() -> tuple[list[dict[str, Any]], dict[str, StrategyBrief]]:
    rows, briefs = source_contexts(HOLDOUT, "f2-src")
    return rows, briefs


def cmd_holdout(a: argparse.Namespace) -> int:
    frozen = json.loads(FROZEN.read_text())
    out = OUT / f"holdout-{a.shard}of{a.shards}.json"
    if out.exists():
        raise SystemExit(f"refusing: {out.name} exists; the holdout runs once")
    _, sha = load_prompt(frozen["prompt_version"])
    if sha != frozen["prompt_sha256"]:
        raise SystemExit("refusing: prompt changed after freezing")
    rows_src, briefs = holdout_contexts()
    if frozen["brief_sha256"] != _brief_sha({s: b.model_dump(mode="json") for s, b in briefs.items()}):
        raise SystemExit("refusing: strategy brief changed after freezing")
    store = JournalStore(Path(tempfile.mkdtemp()) / "holdout.sqlite")
    run_id = f"holdout-{a.shard}"
    provider, key = _provider(frozen["role"], store)
    rows: list[dict[str, Any]] = []
    for i, src in enumerate(rows_src):
        if i % a.shards != a.shard:
            continue
        ctx = _ctx_from(src["raw"], briefs[src["symbol"]], run_id=run_id)
        row = {"case": src["key"], "baseline_entered": src["baseline_entered"], **_row(ctx, provider, store, run_id)}
        rows.append(row)
        print(row["case"], row["status"], row["action"], row["latency_ms"], row["reason_codes"], flush=True)
    out.write_text(_redacted(json.dumps({"frozen": frozen, "rows": rows}, ensure_ascii=False, indent=0), key) + "\n")
    return 0


def _replay_ledger(cfg: Any, ds: Any, outputs: dict[str, dict[str, Any]], role: Literal["SELECTOR", "VETO"], tag: str,
                   db: Path | None = None) -> dict[str, Any]:
    if db is not None and db.exists():
        raise SystemExit(f"refusing: {db} exists (fresh evidence journal expected)")
    if db is not None:
        db.parent.mkdir(parents=True, exist_ok=True)
    store = JournalStore(db or Path(tempfile.mkdtemp()) / f"{tag}.sqlite")
    for k, rec in outputs.items():
        store.put_ref(tag, f"llm:{k}", rec)
    rt = run_backtest(cfg, ds, store, ReplayDecisionProvider(store, tag, exit_enabled=False, role=role), run_id=f"{tag}-replay",
                      period=HOLDOUT).runtime
    trades = rt.ledger.trades
    return {"trades": len(trades), "net_pnl_tmn": str(sum((t.net_pnl_tmn for t in trades), Decimal(0)).quantize(Decimal(1))),
            "wins": sum(t.net_pnl_tmn > 0 for t in trades),
            "max_drawdown_pct": str((rt.ledger.max_drawdown_fraction * 100).quantize(Decimal("0.001")))}


def cmd_holdout_summary(_: argparse.Namespace) -> int:
    frozen = json.loads(FROZEN.read_text())
    rows = [r for p in sorted(OUT.glob("holdout-*of*.json")) for r in json.loads(p.read_text())["rows"]]
    old = {r["case"]: r for r in json.loads((REPO / "reports/phase-05/ai/GPT61_F2_CASES.json").read_text())["rows"]}
    src = holdout_contexts()[0]
    if len(rows) != len(src) or {r["case"] for r in rows} != {s["key"] for s in src}:
        raise SystemExit(f"{len(rows)} holdout rows for {len(src)} cases; run every shard first")
    by_key = {s["key"]: s for s in src}
    changed = [r for r in rows if r["action"] != old[r["case"]]["action"]]
    entries = [r for r in rows if r["baseline_entered"]]
    cfg = load_run_config(CFG)
    ds = load_dataset(PACK_1H, expected_resolution_seconds=3600)
    base_rt = run_backtest(cfg, ds, JournalStore(Path(tempfile.mkdtemp()) / "b.sqlite"), BaselineSelector(), run_id="b",
                           period=HOLDOUT).runtime
    baseline = {"trades": len(base_rt.ledger.trades),
                "net_pnl_tmn": str(sum((t.net_pnl_tmn for t in base_rt.ledger.trades), Decimal(0)).quantize(Decimal(1))),
                "wins": sum(t.net_pnl_tmn > 0 for t in base_rt.ledger.trades),
                "max_drawdown_pct": str((base_rt.ledger.max_drawdown_fraction * 100).quantize(Decimal("0.001")))}
    new_out = {r["case"]: {"output": r["raw_output"], "status": r["status"] if r["raw_output"] is None else "VALID",
                           "detail": r["detail"], "latency_ms": r["latency_ms"], "responding_model": r["responder"]} for r in rows}
    old_out: dict[str, dict[str, Any]] = {}
    for k, r in old.items():
        snap_id = by_key[k]["raw"]["snapshot"]["snapshot_id"]
        out = None
        if r["status"] == "VALID":
            out = AgentDecision.model_validate({"snapshot_id": snap_id, "action": r["action"], "selected_candidate_id": r["candidate"],
                                                "regime_assessment": "RANGE", "confidence": "MEDIUM",
                                                "reason_codes": ["TREND_CONFIRMED" if r["action"] == "ENTER" else "WEAK_EVIDENCE"],
                                                "brief_rationale": "reconstructed from GPT61_F2_CASES.json"}).model_dump(mode="json")
        old_out[k] = {"output": out, "status": r["status"], "detail": None, "latency_ms": r["latency_ms"], "responding_model": r["responder"]}
    result = {"frozen": frozen, "cases": len(rows), "status_counts": dict(Counter(r["status"] for r in rows)),
              "actions": dict(Counter(r["action"] for r in rows)), "wait_rate": sum(r["action"] == "WAIT" for r in rows) / len(rows),
              "model_wait_rate": sum(r["model_action"] == "WAIT" for r in rows) / len(rows),
              "veto_unsupported": sum(r["status"] == "VETO_UNSUPPORTED" for r in rows),
              "invalid_candidate_references": sum(not r["discipline"] for r in rows),
              "changed_vs_decision_v2": len(changed),
              "changed_detail": dict(Counter(f"{old[r['case']]['action']}->{r['action']}" for r in changed)),
              "baseline_entry_points": len(entries),
              "baseline_entry_points_entered_new": sum(r["action"] == "ENTER" for r in entries),
              "baseline_entry_points_entered_v2": sum(old[r["case"]]["action"] == "ENTER" for r in entries),
              "veto_reasons": dict(Counter(c for r in rows if r["model_action"] == "WAIT" for c in r["reason_codes"])),
              "latency_ms_p50": int(median(sorted(r["latency_ms"] for r in rows))), "latency_ms_max": max(r["latency_ms"] for r in rows),
              "responders": sorted({r["responder"] for r in rows if r["responder"]}),
              "tool_calls": sum(len(r["tools"]) for r in rows),
              "counterfactual_replay": {"period": [h.isoformat() for h in HOLDOUT], "deterministic_f2_baseline": baseline,
                                        "decision_v2_selector": _replay_ledger(cfg, ds, old_out, "SELECTOR", "v2"),
                                        f"frozen_{frozen['role'].lower()}": _replay_ledger(
                                            cfg, ds, new_out, frozen["role"], "frozen", REPO / "var/precheck/holdout-replay.sqlite")},
              "evidence_journal": "var/precheck/holdout-replay.sqlite run frozen-replay (git-ignored; export with wallex-agent evidence)",
              "note": "retrospective holdout, run once after freezing; nothing tuned from it. Replay re-validates recorded outputs "
                      "in the real kernel (risk, fills, exits); decision_v2 outputs are reconstructed from action/candidate only."}
    (OUT / "HOLDOUT_SUMMARY.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("faults")
    r = sub.add_parser("run")
    r.add_argument("--role", choices=("SELECTOR", "VETO"), required=True)
    r.add_argument("--model", choices=QUALIFY_MODELS, default=MODEL)
    q = sub.add_parser("qualify")
    q.add_argument("--model", choices=QUALIFY_MODELS, required=True)
    for p in (r, sub.add_parser("holdout")):
        p.add_argument("--shard", type=int, default=0)
        p.add_argument("--shards", type=int, default=1)
    sub.add_parser("summarize")
    sub.add_parser("holdout-summary")
    a = ap.parse_args()
    return {"build": cmd_build, "qualify": cmd_qualify, "faults": cmd_faults, "run": cmd_run, "summarize": cmd_summarize, "holdout": cmd_holdout,
            "holdout-summary": cmd_holdout_summary}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
