from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, current_thread

import pytest

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.agent.context import DecisionContext
from wallex_agent.contracts import ApprovalStatus, AutonomyMode, CodeVersion, CycleStatus, DecisionProvenance, Environment, ValidatedDecision
from wallex_agent.execution.sim_shadow import ShadowVenue
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import aggregate
from wallex_agent.market.book import OrderBook
from wallex_agent.market.dataset import Dataset
from wallex_agent.portfolio.ledger import ExitPlan, Fill
from wallex_agent.runtime.backtest import universe_from_metadata
from wallex_agent.runtime.config import RunConfig
from wallex_agent.runtime.engine import TradingRuntime

T = datetime(2026, 10, 3, 6, 15, tzinfo=UTC)


def _runtime(cfg: RunConfig, ds: Dataset, store: JournalStore, run_id: str) -> TradingRuntime:
    metas, eligible, universe = universe_from_metadata(ds.path / "metadata")
    books: dict[str, OrderBook] = {}
    venue = ShadowVenue(fee_rate=cfg.costs.fee_rate, book=books.get)
    rt = TradingRuntime(cfg, store=store, venue=venue, decider=BaselineSelector(), metas=metas, eligible=eligible,
                        universe_version=universe, run_id=run_id, code=CodeVersion(git_commit="a" * 40, dirty=False),
                        dataset_id=ds.name, data_source="FIXTURE")
    rt.start(T - timedelta(minutes=1), dependency_lock_sha256="a" * 64)
    for symbol in rt.symbols:
        closed = [b for b in ds.bars[symbol] if b.ts_open + timedelta(minutes=1) <= T]
        price = closed[-1].close
        book = OrderBook(bids=((price * Decimal("0.9997"), Decimal(1)),),
                         asks=((price * Decimal("1.0003"), Decimal(1)),), received_at=T)
        books[symbol] = book
        rt.update_market(symbol, mark=price, book=book)
    return rt


def _cycle(rt: TradingRuntime, ds: Dataset):  # type: ignore[no-untyped-def]
    bars = [b for b in aggregate(ds.bars["BTCTMN"], 900) if b.ts_open + timedelta(seconds=900) <= T]
    return rt.cycle("BTCTMN", T, bars[-rt.history_window:], flags=frozenset())


def test_foreign_runtime_never_consumes_an_approved_ticket(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                                         tmp_path: Path) -> None:
    cfg = make_cfg(**{"run.environment": Environment.SHADOW, "run.autonomy": AutonomyMode.APPROVAL_REQUIRED})
    store = JournalStore(tmp_path / "j.sqlite")
    owner = _runtime(cfg, synthetic_dataset, store, "owner")
    rec = _cycle(owner, synthetic_dataset)
    assert rec.approval is not None and rec.status is CycleStatus.AWAITING_APPROVAL
    owner.approvals.decide(rec.approval.ticket_id, approve=True, operator="owner", now=T)
    other = _runtime(cfg, synthetic_dataset, store, "other")
    assert other.process_approvals(T + timedelta(seconds=1)) == []
    ticket = store.ticket(rec.approval.ticket_id)
    assert ticket is not None and ticket[0].status is ApprovalStatus.APPROVED
    assert owner.ledger.reserved_tmn > 0 and other.ledger.reserved_tmn == 0
    assert other.execution.orders() == []
    assert owner.process_approvals(T + timedelta(seconds=1)) == [rec.approval.ticket_id]


def test_reusing_run_id_refuses_unsupported_recovery(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                                     tmp_path: Path) -> None:
    cfg = make_cfg(**{"run.environment": Environment.SHADOW})
    store = JournalStore(tmp_path / "j.sqlite")
    _runtime(cfg, synthetic_dataset, store, "same-run")
    metas, eligible, universe = universe_from_metadata(synthetic_dataset.path / "metadata")
    with pytest.raises(RuntimeError, match="recovery"):
        TradingRuntime(cfg, store=store, venue=ShadowVenue(fee_rate=Decimal(0), book=lambda s: None), decider=BaselineSelector(),
                       metas=metas, eligible=eligible, universe_version=universe, run_id="same-run",
                       code=CodeVersion(git_commit="a" * 40, dirty=False), dataset_id="d", data_source="FIXTURE")


class DelayedDecision:
    source_name = "BASELINE"

    def __init__(self, advance: Callable[[], None]) -> None:
        self.advance = advance

    def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
        result = BaselineSelector().decide(ctx)
        self.advance()
        return result


@pytest.mark.parametrize("elapsed,cutoff,expected", [(40, True, CycleStatus.BLOCKED),
                                                   (40, False, CycleStatus.RISK_REJECTED),
                                                   (70, False, CycleStatus.WAITED)])
def test_decision_completion_uses_host_time(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                           tmp_path: Path, elapsed: int, cutoff: bool, expected: CycleStatus) -> None:
    cfg = make_cfg(**{"run.environment": Environment.SHADOW, "limits.cutoff.no_new_entry_before_end_s": 0,
                      "limits.cutoff.liquidation_start_before_end_s": 0,
                      "competition.end_utc": T + timedelta(seconds=30) if cutoff else None})
    store = JournalStore(tmp_path / "j.sqlite")
    rt = _runtime(cfg, synthetic_dataset, store, "clock")
    wall = {"now": T}
    rt.clock = lambda: wall["now"]

    def advance() -> None:
        wall["now"] += timedelta(seconds=elapsed)

    rt.decider = DelayedDecision(advance)
    rec = _cycle(rt, synthetic_dataset)
    assert rec.status is expected
    assert rec.recorded_at == T + timedelta(seconds=elapsed)
    assert rt.execution.orders() == []
    if cutoff:
        assert rec.block_reason == "COMPETITION_ENDED"
    elif elapsed == 40:
        assert rec.risk_verdict is not None
        assert any(c.name == "book_fresh" and not c.passed for c in rec.risk_verdict.checks)
    else:
        assert rec.validated is not None and rec.validated.status.value == "STALE"


def test_waiting_for_model_services_exits_on_the_kernel_thread(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                                              tmp_path: Path) -> None:
    from wallex_agent.runtime.decision_wait import ResponsiveDecisionProvider

    rt = _runtime(make_cfg(**{"run.environment": Environment.SHADOW}), synthetic_dataset,
                  JournalStore(tmp_path / "j.sqlite"), "wait")
    book = rt.symbols["BTCTMN"].book
    assert book is not None
    price = book.bids[0][0]
    rt.ledger.apply_fill(Fill("BTCTMN", "BUY", Decimal("0.005"), price * Decimal("1.1"), Decimal(0), "TMN",
                             T - timedelta(minutes=1), "seed", "ENTRY", "seed", "test"),
                         plan=ExitPlan("seed", price * Decimal("1.01"), None, T + timedelta(hours=1)))
    released = Event()
    main = current_thread()

    class BlockingDecision:
        source_name = "BASELINE"

        def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
            assert released.wait(2), "exit watcher was blocked by model wait"
            assert len(rt.ledger.trades) == 1
            return BaselineSelector().decide(ctx)

    def maintain() -> None:
        assert current_thread() is main  # ledger/risk/execution remain on one thread
        rt.poll(T)
        actions = rt.run_exits(T, {"BTCTMN": book})
        assert actions and actions[0].outcome == "EXECUTED"
        assert rt.ledger.trades[0].exit_reason == "EXIT_STOP"
        released.set()

    waiter = ResponsiveDecisionProvider(BlockingDecision(), maintain=maintain, clock=lambda: T, interval_s=0.001)
    rt.decider = waiter
    try:
        _cycle(rt, synthetic_dataset)
        assert released.is_set()
    finally:
        released.set()
        waiter.close()


def test_unresponsive_provider_becomes_wait_without_new_orders(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                                             tmp_path: Path) -> None:
    from wallex_agent.runtime.decision_wait import ResponsiveDecisionProvider

    rt = _runtime(make_cfg(**{"run.environment": Environment.SHADOW, "agent.decision_deadline_s": 1}), synthetic_dataset,
                  JournalStore(tmp_path / "j.sqlite"), "timeout")
    released = Event()

    class BlockingDecision:
        source_name = "BASELINE"

        def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
            released.wait(2)
            return BaselineSelector().decide(ctx)

    waiter = ResponsiveDecisionProvider(BlockingDecision(), maintain=lambda: None, clock=lambda: T, interval_s=0.01)
    rt.decider = waiter
    try:
        rec = _cycle(rt, synthetic_dataset)
        assert rec.status is CycleStatus.WAITED and rec.validated is not None
        assert rec.validated.status.value == "TIMEOUT"
        assert rt.execution.orders() == []
    finally:
        released.set()
        waiter.close()


class UnsupportedVeto:
    """VETO-role model that declines the deterministic candidate without a permitted reason."""

    source_name = "BASELINE"

    def __init__(self, advance: Callable[[], None]) -> None:
        self.advance = advance

    def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
        from wallex_agent.agent.validator import validate
        from wallex_agent.contracts import AgentAction, ReasonCode

        base, prov = BaselineSelector().decide(ctx)
        assert base.decision is not None
        wait = base.decision.model_copy(update={"action": AgentAction.WAIT, "selected_candidate_id": None,
                                                "reason_codes": (ReasonCode.WEAK_EVIDENCE,)})
        self.advance()
        return validate(wait, ctx, received_at=ctx.now, exit_enabled=False, role="VETO"), prov


@pytest.mark.parametrize("elapsed", [0, 70])
def test_overruled_veto_is_not_a_model_failure_and_still_expires(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset,
                                                                 tmp_path: Path, elapsed: int) -> None:
    cfg = make_cfg(**{"run.environment": Environment.SHADOW, "agent.role": "VETO", "agent.prompt_version": "veto_v1"})
    store = JournalStore(tmp_path / "j.sqlite")
    rt = _runtime(cfg, synthetic_dataset, store, "veto")
    wall = {"now": T}
    rt.clock = lambda: wall["now"]

    def advance() -> None:
        wall["now"] += timedelta(seconds=elapsed)

    rt.decider = UnsupportedVeto(advance)
    rec = _cycle(rt, synthetic_dataset)
    kinds = [e["kind"] for e in store.risk_events("veto")]
    assert rec.validated is not None
    if elapsed == 0:
        assert rec.validated.status.value == "VETO_UNSUPPORTED" and rec.effective_action.value == "ENTER"
        assert rec.status is not CycleStatus.WAITED and "AI_VETO_UNSUPPORTED" in kinds and "AI_DECISION_INVALID" not in kinds
    else:  # answered after the decision deadline: the overruled veto must not turn into a late entry
        assert rec.validated.status.value == "STALE" and rec.status is CycleStatus.WAITED and rt.execution.orders() == []
