"""Ports: the interfaces through which the shared core talks to the outside world.

BACKTEST, SHADOW and LIVE are not three systems. They are three sets of adapters for these
ports, chosen once by the composition root (wallex_agent.runtime). Strategy, agent, policy,
risk, journal and orchestrator code is identical across environments.

    port               BACKTEST                 SHADOW                     LIVE
    Clock              virtual (bar replay)     wall clock                 wall clock
    MarketDataPort     recorded dataset         Wallex REST/WS + MCP read  Wallex REST/WS + MCP read
    AccountPort        virtual ledger           virtual ledger (+fees RO)  Wallex private reads
    ExecutionPort      bar fill simulator       book-based fill simulator  Wallex REST orders
    DecisionProvider   LLM | replay | baseline  LLM | baseline             LLM | baseline

Interfaces only in Phase 02; implementations land in Phase 03 (see reports/phase-02/HANDOFF_TO_PHASE_03.md).
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from wallex_agent.contracts import (
    ApprovalTicket,
    DecisionProvenance,
    DecisionRecord,
    Environment,
    ExecutionEvent,
    FeatureSet,
    MarketSnapshot,
    OrderIntent,
    RiskVerdict,
    RunManifest,
    SignalCandidate,
    ValidatedDecision,
)
from wallex_agent.news.contracts import NewsContext


class Clock(Protocol):
    def now(self) -> datetime:
        """Current UTC time. Virtual in BACKTEST; nothing in the core calls datetime.now() directly."""
        ...


class NewsContextPort(Protocol):
    def context(self, decision_time: datetime, *, assets: tuple[str, ...] = ()) -> NewsContext:
        """Cached, bounded, as-of news only. No network, model invocation or execution capability."""
        ...


class MarketDataPort(Protocol):
    async def snapshot(self, symbol: str, as_of: datetime) -> MarketSnapshot:
        """Validated snapshot from bars closed at or before `as_of`; quality problems are flags, not exceptions."""
        ...

    async def eligible_symbols(self) -> frozenset[str]:
        """spot ∩ metadata-available − package-unavailable − confirmed extra restrictions. Fail closed."""
        ...


class AccountPort(Protocol):
    async def balances(self) -> dict[str, tuple[Decimal, Decimal]]:
        """asset -> (available, locked)."""
        ...

    async def open_orders(self) -> Sequence[ExecutionEvent]: ...

    async def order_by_client_id(self, client_order_id: str) -> ExecutionEvent | None:
        """Used to resolve UNKNOWN submissions. None means the exchange has no such order."""
        ...


class ExecutionPort(Protocol):
    environment: Environment

    async def submit(self, intent: OrderIntent, verdict: RiskVerdict) -> ExecutionEvent:
        """Refuses unless verdict.intent_hash == intent.intent_hash, verdict is unexpired and approves a qty
        >= intent.qty, and intent.environment == self.environment. A lost response returns state UNKNOWN."""
        ...

    async def cancel(self, client_order_id: str) -> ExecutionEvent: ...


class DecisionProvider(Protocol):
    async def decide(
        self,
        snapshot: MarketSnapshot,
        features: FeatureSet,
        candidates: Sequence[SignalCandidate],
    ) -> tuple[ValidatedDecision, DecisionProvenance]:
        """LLM agent, recorded-response replay, or deterministic baseline. Never raises for model faults:
        every failure is a ValidatedDecision whose effective_action is WAIT."""
        ...


class ApprovalStore(Protocol):
    async def open_ticket(self, intent: OrderIntent, expires_at: datetime) -> ApprovalTicket: ...

    async def consume(self, ticket_id: str, intent_hash: str, now: datetime) -> ApprovalTicket:
        """Atomically APPROVED -> CONSUMED. Raises if expired, rejected, already used, or hash differs."""
        ...


class Journal(Protocol):
    async def start_run(self, manifest: RunManifest) -> None: ...

    async def record(self, record: DecisionRecord) -> None:
        """Append-only. Written before the cycle returns, including WAIT and failure cycles."""
        ...
