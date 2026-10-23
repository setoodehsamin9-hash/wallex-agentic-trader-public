"""LIVE exit safety: a resting non-filled SELL can never leave a position unprotected or be double-sold.

Rules pinned here (Phase 05): every resting exit order has a TTL; on expiry, or when the stop is breached while a
non-stop exit rests, the order is cancelled (CANCEL_PENDING persisted before the venue call); a new SELL is issued
only after the cancel is confirmed terminal by a SECOND later lookup (fill-after-cancel race); a lost cancel response
is UNKNOWN and is reconciled, never resubmitted; partial fills shrink the re-issued quantity; TTL unset = old behaviour.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from wallex_agent.contracts import (
    AutonomyMode,
    DataRef,
    Environment,
    FillSource,
    MarketMeta,
    MarketSnapshot,
    OrderIntent,
    OrderPurpose,
    OrderState,
)
from wallex_agent.execution.engine import ExecutionEngine
from wallex_agent.execution.exits import ExitCoordinator
from wallex_agent.execution.venue import VenueFill, VenueOrder
from wallex_agent.market.book import OrderBook
from wallex_agent.portfolio.ledger import ExitPlan, Fill, Ledger
from wallex_agent.risk.engine import RiskContext, RiskEngine
from wallex_agent.risk.kill_switch import KillSwitch
from wallex_agent.risk.protections import Protections
from wallex_agent.runtime.calendar import WindowStatus
from wallex_agent.runtime.config import CostModel, CutoffLimits, DataLimits, ExecutionSettings, KillLimits, Limits, RiskLimits

T = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
TTL = 60
META = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=6, min_qty=Decimal("0.000001"),
                  min_notional=Decimal(50000), market_order_enabled=True, metadata_version="t")
RISK = RiskLimits(
    per_trade_budget_fraction=Decimal("0.005"), total_loss_budget_tmn=Decimal(150000), session_loss_budget_tmn=Decimal(75000),
    max_position_notional_tmn=Decimal(1_500_000), max_total_exposure_tmn=Decimal(2_500_000), max_open_positions=2,
    drawdown_pause_fraction=Decimal("0.02"), drawdown_emergency_fraction=Decimal("0.04"), loss_streak_count=3,
    loss_streak_window_s=14400, cooldown_s=3600, max_spread_bps=Decimal(30), max_depth_fraction=Decimal("0.25"),
    stress_cost_bps=Decimal(10), verdict_ttl_s=30, cautious_size_fraction=Decimal("0.5"))
LIMITS = Limits(version="lim-t", risk=RISK, data=DataLimits(max_bar_age_s=1860, max_book_age_s=30),
                cutoff=CutoffLimits(no_new_entry_before_end_s=3600, liquidation_start_before_end_s=1800),
                kill=KillLimits(max_consecutive_execution_errors=3, reconciliation_tolerance_tmn=Decimal(1000)))
COSTS = CostModel(fee_rate=Decimal("0.0025"), backtest_slippage_bps=Decimal(5))
EXE = ExecutionSettings(entry_limit_slippage_bps=Decimal(20), exit_limit_slippage_bps=Decimal(30), entry_ttl_s=900)
OPEN = WindowStatus(True, True, True, False, "OPEN", None, None, "UNBOUNDED", None)
QTY = Decimal("0.01")


@dataclass
class RestingVenue:
    """Exchange fake: limit SELLs rest unfilled; cancel outcome is scripted ('ok', 'timeout', 'partial')."""
    environment: Environment = Environment.LIVE
    fill_source: FillSource = FillSource.EXCHANGE
    cancel_mode: str = "ok"
    orders: dict[str, VenueOrder] = field(default_factory=lambda: {})
    submitted: list[OrderIntent] = field(default_factory=lambda: [])
    cancels: list[str] = field(default_factory=lambda: [])

    def submit(self, intent: OrderIntent, now: datetime) -> VenueOrder:
        self.submitted.append(intent)
        vo = VenueOrder(intent.client_order_id, intent, OrderState.OPEN, "NEW")
        self.orders[intent.client_order_id] = vo
        return vo

    def query(self, client_order_id: str) -> VenueOrder | None:
        return self.orders.get(client_order_id)

    def cancel(self, client_order_id: str, now: datetime) -> VenueOrder | None:
        self.cancels.append(client_order_id)
        vo = self.orders[client_order_id]
        if self.cancel_mode == "timeout":
            return None
        if self.cancel_mode == "partial":
            vo.fills.append(VenueFill(f"{client_order_id}-f0", vo.intent.qty * Decimal("0.4"), Decimal(99_800_000),
                                      Decimal(0), "TMN", now))
        vo.state, vo.status_raw = OrderState.CANCELED, "CANCELED"
        return vo

    def fill_all(self, client_order_id: str, now: datetime) -> None:
        vo = self.orders[client_order_id]
        vo.fills.append(VenueFill(f"{client_order_id}-late", vo.intent.qty - vo.filled_qty, Decimal(99_700_000), Decimal(0),
                                  "TMN", now))
        vo.state, vo.status_raw = OrderState.FILLED, "FILLED"

    def open_sells(self) -> list[str]:
        return [c for c, v in self.orders.items() if v.intent.side == "SELL" and v.state is OrderState.OPEN]


class Rig:
    def __init__(self, *, ttl: int | None = TTL, cancel_mode: str = "ok", deadline: datetime = T) -> None:
        self.ledger = Ledger(Decimal(5_000_000))
        self.kill = KillSwitch()
        self.prot = Protections(LIMITS.risk)
        self.risk = RiskEngine(LIMITS, COSTS, EXE, self.ledger, self.kill, self.prot, starting_capital_tmn=Decimal(5_000_000))
        self.venue = RestingVenue(cancel_mode=cancel_mode)
        self.exe = ExecutionEngine(self.venue, self.ledger, self.prot, self.kill, metas={"BTCTMN": META}, max_consecutive_errors=3)
        self.exits = ExitCoordinator(self.risk, self.exe, pricing="BOOK", exit_slippage_bps=Decimal(30),
                                     live_autonomous_enabled=True, exit_order_ttl_s=ttl)
        self.window = OPEN
        plan = ExitPlan("c", Decimal(95_000_000), None, deadline)
        self.ledger.apply_fill(Fill("BTCTMN", "BUY", QTY, Decimal(100_000_000), Decimal(0), "TMN", T - timedelta(hours=5), "e1",
                                    "ENTRY", "d1", "f2-test"), plan=plan)

    def book(self, bid: int, now: datetime) -> OrderBook:
        return OrderBook(bids=((Decimal(bid), Decimal(1)),), asks=((Decimal(bid + 100_000), Decimal(1)),), received_at=now)

    def run(self, now: datetime, bid: int = 100_000_000) -> list[str]:
        book = self.book(bid, now)
        ref = DataRef(dataset_id="d", content_sha256="0" * 64, source="FIXTURE", symbol="BTCTMN", first_ts=now, last_ts=now,
                      resolution_seconds=14400)
        snap = MarketSnapshot(snapshot_id="s", symbol="BTCTMN", as_of=now, bar_interval_seconds=14400,
                              last_closed_bar_open=now - timedelta(hours=4), candles=ref, book=book.top(), meta=META,
                              universe_version="u", quality_flags=frozenset())
        ctx = RiskContext(now=now, snapshot=snap, book=book, marks={"BTCTMN": Decimal(bid)}, eligible=frozenset({"BTCTMN"}),
                          window=self.window, require_book=True, environment=Environment.LIVE)
        actions = self.exits.run(now=now, autonomy=AutonomyMode.AUTONOMOUS, views={"BTCTMN": book}, ctx_for=lambda s: ctx,
                                 metas={"BTCTMN": META}, must_flatten=False)
        return [a.outcome for a in actions]

    def pending(self) -> str | None:
        pos = self.ledger.positions.get("BTCTMN")
        return pos.exit_order_id if pos else None


def test_expired_resting_exit_is_cancelled_and_reissued_only_after_confirmation() -> None:
    rig = Rig()
    assert rig.run(T) == ["EXECUTED"]  # time exit (deadline passed) rests at the venue
    first = rig.pending()
    assert first is not None and rig.run(T + timedelta(seconds=TTL - 1)) == []  # before TTL: untouched, no second sell
    assert rig.run(T + timedelta(seconds=TTL)) == ["CANCEL_REQUESTED"]
    states = [e.state for e in rig.exe.order(first).events]
    assert states.index(OrderState.CANCEL_PENDING) < states.index(OrderState.CANCELED)  # persisted before the venue call
    assert rig.pending() == first and rig.run(T + timedelta(seconds=TTL + 1)) == []  # awaiting a confirming lookup
    rig.exe.poll(now=T + timedelta(seconds=TTL + 2))
    assert rig.pending() is None
    assert rig.run(T + timedelta(seconds=TTL + 3)) == ["EXECUTED"]
    sells = [i for i in rig.venue.submitted if i.side == "SELL"]
    assert len(sells) == 2 and sells[0].client_order_id != sells[1].client_order_id
    assert len(rig.venue.open_sells()) == 1  # never two live sells for one position


def test_stop_breach_cancels_a_resting_non_stop_exit_without_waiting_for_ttl() -> None:
    rig = Rig()
    rig.run(T)
    first = rig.pending()
    assert rig.run(T + timedelta(seconds=5), bid=94_000_000) == ["CANCEL_REQUESTED"]  # below the 95M stop
    rig.exe.poll(now=T + timedelta(seconds=6))
    assert rig.run(T + timedelta(seconds=7), bid=94_000_000) == ["EXECUTED"]
    second = rig.venue.submitted[-1]
    assert first != second.client_order_id and second.purpose is OrderPurpose.EXIT_STOP


def test_lost_cancel_response_is_unknown_and_never_resubmitted() -> None:
    rig = Rig(cancel_mode="timeout")
    rig.run(T)
    first = rig.pending()
    assert first is not None and rig.run(T + timedelta(seconds=TTL)) == ["CANCEL_REQUESTED"]
    assert rig.exe.order(first).state is OrderState.UNKNOWN
    rig.venue.orders.pop(first)  # the exchange lookup also fails: outcome stays unknown
    rig.exe.poll(now=T + timedelta(seconds=TTL + 5))
    for k in range(1, 4):
        assert rig.run(T + timedelta(seconds=TTL + 10 * k), bid=94_000_000) == ["RECONCILE_REQUIRED"]
    assert [i.side for i in rig.venue.submitted] == ["SELL"] and rig.pending() == first
    assert first in rig.exe.unresolved()


def test_partial_fill_during_cancel_reissues_only_the_remainder() -> None:
    rig = Rig(cancel_mode="partial")
    rig.run(T)
    rig.run(T + timedelta(seconds=TTL))
    rig.exe.poll(now=T + timedelta(seconds=TTL + 1))
    pos = rig.ledger.positions["BTCTMN"]
    assert pos.qty == QTY * Decimal("0.6")
    rig.run(T + timedelta(seconds=TTL + 2))
    assert rig.venue.submitted[-1].qty == QTY * Decimal("0.6")


def test_fill_after_cancel_race_is_applied_once_and_blocks_a_second_sell() -> None:
    rig = Rig()
    rig.run(T)
    first = rig.pending()
    assert first is not None
    rig.run(T + timedelta(seconds=TTL))  # cancel acknowledged as CANCELED with no fill...
    rig.venue.fill_all(first, T + timedelta(seconds=TTL + 1))  # ...but the exchange reports a late fill
    rig.exe.poll(now=T + timedelta(seconds=TTL + 2))
    assert "BTCTMN" not in rig.ledger.positions and len(rig.ledger.trades) == 1
    assert rig.run(T + timedelta(seconds=TTL + 3)) == []
    assert [i.side for i in rig.venue.submitted] == ["SELL"]


def test_ttl_unset_keeps_the_previous_behaviour() -> None:
    rig = Rig(ttl=None)
    rig.run(T)
    assert rig.run(T + timedelta(hours=1), bid=94_000_000) == [] and rig.venue.cancels == []


def test_first_lookup_filled_after_cancel_releases_at_once_and_rejected_too() -> None:
    rig = Rig()
    rig.run(T)
    first = rig.pending()
    assert first is not None
    vo = rig.venue.orders[first]
    rig.venue.cancel_mode = "ok"
    rig.venue.fill_all(first, T + timedelta(seconds=TTL))  # filled before our cancel landed
    rig.venue.cancel = lambda cid, now: vo  # type: ignore[method-assign]
    # the position grew after the exit was sized (late entry fill): the remainder must be re-protected
    rig.ledger.apply_fill(Fill("BTCTMN", "BUY", Decimal("0.005"), Decimal(100_000_000), Decimal(0), "TMN", T, "e2", "ENTRY",
                               "d2", "f2-test"), plan=None)
    rig.run(T + timedelta(seconds=TTL))
    assert rig.exe.order(first).state is OrderState.FILLED
    assert rig.ledger.positions["BTCTMN"].qty == Decimal("0.005") and rig.pending() is None  # FILLED is final at once


def test_cancel_retry_waits_a_ttl_and_journals_its_reason() -> None:
    rig = Rig()
    rig.run(T)
    first = rig.pending()
    assert first is not None
    vo = rig.venue.orders[first]

    def still_open(cid: str, now: datetime) -> VenueOrder:
        rig.venue.cancels.append(cid)
        return vo  # exchange keeps reporting OPEN

    rig.venue.cancel = still_open  # type: ignore[method-assign]
    assert rig.run(T + timedelta(seconds=TTL)) == ["CANCEL_REQUESTED"]
    assert rig.run(T + timedelta(seconds=TTL + 10)) == [] and len(rig.venue.cancels) == 1  # not every cycle
    assert rig.run(T + timedelta(seconds=2 * TTL)) == ["CANCEL_REQUESTED"] and len(rig.venue.cancels) == 2
    raws = [e.exchange_status_raw for e in rig.exe.order(first).events if e.state is OrderState.CANCEL_PENDING]
    assert raws == ["EXIT_ORDER_TTL", "CANCEL_RETRY"]


def test_no_cancel_is_sent_when_orders_are_not_allowed() -> None:
    rig = Rig()
    rig.run(T)
    closed = WindowStatus(False, False, False, False, "OUTSIDE_TRADING_WINDOW", None, None, "ACTIVE", "tw")
    rig.window = closed
    assert rig.run(T + timedelta(seconds=TTL), bid=94_000_000) == [] and rig.venue.cancels == []


def test_exit_marker_without_an_order_record_latches_the_kill_switch() -> None:
    rig = Rig()
    rig.ledger.positions["BTCTMN"].exit_order_id = "ghost"
    assert rig.run(T) == ["INVARIANT_BROKEN"] and rig.kill.latched


def test_exit_order_ttl_is_bounded() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExecutionSettings(entry_limit_slippage_bps=Decimal(20), exit_limit_slippage_bps=Decimal(30), entry_ttl_s=900,
                          exit_order_ttl_s=3600)
