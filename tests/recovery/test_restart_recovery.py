"""Restart / crash recovery scenarios through the real runtime, kernel and journal (fakes for venues only).

A "restart" is always: drop the runtime object, reopen the SQLite journal from disk, build a NEW runtime with
`resume=True` and a NEW venue object, and let `RecoveryCoordinator` rebuild everything from persisted state.
"""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from support.recovery_fakes import Crash, ExchangeLikeVenue

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.approval.service import ApprovalError, ApprovalService
from wallex_agent.contracts import (
    ApprovalStatus,
    AutonomyMode,
    CodeVersion,
    CycleStatus,
    DecisionRecord,
    Environment,
    ExecutionEvent,
    OrderIntent,
    OrderPurpose,
    OrderState,
    RiskCheck,
    RiskOutcome,
    RiskVerdict,
)
from wallex_agent.contracts.hashing import content_id
from wallex_agent.execution.engine import OrderRecord
from wallex_agent.execution.sim_shadow import ShadowVenue
from wallex_agent.execution.venue import Venue
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import aggregate
from wallex_agent.market.book import OrderBook
from wallex_agent.market.dataset import Dataset
from wallex_agent.portfolio.ledger import ExitPlan
from wallex_agent.recovery.coordinator import RecoveryCoordinator, RecoveryOutcome
from wallex_agent.recovery.rebuild import ledger_digest
from wallex_agent.runtime.backtest import universe_from_metadata
from wallex_agent.runtime.config import RunConfig
from wallex_agent.runtime.engine import RecoveryRequired, TradingRuntime

T = datetime(2026, 10, 3, 6, 15, tzinfo=UTC)
CODE = CodeVersion(git_commit="a" * 40, dirty=False)


class Rig:
    """One process incarnation: a runtime over a journal file, a venue and the order books it sees."""

    def __init__(self, cfg: RunConfig, ds: Dataset, path: Path, venue: Venue | None = None, *, resume: bool = False,
                 code: CodeVersion = CODE) -> None:
        self.ds = ds
        self.store = JournalStore(path)
        self.books: dict[str, OrderBook] = {}
        self.venue: Venue = venue or ShadowVenue(fee_rate=cfg.costs.fee_rate, book=self.books.get)
        metas, eligible, universe = universe_from_metadata(ds.path / "metadata")
        self.rt = TradingRuntime(cfg, store=self.store, venue=self.venue, decider=BaselineSelector(), metas=metas,
                                 eligible=eligible, universe_version=universe, run_id="run", code=code, dataset_id=ds.name,
                                 data_source="FIXTURE", resume=resume)
        if not resume:
            self.rt.start(T - timedelta(minutes=1), dependency_lock_sha256="a" * 64)
        self.quote(T)

    def quote(self, at: datetime, *, factor: Decimal = Decimal(1)) -> None:
        for symbol in self.rt.symbols:
            price = [b for b in self.ds.bars[symbol] if b.ts_open + timedelta(minutes=1) <= T][-1].close * factor
            book = OrderBook(bids=((price * Decimal("0.9997"), Decimal(1)),), asks=((price * Decimal("1.0003"), Decimal(1)),),
                             received_at=at)
            self.books[symbol] = book
            self.rt.update_market(symbol, mark=price, book=book)

    def bars(self, symbol: str, as_of: datetime):  # type: ignore[no-untyped-def]
        bars = [b for b in aggregate(self.ds.bars[symbol], 900) if b.ts_open + timedelta(seconds=900) <= as_of]
        return bars[-self.rt.history_window:]

    def cycle(self, as_of: datetime = T) -> DecisionRecord:
        return self.rt.cycle("BTCTMN", as_of, self.bars("BTCTMN", as_of), flags=frozenset())

    def prime(self, as_of: datetime) -> None:
        """What the SHADOW resume loop does before its first maintenance pass: market views for the exit watcher."""
        for symbol in self.rt.symbols:
            self.rt.prime_snapshot(symbol, as_of, self.bars(symbol, as_of), flags=frozenset())

    def recover(self, at: datetime, **kw: object):  # type: ignore[no-untyped-def]
        return RecoveryCoordinator(self.rt, **kw).recover(at)  # type: ignore[arg-type]


def _shadow(make_cfg: Callable[..., RunConfig], **extra: object) -> RunConfig:
    return make_cfg(**{"run.environment": Environment.SHADOW, **extra})


def _enter(rig: Rig) -> DecisionRecord:
    rec = rig.cycle()
    assert rec.status is CycleStatus.FILLED, rec.status
    return rec


# ---------------------------------------------------------------------------------------------- 1, 2, 14 positions
def test_clean_shutdown_then_restart_restores_identical_state(make_cfg: Callable[..., RunConfig],
                                                              synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    _enter(a)
    a.rt.checkpoint("SHUTDOWN", T + timedelta(seconds=5))
    before, plan = ledger_digest(a.rt.ledger), a.rt.ledger.positions["BTCTMN"].plan
    a.store.close()

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", resume=True)
    report = b.recover(T + timedelta(minutes=2))
    assert report.outcome is RecoveryOutcome.READY, report.blockers
    assert report.checkpoints_verified == 1
    assert ledger_digest(b.rt.ledger) == before
    assert b.rt.ledger.positions["BTCTMN"].plan == plan
    assert [e.symbol for e in report.rearmed_exits] == ["BTCTMN"]
    kinds = [e["kind"] for e in b.store.risk_events("run")]
    for k in ("RECOVERY_STARTED", "RECOVERY_STATE_RECONSTRUCTED", "RECOVERY_EXIT_REARMED", "RECOVERY_READY"):
        assert k in kinds


def test_crash_with_open_position_rearms_stop_and_never_double_sells(make_cfg: Callable[..., RunConfig],
                                                                    synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    _enter(a)
    held = a.rt.ledger.positions["BTCTMN"].qty
    stop = a.rt.ledger.positions["BTCTMN"].plan.stop_price  # type: ignore[union-attr]
    # no checkpoint, no close: the process just dies

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", resume=True)
    assert b.recover(T + timedelta(minutes=1)).outcome is RecoveryOutcome.READY
    b.prime(T)
    later = T + timedelta(minutes=15)
    b.quote(later)
    assert b.cycle(later).status is not CycleStatus.FILLED  # it remembers the position: no second entry
    assert len(b.rt.ledger.positions) == 1 and b.venue.submitted == []  # type: ignore[attr-defined]

    b.quote(later, factor=stop / b.rt.symbols["BTCTMN"].mark * Decimal("0.99"))  # type: ignore[operator]
    first = b.rt.run_exits(later, dict(b.books))
    again = b.rt.run_exits(later + timedelta(seconds=1), dict(b.books))
    assert [x.outcome for x in first] == ["EXECUTED"] and again == []
    assert len(b.venue.submitted) == 1  # type: ignore[attr-defined]
    assert "BTCTMN" not in b.rt.ledger.positions
    assert b.rt.ledger.trades[-1].qty == held


def test_unresolved_exit_after_restart_blocks_a_second_sell_until_reconciled(make_cfg: Callable[..., RunConfig],
                                                                            synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    ex = ExchangeLikeVenue(fee_rate=cfg.costs.fee_rate, cash=cfg.run.starting_capital_tmn)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex)
    _enter(a)
    stop = a.rt.ledger.positions["BTCTMN"].plan.stop_price  # type: ignore[union-attr]
    a.quote(T + timedelta(minutes=1), factor=stop / a.rt.symbols["BTCTMN"].mark * Decimal("0.99"))  # type: ignore[operator]
    ex.crash_after_accept, ex.mode = True, "REST"
    with pytest.raises(Crash):
        a.rt.run_exits(T + timedelta(minutes=1), dict(a.books))
    sell = ex.submits[-1]
    ex.crash_after_accept = False
    ex.hidden.add(sell)  # the exchange does not report it yet

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex, resume=True)
    report = b.recover(T + timedelta(minutes=2), balances=ex.balances)
    b.prime(T)
    assert report.outcome is RecoveryOutcome.EXITS_ONLY
    assert report.unresolved_orders == (sell,)
    assert report.rearmed_exits[0].pending_exit == sell
    b.quote(T + timedelta(minutes=2), factor=stop / b.rt.symbols["BTCTMN"].mark * Decimal("0.98"))  # type: ignore[operator]
    assert b.rt.run_exits(T + timedelta(minutes=2), dict(b.books)) == []
    assert ex.submits.count(sell) == 1 and len(ex.submits) == 2
    blocked = b.cycle(T + timedelta(minutes=15))
    assert blocked.status is CycleStatus.BLOCKED and blocked.block_reason is not None
    assert blocked.block_reason.startswith("RECOVERY_RECONCILIATION_PENDING")

    assert "BALANCE_RECONCILIATION_PENDING" in b.rt.entry_gate  # balances cannot be compared while an order is UNKNOWN

    ex.hidden.clear()
    ex.complete(sell, T + timedelta(minutes=3))
    b.rt.poll(T + timedelta(minutes=3))
    assert "BTCTMN" not in b.rt.ledger.positions and b.rt.entry_gate == {}
    kinds = [e["kind"] for e in b.store.risk_events("run")]
    assert kinds.index("RECOVERY_BALANCES_RECONCILED") < kinds.index("RECOVERY_RECONCILED")
    assert len(ex.submits) == 2


def test_late_balance_mismatch_keeps_entries_gated_and_latches(make_cfg: Callable[..., RunConfig],
                                                               synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    ex = ExchangeLikeVenue(fee_rate=cfg.costs.fee_rate, cash=cfg.run.starting_capital_tmn, mode="REST")
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex)
    assert a.cycle().status is CycleStatus.SUBMITTED
    entry = ex.submits[0]
    ex.hidden.add(entry)
    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex, resume=True)
    assert b.recover(T + timedelta(minutes=1), balances=ex.balances).outcome is RecoveryOutcome.EXITS_ONLY
    ex.hidden.clear()
    ex.cash -= Decimal(50_000)  # an untracked withdrawal/trade happened meanwhile
    b.rt.poll(T + timedelta(minutes=2))
    assert "order:" + entry not in b.rt.entry_gate
    assert "BALANCE_RECONCILIATION_PENDING" in b.rt.entry_gate and b.rt.kill.latched


def test_unexpected_recovery_error_is_journaled_blocked_and_latched(make_cfg: Callable[..., RunConfig],
                                                                   synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    ex = ExchangeLikeVenue(fee_rate=cfg.costs.fee_rate, cash=cfg.run.starting_capital_tmn)
    Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex)

    def broken() -> dict[str, Decimal]:
        raise OSError("balance endpoint down")

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex, resume=True)
    report = b.recover(T + timedelta(minutes=1), balances=broken)
    assert report.outcome is RecoveryOutcome.BLOCKED and report.blockers[0].startswith("RECOVERY_ERROR")
    assert b.rt.kill.latched and "RECOVERY_BLOCKED" in [e["kind"] for e in b.store.risk_events("run")]


def test_trade_closed_but_not_flushed_before_crash_reaches_the_trades_table(
        make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    _losing_round_trip(a, 0, T)
    assert len(a.rt.ledger.trades) == 1
    assert a.store.trades("run") == []  # engine-level fills: the runtime never flushed the trade
    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", resume=True)
    assert b.recover(T + timedelta(minutes=1)).outcome is RecoveryOutcome.READY
    assert len(b.store.trades("run")) == 1


# ---------------------------------------------------------------------------------------------- 3, 4, 5, 6 crash points
class CrashingShadowVenue(ShadowVenue):
    def submit(self, intent, now):  # type: ignore[no-untyped-def]
        raise Crash("died after INTENT/SUBMITTING were persisted, before the venue call")


def test_crash_after_intent_persisted_before_submit_simulated(make_cfg: Callable[..., RunConfig],
                                                              synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    path = tmp_path / "j.sqlite"
    a = Rig(cfg, synthetic_dataset, path)
    a.rt.venue = a.rt.execution.venue = CrashingShadowVenue(fee_rate=cfg.costs.fee_rate, book=a.books.get)
    with pytest.raises(Crash):
        a.cycle()
    assert a.rt.ledger.reserved_tmn > 0

    b = Rig(cfg, synthetic_dataset, path, resume=True)
    report = b.recover(T + timedelta(minutes=1))
    assert report.outcome is RecoveryOutcome.READY
    assert len(report.ambiguous_orders) == 1 and report.settled_orders == report.ambiguous_orders
    cid = report.ambiguous_orders[0]
    assert b.rt.execution.order(cid).state is OrderState.CANCELED
    assert b.rt.ledger.reservations == {} and b.rt.ledger.positions == {}
    assert b.venue.submitted == []  # type: ignore[attr-defined]  # never resubmitted


def test_crash_before_exchange_received_order_stays_unknown_and_blocks_entries(
        make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    ex = ExchangeLikeVenue(fee_rate=cfg.costs.fee_rate, cash=cfg.run.starting_capital_tmn)
    ex.crash_before_accept = True
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex)
    with pytest.raises(Crash):
        a.cycle()
    reserved = dict(a.rt.ledger.reservations)
    ex.crash_before_accept = False

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex, resume=True)
    report = b.recover(T + timedelta(minutes=1), balances=ex.balances)
    assert report.outcome is RecoveryOutcome.EXITS_ONLY
    assert len(report.unresolved_orders) == 1
    assert b.rt.ledger.reservations == reserved  # capital stays reserved while the outcome is unknown
    assert b.cycle(T + timedelta(minutes=15)).status is CycleStatus.BLOCKED
    assert ex.submits == []
    assert "RECOVERY_RECONCILIATION_REQUIRED" in [e["kind"] for e in b.store.risk_events("run")]


def test_exchange_accepted_response_lost_reconciles_without_resubmit(make_cfg: Callable[..., RunConfig],
                                                                     synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    ex = ExchangeLikeVenue(fee_rate=cfg.costs.fee_rate, cash=cfg.run.starting_capital_tmn)
    ex.crash_after_accept = True
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex)
    with pytest.raises(Crash):
        a.cycle()
    assert a.rt.ledger.positions == {}  # the fill never reached our ledger
    ex.crash_after_accept = False

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex, resume=True)
    report = b.recover(T + timedelta(minutes=1), balances=ex.balances)
    assert report.outcome is RecoveryOutcome.READY, report.blockers
    assert report.reconciled_orders == report.ambiguous_orders and len(ex.submits) == 1
    pos = b.rt.ledger.positions["BTCTMN"]
    assert pos.qty == ex.orders[ex.submits[0]].filled_qty and pos.plan is not None
    assert b.rt.ledger.reservations == {}
    b.rt.poll(T + timedelta(minutes=2))  # applying the same fills again must be impossible
    assert b.rt.ledger.positions["BTCTMN"].qty == pos.qty and len(ex.submits) == 1


def test_partial_fill_before_crash_applies_only_new_fills(make_cfg: Callable[..., RunConfig],
                                                          synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    ex = ExchangeLikeVenue(fee_rate=cfg.costs.fee_rate, cash=cfg.run.starting_capital_tmn, mode="PARTIAL")
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex)
    rec = a.cycle()
    assert rec.status is CycleStatus.PARTIALLY_FILLED
    cid = ex.submits[0]
    half = a.rt.ledger.positions["BTCTMN"].qty
    ex.complete(cid, T + timedelta(seconds=30))  # remainder fills while we are down

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex, resume=True)
    report = b.recover(T + timedelta(minutes=1), balances=ex.balances)
    assert report.outcome is RecoveryOutcome.READY, report.blockers
    assert b.rt.execution.order(cid).state is OrderState.FILLED
    assert b.rt.ledger.positions["BTCTMN"].qty == 2 * half == ex.base["BTC"]
    assert b.rt.ledger.reservations == {} and len(ex.submits) == 1


class CrashOnTerminalState:
    def __init__(self, inner: object) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)

    def state_changed(self, rec: OrderRecord, ev: ExecutionEvent) -> None:
        if ev.state is OrderState.FILLED:
            raise Crash("fill persisted, terminal state not")
        self.inner.state_changed(rec, ev)  # type: ignore[attr-defined]


def test_fill_persisted_immediately_before_crash_is_not_lost_or_doubled(make_cfg: Callable[..., RunConfig],
                                                                       synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    a.rt.execution.recorder = CrashOnTerminalState(a.rt.execution.recorder)  # type: ignore[assignment]
    with pytest.raises(Crash):
        a.cycle()
    qty = a.rt.ledger.positions["BTCTMN"].qty

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", resume=True)
    report = b.recover(T + timedelta(minutes=1))
    assert report.outcome is RecoveryOutcome.READY
    assert b.rt.ledger.positions["BTCTMN"].qty == qty
    assert b.rt.execution.order(report.settled_orders[0]).state is OrderState.FILLED
    assert b.rt.ledger.reservations == {}


# ---------------------------------------------------------------------------------------------- 7 reservations
def test_resting_entry_keeps_reservation_across_restart(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                                        tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    ex = ExchangeLikeVenue(fee_rate=cfg.costs.fee_rate, cash=cfg.run.starting_capital_tmn, mode="REST")
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex)
    assert a.cycle().status is CycleStatus.SUBMITTED
    reserved, spendable = dict(a.rt.ledger.reservations), a.rt.ledger.spendable_tmn

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex, resume=True)
    report = b.recover(T + timedelta(minutes=1), balances=ex.balances)
    assert report.outcome is RecoveryOutcome.READY, report.blockers
    assert b.rt.ledger.reservations == reserved and b.rt.ledger.spendable_tmn == spendable
    assert report.reservations == {k: str(v) for k, v in reserved.items()}


# ---------------------------------------------------------------------------------------------- 8, 9 latches/protections
def test_kill_switch_stays_latched_after_restart(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                                 tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    a.rt.kill.latch("operator test", at=T)
    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", resume=True)
    report = b.recover(T + timedelta(minutes=1))
    assert report.kill_switch_latched and report.kill_reason == "operator test"
    assert b.cycle(T + timedelta(minutes=15)).status is not CycleStatus.FILLED
    assert b.venue.submitted == []  # type: ignore[attr-defined]


def test_loss_streak_cooldown_and_drawdown_pause_survive_restart(make_cfg: Callable[..., RunConfig],
                                                                 synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    for i in range(cfg.limits.risk.loss_streak_count or 0):
        _losing_round_trip(a, i, T + timedelta(minutes=i))
    later = T + timedelta(minutes=10)
    assert len(a.rt.ledger.trades) == cfg.limits.risk.loss_streak_count
    assert a.rt.prot.loss_streak_until(later) is not None
    a.rt.prot.drawdown_pause_until = later + timedelta(minutes=30)
    a.rt.mark_equity(later, record=False)  # risk memory checkpoint

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", resume=True)
    assert b.recover(later).outcome is RecoveryOutcome.READY
    assert b.rt.prot.loss_streak_until(later) == a.rt.prot.loss_streak_until(later)
    assert b.rt.prot.drawdown_pause_until == later + timedelta(minutes=30)
    assert b.rt.ledger.peak_equity_tmn == a.rt.ledger.peak_equity_tmn
    rec = b.cycle(T + timedelta(minutes=15))
    assert rec.status is not CycleStatus.FILLED and b.venue.submitted == []  # type: ignore[attr-defined]


def _losing_round_trip(rig: Rig, i: int, at: datetime) -> None:
    """BUY at the ask, SELL at the bid through the execution engine (journaled), i.e. a small realized loss."""
    book = rig.books["BTCTMN"]
    for side, price, purpose in (("BUY", book.asks[0][0] * Decimal("1.001"), OrderPurpose.ENTRY),
                                 ("SELL", book.bids[0][0] * Decimal("0.999"), OrderPurpose.EXIT_STOP)):
        body = {"intent_id": f"in-{i}-{side}", "decision_id": f"d{i}", "client_order_id": f"wa-{i}-{side}", "symbol": "BTCTMN",
                "side": side, "order_type": "LIMIT", "qty": Decimal("0.002"), "limit_price": price.quantize(Decimal(1)),
                "purpose": purpose, "environment": Environment.SHADOW, "autonomy": AutonomyMode.AUTONOMOUS, "created_at": at}
        intent = OrderIntent(intent_hash=content_id(body), **body)  # type: ignore[arg-type]
        verdict = RiskVerdict(verdict_id="v", intent_hash=intent.intent_hash, outcome=RiskOutcome.APPROVE, approved_qty=intent.qty,
                              checks=(RiskCheck(name="x", passed=True, detail=""),), limits_version="l", evaluated_at=at,
                              expires_at=at + timedelta(seconds=30))
        plan = ExitPlan("c", price * Decimal("0.9"), None, at + timedelta(hours=1)) if side == "BUY" else None
        rig.rt.execution.submit(intent, verdict, now=at, plan=plan, strategy_version="t")


# ---------------------------------------------------------------------------------------------- 10 approvals
def test_outstanding_approval_is_invalidated_on_restart(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                                        tmp_path: Path) -> None:
    cfg = _shadow(make_cfg, **{"run.autonomy": AutonomyMode.APPROVAL_REQUIRED})
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    rec = a.cycle()
    assert rec.approval is not None and a.rt.ledger.reserved_tmn > 0
    a.rt.approvals.decide(rec.approval.ticket_id, approve=True, operator="owner", now=T)  # approved, then crash

    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", resume=True)
    report = b.recover(T + timedelta(seconds=30))
    assert report.outcome is RecoveryOutcome.READY
    assert report.invalidated_approvals == (rec.approval.ticket_id,)
    found = b.store.ticket(rec.approval.ticket_id)
    assert found is not None and found[0].status is ApprovalStatus.EXPIRED and "restart" in (found[0].note or "")
    assert b.rt.ledger.reservations == {}
    assert b.rt.process_approvals(T + timedelta(seconds=31)) == [] and b.venue.submitted == []  # type: ignore[attr-defined]
    with pytest.raises(ApprovalError):
        ApprovalService(b.store, ttl_s=300).decide(rec.approval.ticket_id, approve=True, operator="owner",
                                                   now=T + timedelta(seconds=32))


# ---------------------------------------------------------------------------------------------- 11, 12 fail closed
@pytest.mark.parametrize("change", ["limits", "autonomy", "code"])
def test_incompatible_restart_is_blocked(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset, tmp_path: Path,
                                         change: str) -> None:
    cfg = _shadow(make_cfg)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    _enter(a)
    cfg2, code = cfg, CODE
    if change == "limits":
        cfg2 = _shadow(make_cfg, **{"limits.version": "other-limits"})
    elif change == "autonomy":
        cfg2 = _shadow(make_cfg, **{"run.autonomy": AutonomyMode.ADVISOR})
    else:
        code = CodeVersion(git_commit="b" * 40, dirty=False)
    b = Rig(cfg2, synthetic_dataset, tmp_path / "j.sqlite", resume=True, code=code)
    report = b.recover(T + timedelta(minutes=1))
    assert report.outcome is RecoveryOutcome.BLOCKED
    assert any(x.startswith("INCOMPATIBLE") for x in report.blockers)
    assert b.rt.entry_gate and not b.rt.kill.latched
    assert "RECOVERY_BLOCKED" in [e["kind"] for e in b.store.risk_events("run")]


def test_corrupt_state_log_blocks_and_latches_kill(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                                   tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    _enter(a)
    db = sqlite3.connect(tmp_path / "j.sqlite")
    db.execute("UPDATE state_log SET body = replace(body, '\"BUY\"', '\"SELL\"') WHERE kind = 'ORDER'")
    db.commit()
    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", resume=True)
    report = b.recover(T + timedelta(minutes=1))
    assert report.outcome is RecoveryOutcome.BLOCKED
    assert any(x.startswith("STATE_LOG_CORRUPT") for x in report.blockers)
    assert b.rt.kill.latched and b.rt.ledger.positions == {}


def test_incomplete_persisted_state_blocks(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                           tmp_path: Path) -> None:
    """Execution events exist but the state log is missing (e.g. a run journaled by older code): never guess."""
    cfg = _shadow(make_cfg)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    _enter(a)
    db = sqlite3.connect(tmp_path / "j.sqlite")
    db.execute("DELETE FROM state_log")
    db.commit()
    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", resume=True)
    report = b.recover(T + timedelta(minutes=1))
    assert report.outcome is RecoveryOutcome.BLOCKED and any(x.startswith("NO_STATE_LOG") for x in report.blockers)


def test_sealed_run_is_not_resumed(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    a.store.seal_run("run", T + timedelta(minutes=1))
    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", resume=True)
    report = b.recover(T + timedelta(minutes=2))
    assert report.outcome is RecoveryOutcome.BLOCKED and any(x.startswith("RUN_SEALED") for x in report.blockers)


def test_new_runtime_without_resume_still_refuses_existing_run(make_cfg: Callable[..., RunConfig],
                                                               synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    with pytest.raises(RecoveryRequired):
        Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite")
    with pytest.raises(RecoveryRequired):
        Rig(cfg, synthetic_dataset, tmp_path / "other.sqlite", resume=True)


def test_balance_mismatch_beyond_tolerance_blocks_and_latches(make_cfg: Callable[..., RunConfig],
                                                              synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    ex = ExchangeLikeVenue(fee_rate=cfg.costs.fee_rate, cash=cfg.run.starting_capital_tmn)
    a = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex)
    _enter(a)
    ex.base["BTC"] -= Decimal("0.001")  # someone sold outside the system
    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex, resume=True)
    report = b.recover(T + timedelta(minutes=1), balances=ex.balances)
    assert report.outcome is RecoveryOutcome.BLOCKED and any(x.startswith("BALANCE_MISMATCH") for x in report.blockers)
    assert b.rt.kill.latched


def test_exchange_recovery_without_balance_source_blocks_entries(make_cfg: Callable[..., RunConfig],
                                                                 synthetic_dataset: Dataset, tmp_path: Path) -> None:
    cfg = _shadow(make_cfg)
    ex = ExchangeLikeVenue(fee_rate=cfg.costs.fee_rate, cash=cfg.run.starting_capital_tmn)
    Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex)
    b = Rig(cfg, synthetic_dataset, tmp_path / "j.sqlite", ex, resume=True)
    report = b.recover(T + timedelta(minutes=1))
    assert report.outcome is RecoveryOutcome.EXITS_ONLY
    assert "BALANCE_RECONCILIATION_UNAVAILABLE" in b.rt.entry_gate


# ---------------------------------------------------------------------------------------------- BACKTEST determinism
def test_backtest_state_log_replays_to_the_final_ledger(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                                        tmp_path: Path) -> None:
    from wallex_agent.recovery.coordinator import inspect_run
    from wallex_agent.runtime.backtest import run_backtest

    cfg = make_cfg()
    store = JournalStore(tmp_path / "bt.sqlite")
    res = run_backtest(cfg, synthetic_dataset, store, BaselineSelector(), run_id="bt", code=CODE)
    assert res.runtime.ledger.trades, "fixture should trade"
    ledger, rebuilt = inspect_run(store, "bt", cfg)
    assert ledger_digest(ledger) == ledger_digest(res.runtime.ledger)
    assert len(rebuilt.orders) == len(res.runtime.execution.orders())
