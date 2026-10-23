"""Execution Engine: the only component that talks to a venue's write methods.

- Refuses any intent without a matching, unexpired, sufficient RiskVerdict for the same environment.
- Refuses a second submission of the same client_order_id or intent_hash (no duplicate orders, ever).
- Persists INTENT/SUBMITTING before the venue call. A lost response -> UNKNOWN -> resolved only by looking the
  order up by client_order_id; never resubmitted blindly (Wallex llm.txt: never silently retry a state change).
- Applies each venue fill to the ledger exactly once; protects partial fills (positions exist from the first fill);
  releases entry reservations and clears pending-exit markers when an order becomes terminal.
- A requested cancel is persisted (CANCEL_PENDING) before the venue call; a lost cancel response is UNKNOWN. A
  cancelled SELL keeps its position's pending-exit marker until a SECOND, later lookup confirms the terminal state
  (fill-after-cancel race), so a replacement exit can never overlap a still-live one.
- N consecutive execution errors latch the kill switch.
- Every order opening, fill and state change is handed to the `recorder` (the persisted state log) so restart
  recovery can rebuild orders and the ledger; the ORDER record and SUBMITTING state are persisted before the venue
  call. After a restart, recovered orders are `adopt`ed so duplicate refusal still covers them.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from wallex_agent.contracts import (
    TERMINAL_ORDER_STATES,
    ExecutionEvent,
    FillSource,
    MarketMeta,
    OrderIntent,
    OrderPurpose,
    OrderState,
    RiskOutcome,
    RiskVerdict,
)
from wallex_agent.contracts.hashing import content_id
from wallex_agent.portfolio.ledger import ExitPlan, Fill, Ledger, TradeRecord, reservation_id
from wallex_agent.risk.kill_switch import KillSwitch
from wallex_agent.risk.protections import Protections

from .venue import SubmitTimeout, Venue, VenueError, VenueFill, VenueOrder


class VerdictMismatch(RuntimeError):
    """Intent and verdict do not bind (hash, expiry, quantity, outcome or environment)."""


class DuplicateSubmit(RuntimeError):
    pass


@dataclass
class OrderRecord:
    intent: OrderIntent
    verdict: RiskVerdict
    plan: ExitPlan | None
    strategy_version: str
    state: OrderState = OrderState.INTENT
    applied: set[str] = field(default_factory=lambda: set())
    filled_qty: Decimal = Decimal(0)
    events: list[ExecutionEvent] = field(default_factory=lambda: [])
    trades: list[TradeRecord] = field(default_factory=lambda: [])
    fills: list[VenueFill] = field(default_factory=lambda: [])
    cancel_requested_at: datetime | None = None
    last_cancel_at: datetime | None = None
    terminal_seen: int = 0


class ExecutionRecorder(Protocol):
    """Durable sink for order lifecycle facts (implemented by `wallex_agent.recovery.state_log.StateLog`)."""

    def order_opened(self, rec: OrderRecord, at: datetime) -> None: ...

    def fill_applied(self, rec: OrderRecord, fill: VenueFill, dust_qty: Decimal, ledger: Ledger) -> None: ...

    def state_changed(self, rec: OrderRecord, ev: ExecutionEvent) -> None: ...


SIMULATED_SOURCES = frozenset({FillSource.BACKTEST_SIM, FillSource.SHADOW_SIM})
CANCEL_ENDS = frozenset({OrderState.CANCELED, OrderState.EXPIRED})
CANCEL_CONFIRMED = "CANCEL_CONFIRMED"


def awaiting_cancel_confirmation(rec: "OrderRecord") -> bool:
    """A cancelled exit SELL seen terminal once but not yet confirmed by the second lookup: its position stays held."""
    return rec.intent.side == "SELL" and rec.cancel_requested_at is not None and rec.state in CANCEL_ENDS and rec.terminal_seen < 2


class ExecutionEngine:
    def __init__(self, venue: Venue, ledger: Ledger, protections: Protections, kill: KillSwitch, *,
                 metas: dict[str, MarketMeta], max_consecutive_errors: int | None,
                 on_event: Callable[[ExecutionEvent, OrderRecord], None] | None = None,
                 recorder: ExecutionRecorder | None = None) -> None:
        self.venue = venue
        self.ledger = ledger
        self.prot = protections
        self.kill = kill
        self.metas = metas
        self.max_errors = max_consecutive_errors
        self.on_event = on_event
        self.recorder = recorder
        self._orders: dict[str, OrderRecord] = {}
        self._hashes: set[str] = set()
        self.consecutive_errors = 0
        self.error_count = 0

    # ------------------------------------------------------------------------------------------------ lookups
    def order(self, client_order_id: str) -> OrderRecord:
        return self._orders[client_order_id]

    def orders(self) -> list[OrderRecord]:
        return list(self._orders.values())

    def unresolved(self) -> list[str]:
        return [cid for cid, r in self._orders.items() if r.state is OrderState.UNKNOWN]

    def open_orders(self) -> list[OrderRecord]:
        return [r for r in self._orders.values() if r.state not in TERMINAL_ORDER_STATES]

    # ------------------------------------------------------------------------------------------------ internals
    def _event(self, rec: OrderRecord, state: OrderState, raw: str | None, at: datetime) -> ExecutionEvent:
        avg = sum((f.price * f.qty for f in rec.fills), Decimal(0)) / rec.filled_qty if rec.filled_qty else None
        assets = {f.fee_asset for f in rec.fills}
        fees = sum((f.fee for f in rec.fills), Decimal(0)) if len(assets) == 1 else None
        ev = ExecutionEvent(
            event_id="ev-" + content_id({"c": rec.intent.client_order_id, "s": state.value, "q": rec.filled_qty, "t": at,
                                         "n": len(rec.events)})[:16],
            intent_id=rec.intent.intent_id, client_order_id=rec.intent.client_order_id, state=state,
            exchange_status_raw=raw, cum_filled_qty=rec.filled_qty, avg_fill_price=avg, fee=fees,
            fee_asset=next(iter(assets)) if len(assets) == 1 else None,
            source=self.venue.fill_source, at=at)
        rec.state = state
        rec.events.append(ev)
        if self.recorder:
            self.recorder.state_changed(rec, ev)
        if self.on_event:
            self.on_event(ev, rec)
        return ev

    def _sync(self, rec: OrderRecord, vo: VenueOrder, now: datetime) -> list[ExecutionEvent]:
        out: list[ExecutionEvent] = []
        intent = rec.intent
        meta = self.metas.get(intent.symbol)
        new = [f for f in vo.fills if f.fill_id not in rec.applied]
        for index, f in enumerate(new):
            # A small remainder may still be filled at the next book level, or after a cancel.
            complete = vo.filled_qty == intent.qty and vo.state in TERMINAL_ORDER_STATES and index == len(new) - 1
            dust = (meta.min_notional / f.price) if meta is not None and complete and intent.side == "SELL" else Decimal(0)
            trade = self.ledger.apply_fill(
                Fill(intent.symbol, intent.side, f.qty, f.price, f.fee, f.fee_asset, f.at, intent.client_order_id,
                     intent.purpose.value, intent.decision_id, rec.strategy_version),
                plan=rec.plan if intent.side == "BUY" else None, dust_qty=dust)
            rec.applied.add(f.fill_id)
            rec.filled_qty += f.qty
            rec.fills.append(f)
            if self.recorder:
                self.recorder.fill_applied(rec, f, dust, self.ledger)
            if trade is not None:
                rec.trades.append(trade)
                self.prot.record_exit(trade.net_pnl_tmn, trade.closed_at)
        state = vo.state
        if state is OrderState.CANCELED and rec.filled_qty > 0 and rec.filled_qty < intent.qty and (new or state is not rec.state):
            out.append(self._event(rec, OrderState.PARTIAL, vo.status_raw, now))
        if new or state is not rec.state:
            out.append(self._event(rec, state, vo.status_raw, now))
        if state in TERMINAL_ORDER_STATES:
            if rec.cancel_requested_at is not None and intent.side == "SELL" and state in CANCEL_ENDS:
                rec.terminal_seen += 1
                if rec.terminal_seen < 2:
                    return out  # confirm a cancelled exit with a second, later lookup before releasing the position
                if rec.terminal_seen == 2:  # journaled, so a replay reproduces exactly when the position was released
                    out.append(self._event(rec, state, CANCEL_CONFIRMED, now))
            self._finish(rec)  # FILLED / REJECTED are final at once
        return out

    def _finish(self, rec: OrderRecord) -> None:
        intent = rec.intent
        if intent.purpose is OrderPurpose.ENTRY:
            self.ledger.release(reservation_id(intent.symbol, intent.intent_id))
        else:
            pos = self.ledger.positions.get(intent.symbol)
            if pos is not None and pos.exit_order_id == intent.client_order_id:
                pos.exit_order_id = None

    def _error(self, now: datetime, reason: str) -> None:
        self.consecutive_errors += 1
        self.error_count += 1
        if self.max_errors is not None and self.consecutive_errors >= self.max_errors:
            self.kill.latch(f"{self.consecutive_errors} consecutive execution errors: {reason}", at=now)

    # ------------------------------------------------------------------------------------------------ public API
    def submit(self, intent: OrderIntent, verdict: RiskVerdict, *, now: datetime, plan: ExitPlan | None,
               strategy_version: str = "") -> list[ExecutionEvent]:
        if content_id(intent.model_dump(exclude={"intent_hash", "schema_version"})) != intent.intent_hash:
            raise VerdictMismatch("intent content no longer matches its hash")
        if verdict.intent_hash != intent.intent_hash:
            raise VerdictMismatch("verdict is for a different intent")
        if verdict.outcome is RiskOutcome.REJECT or verdict.approved_qty is None or verdict.approved_qty < intent.qty:
            raise VerdictMismatch("verdict does not approve this quantity")
        if now >= verdict.expires_at or now < verdict.evaluated_at:
            raise VerdictMismatch("verdict expired; re-evaluate risk")
        if intent.environment is not self.venue.environment:
            raise VerdictMismatch(f"intent for {intent.environment} cannot go to a {self.venue.environment} venue")
        if intent.client_order_id in self._orders or intent.intent_hash in self._hashes:
            raise DuplicateSubmit(intent.client_order_id)

        rec = OrderRecord(intent, verdict, plan, strategy_version)
        self._orders[intent.client_order_id] = rec
        self._hashes.add(intent.intent_hash)
        if self.recorder:
            self.recorder.order_opened(rec, now)
        events = [self._event(rec, OrderState.INTENT, None, now), self._event(rec, OrderState.SUBMITTING, None, now)]
        if intent.side == "SELL":
            pos = self.ledger.positions.get(intent.symbol)
            if pos is not None:
                pos.exit_order_id = intent.client_order_id
        try:
            vo = self.venue.submit(intent, now)
        except SubmitTimeout as exc:
            events.append(self._event(rec, OrderState.UNKNOWN, str(exc), now))
            found = self.venue.query(intent.client_order_id)
            if found is not None:
                events += self._sync(rec, found, now)
            return events
        except VenueError as exc:
            events.append(self._event(rec, OrderState.REJECTED, str(exc), now))
            self._finish(rec)
            self._error(now, str(exc))
            return events
        self.consecutive_errors = 0
        events.append(self._event(rec, OrderState.ACKED, vo.status_raw, now))
        events += self._sync(rec, vo, now)
        return events

    def poll(self, *, now: datetime) -> list[ExecutionEvent]:
        out: list[ExecutionEvent] = []
        for rec in self.orders():
            if rec.state in (OrderState.FILLED, OrderState.REJECTED):
                continue
            vo = self.venue.query(rec.intent.client_order_id)
            if vo is None:
                continue  # still UNKNOWN; never resubmit
            out += self._sync(rec, vo, now)
        return out

    def adopt(self, rec: OrderRecord) -> None:
        """Install an order rebuilt from the state log after a restart (no venue call, no new journal record)."""
        if rec.intent.client_order_id in self._orders or rec.intent.intent_hash in self._hashes:
            raise DuplicateSubmit(rec.intent.client_order_id)
        self._orders[rec.intent.client_order_id] = rec
        self._hashes.add(rec.intent.intent_hash)
        remember = getattr(self.venue, "remember", None)
        if callable(remember):
            remember(rec.intent)  # a real venue must be able to look the order up by client_order_id after a restart

    def mark_unknown(self, client_order_id: str, *, now: datetime, reason: str) -> ExecutionEvent:
        """Restart recovery: a non-terminal order's venue outcome after the last persisted fact is not known."""
        rec = self._orders[client_order_id]
        if rec.state in TERMINAL_ORDER_STATES:
            raise ValueError(f"{client_order_id} is terminal ({rec.state})")
        return self._event(rec, OrderState.UNKNOWN, reason, now)

    def settle_simulated(self, client_order_id: str, *, now: datetime) -> list[ExecutionEvent]:
        """Restart recovery for a SIMULATED venue only. A simulator keeps no state outside this process, so the
        persisted fills are the complete outcome: fully filled -> FILLED, otherwise the remainder is CANCELED (the
        simulators are immediate-or-cancel / time-limited). Never resubmits; refuses for a real exchange."""
        if self.venue.fill_source not in SIMULATED_SOURCES:
            raise ValueError("only a simulated venue's lost outcome can be settled from the journal")
        rec = self._orders[client_order_id]
        if awaiting_cancel_confirmation(rec):  # the simulator kept nothing: the persisted terminal state is final
            rec.terminal_seen = 2
            ev = self._event(rec, rec.state, "SIM_OUTCOME_LOST_AT_RESTART", now)
            self._finish(rec)
            return [ev]
        if rec.state in TERMINAL_ORDER_STATES:
            return []
        state = OrderState.FILLED if rec.filled_qty == rec.intent.qty else OrderState.CANCELED
        events: list[ExecutionEvent] = []
        if state is OrderState.CANCELED and rec.filled_qty > 0:
            events.append(self._event(rec, OrderState.PARTIAL, "SIM_OUTCOME_LOST_AT_RESTART", now))
        events.append(self._event(rec, state, "SIM_OUTCOME_LOST_AT_RESTART", now))
        self._finish(rec)
        return events

    def request_cancel(self, client_order_id: str, *, now: datetime, reason: str) -> list[ExecutionEvent]:
        """Cancel a resting order. CANCEL_PENDING is persisted before the venue call; a lost or rejected cancel is
        resolved by lookup only, and a lookup that fails leaves the order UNKNOWN (reconcile, never resubmit)."""
        rec = self._orders[client_order_id]
        if rec.state in TERMINAL_ORDER_STATES or rec.state is OrderState.UNKNOWN:
            return []
        first = rec.cancel_requested_at is None
        rec.cancel_requested_at = rec.cancel_requested_at or now
        rec.last_cancel_at = now
        events = [self._event(rec, OrderState.CANCEL_PENDING, reason if first else "CANCEL_RETRY", now)]
        try:
            vo = self.venue.cancel(client_order_id, now)
        except VenueError:
            vo = self.venue.query(client_order_id)  # e.g. already filled: the lookup is the truth
        if vo is None:
            events.append(self._event(rec, OrderState.UNKNOWN, "CANCEL_OUTCOME_UNKNOWN", now))
            return events
        return events + self._sync(rec, vo, now)

    def cancel(self, client_order_id: str, *, now: datetime) -> list[ExecutionEvent]:
        rec = self._orders[client_order_id]
        if rec.state in TERMINAL_ORDER_STATES:
            return []
        vo = self.venue.cancel(client_order_id, now)
        return self._sync(rec, vo, now) if vo is not None else []
