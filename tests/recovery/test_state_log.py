"""State log + deterministic rebuild: the persisted trade truth that restart recovery replays.

Every test here would fail if the execution engine or the ledger stopped journaling a mutation (fill, state change,
reservation), if the log stopped detecting tampering/gaps, or if replay diverged from the live in-memory ledger.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from wallex_agent.contracts import (
    AutonomyMode,
    Environment,
    MarketMeta,
    OrderIntent,
    OrderPurpose,
    OrderState,
    RiskCheck,
    RiskOutcome,
    RiskVerdict,
)
from wallex_agent.contracts.hashing import content_id
from wallex_agent.execution.engine import ExecutionEngine, OrderRecord
from wallex_agent.execution.sim_shadow import ShadowVenue
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.book import OrderBook
from wallex_agent.portfolio.ledger import ExitPlan, Ledger, reservation_key
from wallex_agent.recovery.rebuild import RebuildError, ledger_digest, rebuild
from wallex_agent.recovery.state_log import StateLog, StateLogCorrupt
from wallex_agent.risk.kill_switch import KillSwitch
from wallex_agent.risk.protections import Protections
from wallex_agent.runtime.config import RiskLimits

T = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FEE = Decimal("0.0025")
META = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=6, min_qty=Decimal("0.000001"),
                  min_notional=Decimal(50000), market_order_enabled=True, metadata_version="t")
LIM = RiskLimits(loss_streak_count=3, loss_streak_window_s=3600, cooldown_s=600, drawdown_pause_fraction=Decimal("0.02"))


def _intent(side: str, qty: str, limit: str, purpose: OrderPurpose, cid: str) -> OrderIntent:
    body = {"intent_id": f"in-{cid}", "decision_id": "d1", "client_order_id": cid, "symbol": "BTCTMN", "side": side,
            "order_type": "LIMIT", "qty": Decimal(qty), "limit_price": Decimal(limit), "purpose": purpose,
            "environment": Environment.SHADOW, "autonomy": AutonomyMode.AUTONOMOUS, "created_at": T}
    return OrderIntent(intent_hash=content_id(body), **body)  # type: ignore[arg-type]


def _verdict(intent: OrderIntent) -> RiskVerdict:
    return RiskVerdict(verdict_id="v", intent_hash=intent.intent_hash, outcome=RiskOutcome.APPROVE, approved_qty=intent.qty,
                       checks=(RiskCheck(name="x", passed=True, detail=""),), limits_version="l", evaluated_at=T,
                       expires_at=T + timedelta(seconds=30))


def _book(bid: int, ask: int, qty: str = "1") -> OrderBook:
    return OrderBook(bids=((Decimal(bid), Decimal(qty)),), asks=((Decimal(ask), Decimal(qty)),), received_at=T)


class Harness:
    def __init__(self, path: Path) -> None:
        self.store = JournalStore(path)
        self.log = StateLog(self.store, "run-1")
        self.books: dict[str, OrderBook] = {"BTCTMN": _book(100_000_000, 100_100_000)}
        self.venue = ShadowVenue(fee_rate=FEE, book=self.books.get)
        self.ledger = Ledger(Decimal(5_000_000), on_reservation=self.log.reservation)
        self.prot = Protections(LIM)
        self.engine = ExecutionEngine(self.venue, self.ledger, self.prot, KillSwitch(), metas={"BTCTMN": META},
                                      max_consecutive_errors=3, recorder=self.log)

    def buy(self, cid: str, qty: str = "0.01", limit: str = "100200000") -> OrderIntent:
        intent = _intent("BUY", qty, limit, OrderPurpose.ENTRY, cid)
        self.ledger.reserve(reservation_key("BTCTMN", intent.intent_id), Decimal(qty) * Decimal(limit) * (1 + FEE))
        self.engine.submit(intent, _verdict(intent), now=T, plan=ExitPlan("c1", Decimal(99_000_000), Decimal(102_000_000),
                                                                           T + timedelta(hours=1)))
        return intent

    def sell(self, cid: str, qty: str = "0.01", limit: str = "98000000") -> OrderIntent:
        intent = _intent("SELL", qty, limit, OrderPurpose.EXIT_STOP, cid)
        self.engine.submit(intent, _verdict(intent), now=T + timedelta(seconds=10), plan=None)
        return intent


def test_replay_reproduces_ledger_orders_and_loss_history(tmp_path: Path) -> None:
    h = Harness(tmp_path / "j.sqlite")
    h.buy("wa-1")
    h.books["BTCTMN"] = _book(98_500_000, 98_600_000)
    h.sell("wa-2")
    assert len(h.ledger.trades) == 1 and h.ledger.trades[0].net_pnl_tmn < 0

    ledger, prot = Ledger(Decimal(5_000_000)), Protections(LIM)
    rebuilt = rebuild(StateLog(h.store, "run-1").records(), ledger=ledger, protections=prot)

    assert ledger_digest(ledger) == ledger_digest(h.ledger)
    assert prot.exits == h.prot.exits
    assert {cid: r.state for cid, r in rebuilt.orders.items()} == {"wa-1": OrderState.FILLED, "wa-2": OrderState.FILLED}
    assert ledger.reservations == {}


def test_open_position_keeps_plan_and_pending_exit_marker(tmp_path: Path) -> None:
    h = Harness(tmp_path / "j.sqlite")
    h.buy("wa-1")
    sell = _intent("SELL", "0.01", "98000000", OrderPurpose.EXIT_STOP, "wa-2")
    # Persisted INTENT/SUBMITTING, then the process died before the venue answered.
    h.log.order_opened(OrderRecord(sell, _verdict(sell), None, "s"), T)
    h.log.append("STATE", {"cid": "wa-2", "state": "SUBMITTING", "raw": None, "cum_filled_qty": "0"}, T)

    ledger = Ledger(Decimal(5_000_000))
    rebuilt = rebuild(h.log.records(), ledger=ledger, protections=Protections(LIM))
    pos = ledger.positions["BTCTMN"]
    assert pos.plan == ExitPlan("c1", Decimal(99_000_000), Decimal(102_000_000), T + timedelta(hours=1))
    assert pos.exit_order_id == "wa-2"
    assert rebuilt.orders["wa-2"].state is OrderState.SUBMITTING


def test_unterminated_entry_keeps_its_reservation(tmp_path: Path) -> None:
    h = Harness(tmp_path / "j.sqlite")
    h.books["BTCTMN"] = OrderBook(bids=(), asks=(), received_at=T)
    h.venue.drop_next_response = True
    h.venue.forget_orders = True
    h.buy("wa-1")
    assert h.engine.order("wa-1").state is OrderState.UNKNOWN
    ledger = Ledger(Decimal(5_000_000))
    rebuild(h.log.records(), ledger=ledger, protections=Protections(LIM))
    assert ledger.reservations == h.ledger.reservations and ledger.reserved_tmn > 0


def test_tampered_record_is_detected(tmp_path: Path) -> None:
    h = Harness(tmp_path / "j.sqlite")
    h.buy("wa-1")
    db = sqlite3.connect(tmp_path / "j.sqlite")
    db.execute("UPDATE state_log SET body = replace(body, '0.01', '0.02') WHERE kind = 'FILL'")
    db.commit()
    with pytest.raises(StateLogCorrupt, match="hash"):
        StateLog(h.store, "run-1").records()


def test_missing_record_is_detected(tmp_path: Path) -> None:
    h = Harness(tmp_path / "j.sqlite")
    h.buy("wa-1")
    db = sqlite3.connect(tmp_path / "j.sqlite")
    db.execute("DELETE FROM state_log WHERE seq = 2")
    db.commit()
    with pytest.raises(StateLogCorrupt, match="gap|chain"):
        StateLog(h.store, "run-1").records()


def test_fill_for_unknown_order_fails_rebuild(tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    log = StateLog(store, "run-1")
    log.append("FILL", {"cid": "ghost", "fill_id": "f", "qty": "1", "price": "1", "fee": "0", "fee_asset": "TMN",
                        "at": T.isoformat(), "dust_qty": "0", "cash_after": "0", "position_qty_after": "1"}, T)
    with pytest.raises(RebuildError, match="unknown order"):
        rebuild(log.records(), ledger=Ledger(Decimal(5_000_000)), protections=Protections(LIM))


def test_fill_that_diverges_from_recorded_balance_fails_rebuild(tmp_path: Path) -> None:
    h = Harness(tmp_path / "j.sqlite")
    h.buy("wa-1")
    # Same log replayed onto a ledger with a different starting balance cannot match the recorded cash_after.
    with pytest.raises(RebuildError, match="cash"):
        rebuild(h.log.records(), ledger=Ledger(Decimal(6_000_000)), protections=Protections(LIM))


def test_log_resumes_sequence_after_reopen(tmp_path: Path) -> None:
    h = Harness(tmp_path / "j.sqlite")
    h.buy("wa-1")
    n = len(h.log.records())
    h.store.close()
    store = JournalStore(tmp_path / "j.sqlite")
    log = StateLog(store, "run-1")
    log.append("CHECKPOINT", {"reason": "TEST"}, T)
    records = log.records()
    assert [r.seq for r in records] == list(range(1, n + 2))


def test_naive_timestamps_are_rejected(tmp_path: Path) -> None:
    h = Harness(tmp_path / "j.sqlite")
    sell = _intent("SELL", "0.01", "98000000", OrderPurpose.EXIT_STOP, "wa-9")
    h.log.order_opened(OrderRecord(sell, _verdict(sell), ExitPlan("c", Decimal(1), None, datetime(2026, 10, 22, 10)), "s"), T)
    with pytest.raises(RebuildError, match="timezone"):
        rebuild(h.log.records(), ledger=Ledger(Decimal(5_000_000)), protections=Protections(LIM))


def test_rebuilt_cancel_pending_exit_keeps_its_cancel_time_for_double_confirmation(tmp_path: Path) -> None:
    h = Harness(tmp_path / "j.sqlite")
    h.buy("wa-1")
    sell = _intent("SELL", "0.01", "98000000", OrderPurpose.EXIT_TIME, "wa-2")
    h.log.order_opened(OrderRecord(sell, _verdict(sell), None, "s"), T)
    h.log.append("STATE", {"cid": "wa-2", "state": "OPEN", "raw": "NEW", "cum_filled_qty": "0"}, T)
    h.log.append("STATE", {"cid": "wa-2", "state": "CANCEL_PENDING", "raw": "EXIT_ORDER_TTL", "cum_filled_qty": "0"},
                 T + timedelta(seconds=60))  # cancel requested, then the process died before the confirmation
    rebuilt = rebuild(h.log.records(), ledger=Ledger(Decimal(5_000_000)), protections=Protections(LIM))
    rec = rebuilt.orders["wa-2"]
    assert rec.state is OrderState.CANCEL_PENDING and rec.cancel_requested_at == T + timedelta(seconds=60)


def test_cancel_hold_is_reproduced_on_replay_so_checkpoints_match(tmp_path: Path) -> None:
    h = Harness(tmp_path / "j.sqlite")
    h.buy("wa-1")
    sell = _intent("SELL", "0.01", "98000000", OrderPurpose.EXIT_TIME, "wa-2")
    h.log.order_opened(OrderRecord(sell, _verdict(sell), None, "s"), T)
    for state, raw in (("OPEN", "NEW"), ("CANCEL_PENDING", "EXIT_ORDER_TTL"), ("CANCELED", "CANCELED")):
        h.log.append("STATE", {"cid": "wa-2", "state": state, "raw": raw, "cum_filled_qty": "0"}, T + timedelta(seconds=60))
    ledger = Ledger(Decimal(5_000_000))
    rebuilt = rebuild(h.log.records(), ledger=ledger, protections=Protections(LIM))
    assert ledger.positions["BTCTMN"].exit_order_id == "wa-2"  # cancelled once, not yet confirmed: still held
    assert rebuilt.orders["wa-2"].terminal_seen == 1
    h.log.append("STATE", {"cid": "wa-2", "state": "CANCELED", "raw": "CANCEL_CONFIRMED", "cum_filled_qty": "0"},
                 T + timedelta(seconds=75))
    ledger2 = Ledger(Decimal(5_000_000))
    rebuild(h.log.records(), ledger=ledger2, protections=Protections(LIM))
    assert ledger2.positions["BTCTMN"].exit_order_id is None  # confirmed: released, exactly like the live process
