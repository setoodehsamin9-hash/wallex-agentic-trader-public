"""Deterministic evidence bundles and offline reconstruction, with no credentials or runtime access."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from wallex_agent.contracts.hashing import canonical_json, sha256_hex

from .inspector import execution_events, inspect_run
from .presentation import render_demo_fa, render_performance_fa
from .source import EvidenceError, RunEvidence, obj, parse, timestamp

SENSITIVE_KEY = re.compile(r"^(?:.*api[_-]?key.*|authorization|.*headers?|.*credentials?.*|.*password.*|.*secret.*|.*private[_-]?key.*|"
    r"(?:access|refresh|auth|bearer)[_-]?token|token|cookies?|(?:account|session|user|customer)[_-]?(?:id|number)|iban|card[_-]?(?:number|no)|"
    r"chain[_-]of[_-]thought|reasoning|thinking|raw[_-]?(?:output|response|prompt)|model_endpoint)$", re.I)
SECRET_TEXT = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[opsur]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\b(?:x-api-key|api[_-]?key|authorization|password|secret|access[_-]?token|session[_-]?id|account[_-]?id)\s*[\"']?\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
    re.compile(r"\bIR\d{24}\b"),
    re.compile(r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b"),
    re.compile(r"https?://[^\s/]+:[^\s/]+@", re.I),
)


def _secret_check(text: str) -> None:
    # Optional env literals are used only for rejection, never exported or required. No key-file loading.
    literals = [os.environ.get(k, "") for k in ("WALLEX_API_KEY", "NINEROUTER_API_KEY")]
    if any(rx.search(text) for rx in SECRET_TEXT) or any(v and len(v) >= 8 and v in text for v in literals):
        raise EvidenceError("secret-bearing evidence refused; no source value is shown")


def _filter(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _filter(v) for k, v in cast(dict[str, Any], value).items() if not SENSITIVE_KEY.fullmatch(str(k))}
    if isinstance(value, (list, tuple)):
        return [_filter(v) for v in cast(list[Any], value)]
    if isinstance(value, str):
        _secret_check(value)
    return value


def safe_source(source: RunEvidence) -> RunEvidence:
    data = source.model_dump(mode="json")
    for decision in data["decisions"]:
        if decision["provenance"] is not None:
            decision["provenance"]["raw_output_ref"] = None
            for call in decision["provenance"]["tool_calls"]:
                call["result_ref"] = None
    # Hash-chained bytes cannot be redacted without forging a different chain. Refuse such a source.
    for state in source.states:
        original = parse(state["body"])
        if canonical_json(_filter(original)) != canonical_json(original):
            raise EvidenceError("secret-bearing hash-chained evidence cannot be exported")
    try:
        return RunEvidence.model_validate(_filter(data))
    except (ValidationError, TypeError, ValueError):
        raise EvidenceError("secret filtering removed required evidence; export refused") from None


def json_bytes(value: Any) -> bytes:
    return canonical_json(value) + b"\n"


def _jsonl(rows: Any) -> bytes:
    return b"".join(json_bytes(row) for row in rows)


def _content(source: RunEvidence) -> dict[str, bytes]:
    summary = inspect_run(source)
    manifest = source.manifest.model_dump(mode="json", exclude={"model_endpoint"})
    manifest["ended_at"] = source.ended_at.isoformat() if source.ended_at else None
    risk: list[dict[str, Any]] = []
    verdicts: set[str] = set()
    for d in source.decisions:
        if d.risk_verdict is not None and d.risk_verdict.verdict_id not in verdicts:
            verdicts.add(d.risk_verdict.verdict_id)
            risk.append({"kind": "DECISION_RISK_VERDICT", "decision_id": d.decision_id, "verdict": d.risk_verdict.model_dump(mode="json")})
    for state in source.states:
        if state["kind"] == "ORDER":
            verdict = parse(state["body"])["data"].get("verdict")
            if verdict and "verdict_id" in verdict and verdict["verdict_id"] not in verdicts:
                verdicts.add(verdict["verdict_id"])
                risk.append({"kind": "ORDER_RISK_VERDICT", "verdict": verdict})
    risk += list(source.risk_events)
    model = [{"decision_id": d.decision_id, "at": d.recorded_at.isoformat(), "provenance":
              d.provenance.model_dump(mode="json", exclude={"raw_output_ref"}),
              "validation_status": d.validated.status.value if d.validated else None}
             for d in source.decisions if d.provenance is not None]
    news = [{"kind": "NEWS_REFERENCE", "reference_key": k, "data": v} for k, v in sorted(source.references.items()) if k.startswith("news:")]
    news += [r for r in source.risk_events if r["kind"].startswith("NEWS_")]
    recovery = [r for r in source.risk_events if r["kind"].startswith(("RECOVERY_", "RESTART_"))]
    recovery += [{"kind": "RECOVERY_REFERENCE", "reference_key": k, "data": v} for k, v in sorted(source.references.items()) if k.startswith("recovery:")]
    datasets = {canonical_json(d).decode(): d for d in manifest["dataset_refs"]}
    for decision in source.decisions:
        for ref in decision.data_refs:
            data_ref = ref.model_dump(mode="json")
            datasets[canonical_json(data_ref).decode()] = data_ref
    summary_bytes = json_bytes(summary)
    source_bytes = json_bytes(source)
    repro = {"format_version": 1, "source_run_id": source.run_id, "environment": manifest["environment"],
        "autonomy": manifest["autonomy"], "git_commit": manifest["code"]["git_commit"], "code_dirty": manifest["code"]["dirty"],
        "config_sha256": manifest["config_sha256"], "dataset_refs": [datasets[k] for k in sorted(datasets)],
        "dependency_lock_sha256": manifest["dependency_lock_sha256"], "strategy_versions": manifest["strategy_versions"],
        "strategy_ids": sorted({c.strategy_id for d in source.decisions for c in d.candidates}),
        "model_id": manifest["model_id"], "model_provider": manifest["model_provider"],
        "model_settings_sha256": manifest["model_settings_sha256"], "prompt_bundle_sha256": manifest["prompt_bundle_sha256"],
        "seed": manifest["seed"], "clock": manifest["clock"], "source_hashes": source.source_hashes,
        "source_snapshot_sha256": sha256_hex(source_bytes), "observable_summary_sha256": sha256_hex(summary_bytes),
        "state_chain_head": source.states[-1]["hash"] if source.states else None,
        "state_records": len(source.states), "checkpoints_verified": summary["checkpoints_verified"],
        "reconstruction": "wallex-agent evidence verify DIR (pure recorded-evidence reconstruction; no new trading/model calls)",
        "ordering": "state_log seq; execution append row order; decisions recorded_at/append row order; equity at/append row order; risk journal id",
        "limitations": ["Hashes prove internal integrity, not authenticity against an external exchange or a signer.",
                        "Raw configs/model responses/tool payloads are excluded. Ref hashes identify original persisted bytes.",
                        "An intact prefix cannot prove no unjournaled tail exists; counts cover the captured journal prefix.",
                        "No comparison or aggregation across run ids or environments is performed."]}
    return {"SUMMARY.json": summary_bytes, "PERFORMANCE_FA.md": render_performance_fa(source, summary).encode(),
        "TRADES.jsonl": _jsonl(source.trades), "DECISIONS.jsonl": _jsonl(source.decisions), "RISK_EVENTS.jsonl": _jsonl(risk),
        "MODEL_EVENTS.jsonl": _jsonl(model), "NEWS_EVENTS.jsonl": _jsonl(news), "RECOVERY_EVENTS.jsonl": _jsonl(recovery),
        "EXECUTION_EVENTS.jsonl": _jsonl([e.model_dump(mode="json") for e in execution_events(source)]),
        "EQUITY.jsonl": _jsonl(source.equity), "RUN_MANIFEST.json": json_bytes(manifest), "REPRODUCIBILITY.json": json_bytes(repro),
        "SOURCE.json": source_bytes, "DEMO_FA.md": render_demo_fa(source).encode()}


def _stable_hash(hashes: dict[str, str]) -> str:
    return sha256_hex(canonical_json({k: v for k, v in sorted(hashes.items()) if k != "GENERATION.json"}))


def export_evidence(source: RunEvidence, out: Path, *, generated_at: datetime | None = None) -> Path:
    if out.exists() or out.is_symlink():
        raise EvidenceError("export destination already exists; choose a new directory")
    timestamp_value = timestamp((generated_at or datetime.now(UTC)).isoformat())
    safe = safe_source(source)
    content = _content(safe)
    content["GENERATION.json"] = json_bytes({"generated_at": timestamp_value.isoformat(), "source_run_id": source.run_id,
        "note": "Variable generation metadata is excluded from reproducible_content_sha256; all files still have individual hashes."})
    for body in content.values():
        _secret_check(body.decode())
    hashes = {name: sha256_hex(body) for name, body in sorted(content.items())}
    content["FILE_HASHES.json"] = json_bytes({"algorithm": "sha256", "files": hashes,
        "reproducible_content_sha256": _stable_hash(hashes), "self_hash": "excluded to avoid recursive self-hashing"})
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".evidence-", dir=out.parent))
    try:
        for name, body in content.items():
            (stage / name).write_bytes(body)
        verify_bundle(stage)
        if out.exists() or out.is_symlink():
            raise EvidenceError("export destination appeared during generation")
        stage.rename(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return out


def verify_bundle(directory: Path) -> dict[str, Any]:
    """Check every file hash and regenerate every stable artifact from SOURCE.json, offline."""
    try:
        hashes = parse((directory / "FILE_HASHES.json").read_bytes())
        raw_files = hashes["files"]
        if not isinstance(raw_files, dict) or hashes.get("algorithm") != "sha256":
            raise EvidenceError("unsupported evidence hash manifest")
        files: dict[str, str] = {}
        for name, digest in obj(raw_files).items():
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise EvidenceError("invalid evidence artifact hash")
            files[name] = digest
            if Path(name).name != name or name in (".", "..") or (directory / name).is_symlink():
                raise EvidenceError("unsafe artifact path in hash manifest")
            body = (directory / name).read_bytes()
            _secret_check(body.decode())
            if sha256_hex(body) != digest:
                raise EvidenceError("evidence artifact hash mismatch")
        if {p.name for p in directory.iterdir()} != set(files) | {"FILE_HASHES.json"}:
            raise EvidenceError("evidence bundle contains missing/extra artifacts")
        if hashes["reproducible_content_sha256"] != _stable_hash(files):
            raise EvidenceError("reproducible content hash mismatch")
        source = RunEvidence.model_validate_json((directory / "SOURCE.json").read_bytes())
        stable = _content(source)
        if set(files) != set(stable) | {"GENERATION.json"}:
            raise EvidenceError("incomplete evidence artifact inventory")
        for name, expected in stable.items():
            if (directory / name).read_bytes() != expected:
                raise EvidenceError("exported artifact does not reconstruct from the source snapshot")
        return {"reconstruction_matches": True, "reproducible_content_sha256": hashes["reproducible_content_sha256"],
                "summary": json.loads(stable["SUMMARY.json"])}
    except EvidenceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeDecodeError):
        raise EvidenceError("malformed or incomplete evidence bundle") from None
