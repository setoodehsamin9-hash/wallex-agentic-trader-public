"""Exit coordinator: deterministic stop / take-profit / strategy-signal / time exits and forced flattening. Never waits for the model
or a human; in ADVISOR mode it only proposes (policy). One pending exit per position (no double sells).

Pricing depends on the market view, wired by the composition root:
- BAR (BACKTEST): stop/TP orders carry the trigger level; the bar simulator applies stop-market / resting-limit
  semantics. Time/liquidation exits use close x (1 - exit slippage).
- BOOK (SHADOW/LIVE): every exit is a marketable limit at best bid x (1 - exit slippage).

Resting exits (exit_order_ttl_s set; required before LIVE): a pending SELL is supervised, never just skipped. It is
cancelled when older than the TTL, or at once when the stop is breached while a non-stop exit rests; the replacement
is issued only after the execution engine confirms the cancel by a second lookup (the position's pending marker is
held until then), so two sells never overlap. A pending exit whose outcome is UNKNOWN is reported RECONCILE_REQUIRED
every run and never resubmitted. TTL unset keeps the earlier behaviour (simulators expire their own orders).

Chandelier trail (plan.trail_distance): the effective stop is max(stop_price, high_water - trail). The stop is checked
against the high water of PREVIOUS observations, then the high water advances (OHLC cannot order a bar's high and
low, so a bar never tightens its own stop). The trail can only tighten the risk-approved stop, never loosen it.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from datetime import timedelta

from wallex_agent.contracts import (
    TERMINAL_ORDER_STATES,
    AutonomyMode,
    ExecutionEvent,
    MarketMeta,
    OrderPurpose,
    OrderState,
    PolicyOutcome,
    RiskOutcome,
    RiskVerdict,
)
from wallex_agent.market.bars import Bar
from wallex_agent.market.book import OrderBook
from wallex_agent.policy import ActionKind, PolicyContext, decide
from wallex_agent.portfolio.ledger import ExitPlan
from wallex_agent.risk.engine import RiskContext, RiskEngine

from .engine import ExecutionEngine

BPS = Decimal(10000)


def effective_stop(plan: ExitPlan, high_water: Decimal | None) -> Decimal:
    if plan.trail_distance is None or high_water is None:
        return plan.stop_price
    return max(plan.stop_price, high_water - plan.trail_distance)


def advance_high_water(high_water: Decimal | None, view: Bar | OrderBook) -> Decimal | None:
    seen = view.high if isinstance(view, Bar) else (view.bids[0][0] if view.bids else None)
    if seen is None:
        return high_water
    return seen if high_water is None else max(high_water, seen)


def action_kind(purpose: OrderPurpose) -> ActionKind:
    if purpose in (OrderPurpose.LIQUIDATION, OrderPurpose.KILL):
        return ActionKind.LIQUIDATION
    return ActionKind.AI_EXIT if purpose is OrderPurpose.EXIT_AI else ActionKind.PROTECTIVE_EXIT


def pick_bar_exit(plan: ExitPlan, bar: Bar, *, now: datetime,
                  high_water: Decimal | None = None) -> tuple[OrderPurpose, Decimal] | None:
    """Stop first when stop and TP are both inside one bar (OHLC cannot tell the order; assume the worse)."""
    stop = effective_stop(plan, high_water)
    if bar.low <= stop:
        return OrderPurpose.EXIT_STOP, stop
    if plan.take_profit_price is not None and bar.high >= plan.take_profit_price:
        return OrderPurpose.EXIT_TAKE_PROFIT, plan.take_profit_price
    if plan.signal_exit:
        return OrderPurpose.EXIT_SIGNAL, bar.close
    if now >= plan.deadline:
        return OrderPurpose.EXIT_TIME, bar.close
    return None


def pick_book_exit(plan: ExitPlan, book: OrderBook, *, now: datetime,
                   high_water: Decimal | None = None) -> tuple[OrderPurpose, Decimal] | None:
    if not book.bids:
        return None
    bid = book.bids[0][0]
    if bid <= effective_stop(plan, high_water):
        return OrderPurpose.EXIT_STOP, bid
    if plan.take_profit_price is not None and bid >= plan.take_profit_price:
        return OrderPurpose.EXIT_TAKE_PROFIT, bid
    if plan.signal_exit:
        return OrderPurpose.EXIT_SIGNAL, bid
    if now >= plan.deadline:
        return OrderPurpose.EXIT_TIME, bid
    return None


@dataclass
class ExitAction:
    symbol: str
    purpose: OrderPurpose
    outcome: Literal["EXECUTED", "PROPOSED", "RISK_REJECTED", "CANCEL_REQUESTED", "RECONCILE_REQUIRED", "INVARIANT_BROKEN"]
    verdict: RiskVerdict | None
    events: list[ExecutionEvent] = field(default_factory=lambda: [])
    decision_id: str = ""


class ExitCoordinator:
    def __init__(self, risk: RiskEngine, execution: ExecutionEngine, *, pricing: Literal["BAR", "BOOK"],
                 exit_slippage_bps: Decimal, live_autonomous_enabled: bool, exit_order_ttl_s: int | None = None) -> None:
        self.risk = risk
        self.execution = execution
        self.pricing = pricing
        self.slip = exit_slippage_bps / BPS
        self.live_autonomous_enabled = live_autonomous_enabled
        self.ttl = timedelta(seconds=exit_order_ttl_s) if exit_order_ttl_s is not None else None

    def _limit(self, purpose: OrderPurpose, hint: Decimal) -> Decimal:
        if self.pricing == "BAR" and purpose in (OrderPurpose.EXIT_STOP, OrderPurpose.EXIT_TAKE_PROFIT):
            return hint
        return hint * (1 - self.slip)

    def _act(self, symbol: str, purpose: OrderPurpose, hint: Decimal, ctx: RiskContext, meta: MarketMeta,
             autonomy: AutonomyMode) -> ExitAction:
        pos = self.risk.ledger.positions[symbol]
        pol = decide(action_kind(purpose), PolicyContext(autonomy, ctx.environment, self.risk.kill.latched, self.live_autonomous_enabled))
        if pol.outcome is not PolicyOutcome.EXECUTE:
            return ExitAction(symbol, purpose, "PROPOSED", None, decision_id=pos.decision_id)
        verdict, intent = self.risk.evaluate_exit(symbol, purpose, self._limit(purpose, hint), pos.decision_id, ctx, meta,
                                                  autonomy=autonomy)
        if intent is None or verdict.outcome is RiskOutcome.REJECT:
            return ExitAction(symbol, purpose, "RISK_REJECTED", verdict, decision_id=pos.decision_id)
        events = self.execution.submit(intent, verdict, now=ctx.now, plan=None, strategy_version=pos.strategy_version)
        return ExitAction(symbol, purpose, "EXECUTED", verdict, events, pos.decision_id)

    def run(self, *, now: datetime, autonomy: AutonomyMode, views: dict[str, Bar | OrderBook],
            ctx_for: Callable[[str], RiskContext], metas: dict[str, MarketMeta], must_flatten: bool) -> list[ExitAction]:
        actions: list[ExitAction] = []
        for symbol, pos in list(self.risk.ledger.positions.items()):
            if symbol not in views or symbol not in metas:
                continue
            view = views[symbol]
            if pos.exit_order_id is not None:
                supervised = (self._supervise(symbol, pos.exit_order_id, view, now, ctx_for(symbol).window.orders_allowed)
                              if self.ttl is not None else None)
                if supervised is not None:
                    actions.append(supervised)
                continue
            trig: tuple[OrderPurpose, Decimal] | None = None
            if pos.plan is not None:
                hw = pos.high_water
                trig = (pick_bar_exit(pos.plan, view, now=now, high_water=hw) if isinstance(view, Bar)
                        else pick_book_exit(pos.plan, view, now=now, high_water=hw))
                if pos.plan.trail_distance is not None:
                    pos.high_water = advance_high_water(hw, view)
            if trig is None and must_flatten:
                hint = view.close if isinstance(view, Bar) else (view.bids[0][0] if view.bids else None)
                if hint is not None:
                    trig = (OrderPurpose.LIQUIDATION, hint)
            if trig is not None:
                actions.append(self._act(symbol, trig[0], trig[1], ctx_for(symbol), metas[symbol], autonomy))
        return actions

    def _supervise(self, symbol: str, cid: str, view: Bar | OrderBook, now: datetime, orders_allowed: bool) -> ExitAction | None:
        assert self.ttl is not None
        try:
            rec = self.execution.order(cid)
        except KeyError:  # the single-exit invariant is broken: stop all entries, a human must inspect
            self.risk.kill.latch(f"exit marker {cid} for {symbol} has no order record", at=now)
            return ExitAction(symbol, OrderPurpose.KILL, "INVARIANT_BROKEN", None)
        purpose = rec.intent.purpose
        if rec.state is OrderState.UNKNOWN:
            return ExitAction(symbol, purpose, "RECONCILE_REQUIRED", None)
        if rec.state in TERMINAL_ORDER_STATES or not orders_allowed:
            return None  # awaiting the confirming lookup by poll(); or no venue write allowed now (window/cutoff)
        if rec.cancel_requested_at is not None:
            if now - (rec.last_cancel_at or rec.cancel_requested_at) < self.ttl:
                return None
            return ExitAction(symbol, purpose, "CANCEL_REQUESTED", None,
                              self.execution.request_cancel(cid, now=now, reason="CANCEL_RETRY"))  # idempotent cancel
        pos = self.risk.ledger.positions[symbol]
        stop = effective_stop(pos.plan, pos.high_water) if pos.plan is not None else None
        price = view.low if isinstance(view, Bar) else (view.bids[0][0] if view.bids else None)
        breach = stop is not None and price is not None and price <= stop and purpose is not OrderPurpose.EXIT_STOP
        if breach or now - rec.intent.created_at >= self.ttl:
            reason = "STOP_BREACH_WHILE_EXIT_RESTS" if breach else "EXIT_ORDER_TTL"
            return ExitAction(symbol, purpose, "CANCEL_REQUESTED", None, self.execution.request_cancel(cid, now=now, reason=reason))
        return None

    def exit_now(self, symbol: str, purpose: OrderPurpose, hint: Decimal, ctx: RiskContext, meta: MarketMeta,
                 autonomy: AutonomyMode) -> ExitAction | None:
        """AI-requested early exit (EXIT_AI) or operator kill flatten (KILL). Same policy + risk path."""
        pos = self.risk.ledger.positions.get(symbol)
        if pos is None or pos.exit_order_id is not None:
            return None
        return self._act(symbol, purpose, hint, ctx, meta, autonomy)
