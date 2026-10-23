"""Wait for a bounded decision while the caller continues deterministic maintenance.

Only the read-only decision provider runs on a worker. Ledger, risk, approvals and execution stay on the caller's
thread. One in-flight decision maximum; a tardy result has no execution capability and is never applied later.
"""

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime

from wallex_agent.agent.context import DecisionContext, DecisionProvider
from wallex_agent.contracts import AgentAction, DecisionProvenance, DecisionSource, ValidatedDecision, ValidationStatus


class ResponsiveDecisionProvider:
    def __init__(self, provider: DecisionProvider, *, maintain: Callable[[], None], clock: Callable[[], datetime],
                 interval_s: float) -> None:
        if interval_s <= 0:
            raise ValueError("maintenance interval must be positive")
        self.provider, self.maintain, self.clock, self.interval_s = provider, maintain, clock, interval_s
        self.source_name = provider.source_name
        self.source = DecisionSource(provider.source_name)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bounded-decision")
        self._pending: Future[tuple[ValidatedDecision, DecisionProvenance]] | None = None

    def _failure(self, ctx: DecisionContext, status: ValidationStatus, detail: str) -> tuple[ValidatedDecision, DecisionProvenance]:
        now = self.clock()
        return (ValidatedDecision(status=status, decision=None, effective_action=AgentAction.WAIT, detail=detail),
                DecisionProvenance(source=self.source, model_id=None, model_settings_sha256=None, prompt_version=None,
                                   prompt_sha256=None, model_requests=0, latency_ms=max(0, int((now - ctx.now).total_seconds() * 1000)),
                                   raw_output_ref=None, started_at=ctx.now, finished_at=now))

    def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
        if self._pending is not None and not self._pending.done():
            return self._failure(ctx, ValidationStatus.BUDGET_EXCEEDED, "prior bounded decision is still finishing")
        self._pending = self._executor.submit(self.provider.decide, ctx)
        stop = time.monotonic() + max(0, (ctx.deadline - ctx.now).total_seconds())
        while True:
            remaining = min(stop - time.monotonic(), (ctx.deadline - self.clock()).total_seconds())
            if remaining <= 0:
                self._pending.cancel()
                return self._failure(ctx, ValidationStatus.TIMEOUT, "decision deadline reached; late output discarded")
            done, _ = wait((self._pending,), timeout=min(self.interval_s, remaining))
            if not done:
                self.maintain()
                continue
            try:
                return self._pending.result()
            except Exception as exc:
                # Provider exception text can contain secrets; record only its class.
                return self._failure(ctx, ValidationStatus.PROVIDER_ERROR, f"decision provider failed: {type(exc).__name__}")

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
