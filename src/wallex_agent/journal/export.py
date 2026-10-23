"""Run export: SUMMARY.json, TRADES.jsonl, DECISIONS.jsonl, RISK_EVENTS.jsonl, PERFORMANCE_FA.md (+ MANIFEST.json).

The directory is secret-scanned after writing; on any hit the export is deleted and SecretLeak is raised.
"""

import json
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from wallex_agent.agent.ninerouter import resolve_api_key
from wallex_agent.reporting.performance import PerformanceSummary, summary_json
from wallex_agent.reporting.performance_fa import render_performance_fa

from .store import JournalStore

SECRET_PATTERNS = [
    re.compile(r"x-api-key\"?\s*[:=]\s*\"?[A-Za-z0-9_\-]{12,}", re.I),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[opsu]_[A-Za-z0-9]{20,}"),
    re.compile(r"authorization\"?\s*[:=]\s*\"?bearer\s+[A-Za-z0-9._\-]{12,}", re.I),
    re.compile(r"BEGIN [A-Z ]*PRIVATE KEY"),
]


class SecretLeak(RuntimeError):
    pass


def known_secret_literals() -> tuple[str, ...]:
    """Values of configured credentials, to make sure none of them appears verbatim. Never printed."""
    vals: list[str] = []
    for var in ("NINEROUTER_API_KEY", "WALLEX_API_KEY"):
        v = os.environ.get(var, "").strip()
        if len(v) >= 8:
            vals.append(v)
    path = os.environ.get("NINEROUTER_API_KEY_FILE")
    if path and not os.environ.get("NINEROUTER_API_KEY", "").strip():
        v = resolve_api_key()
        if len(v) >= 8:
            vals.append(v)
    return tuple(vals)


def scan_for_secrets(directory: Path, *, extra_literals: Iterable[str]) -> None:
    literals = [x for x in extra_literals if x]
    for f in directory.rglob("*"):
        if not f.is_file():
            continue
        text = f.read_text(errors="ignore")
        for pat in SECRET_PATTERNS:
            if pat.search(text):
                raise SecretLeak(f"{f.name}: matches secret pattern {pat.pattern[:24]}…")
        if any(lit in text for lit in literals):
            raise SecretLeak(f"{f.name}: contains a configured credential value")


def _jsonl(path: Path, rows: Iterable[Any]) -> None:
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def export_run(store: JournalStore, run_id: str, out_dir: Path, *, summary: PerformanceSummary, context: dict[str, Any]) -> Path:
    literals = known_secret_literals()  # refuse invalid credential configuration before writing export files
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "SUMMARY.json").write_text(json.dumps(summary_json(summary), ensure_ascii=False, indent=1))
    _jsonl(out_dir / "TRADES.jsonl", store.trades(run_id))
    _jsonl(out_dir / "DECISIONS.jsonl", (json.loads(d.model_dump_json()) for d in store.decisions(run_id)))
    _jsonl(out_dir / "RISK_EVENTS.jsonl", store.risk_events(run_id))
    manifest = store.manifest(run_id)
    if manifest is not None:
        (out_dir / "MANIFEST.json").write_text(manifest.model_dump_json(indent=1))
    (out_dir / "PERFORMANCE_FA.md").write_text(render_performance_fa(summary, context))
    try:
        scan_for_secrets(out_dir, extra_literals=literals)
    except SecretLeak:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    return out_dir
