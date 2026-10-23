"""Replay provider: re-feeds the model outputs recorded by a previous run (Layer 2 tests, `wallex-agent replay`).

No model, no network. The recorded output is validated again against the re-built context, so a replay on the same
dataset/config reproduces the same intents, verdicts and simulated fills. A missing recording is a WAIT, not a guess.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from wallex_agent.contracts import AgentDecision, DecisionProvenance, DecisionSource, ValidatedDecision, ValidationStatus
from wallex_agent.journal.store import JournalStore

from .context import DecisionContext
from .validator import validate


class ReplayDecisionProvider:
    source_name = "REPLAY"

    def __init__(self, store: JournalStore, source_run_id: str, *, exit_enabled: bool,
                 role: Literal["SELECTOR", "VETO"] = "SELECTOR") -> None:
        self.role: Literal["SELECTOR", "VETO"] = role
        self.store = store
        self.source_run_id = source_run_id
        self.exit_enabled = exit_enabled
        self.missing = 0
        self._baseline = {d.cycle_id: d for d in store.decisions(source_run_id)
                          if d.provenance is not None and d.provenance.source is DecisionSource.BASELINE and d.validated is not None}

    def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
        rec: dict[str, Any] | None = self.store.get_ref(self.source_run_id, f"llm:{ctx.decision_key}")
        recorded = self._baseline.get(ctx.decision_key)
        if rec is None and recorded is not None and recorded.validated is not None:
            v = recorded.validated
            rec = {"output": v.decision.model_dump(mode="json") if v.decision else None,
                   "status": v.status.value, "detail": v.detail, "latency_ms": 0, "responding_model": "BASELINE"}
        now = datetime.now(UTC)
        if rec is None:
            self.missing += 1
            validated = validate(None, ctx, received_at=ctx.now, exit_enabled=self.exit_enabled,
                                 failure=ValidationStatus.PROVIDER_ERROR, detail="no recorded output for this decision", role=self.role)
            latency = 0
            model = None
        else:
            output = AgentDecision.model_validate(rec["output"]) if rec["output"] else None
            latency = int(rec["latency_ms"])
            model = rec.get("responding_model")
            failure = ValidationStatus(rec["status"]) if rec["status"] != ValidationStatus.VALID.value else ValidationStatus.INVALID_SCHEMA
            validated = validate(output, ctx, received_at=ctx.now + timedelta(milliseconds=latency), exit_enabled=self.exit_enabled,
                                 failure=failure, detail=rec.get("detail"), role=self.role)
        prov = DecisionProvenance(source=DecisionSource.REPLAY, model_id=f"replay:{model}" if model else "replay",
                                  model_settings_sha256=None, prompt_version=None, prompt_sha256=None, model_requests=0,
                                  latency_ms=latency, raw_output_ref=None, started_at=now, finished_at=now)
        return validated, prov
