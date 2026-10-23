from datetime import UTC, datetime, timedelta
from decimal import Decimal

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
from wallex_agent.execution.engine import DuplicateSubmit, ExecutionEngine, VerdictMismatch
from wallex_agent.execution.sim_backtest import BacktestVenue
from wallex_agent.execution.sim_shadow import ShadowVenue
from wallex_agent.execution.venue import VenueFill
from wallex_agent.market.bars import Bar
from wallex_agent.market.book import OrderBook
from wallex_agent.portfolio.ledger import ExitPlan, Ledger
from wallex_agent.risk.kill_switch import KillSwitch
from wallex_agent.risk.protections import Protections
from wallex_agent.runtime.config import RiskLimits

T = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
FEE = Decimal("0.0025")
META = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=6, min_qty=Decimal("0.000001"),
                  min_notional=Decimal(50000), market_order_enabled=True, metadata_version="t")
LIM = RiskLimits(loss_streak_count=3, loss_streak_window_s=3600, cooldown_s=600, drawdown_pause_fraction=Decimal("0.02"))


def _intent(side: str = "BUY", qty: str = "0.01", limit: str = "100200000", purpose: OrderPurpose = OrderPurpose.ENTRY,
            env: Environment = Environment.BACKTEST, cid: str = "wa-1") -> OrderIntent:
    body = {"intent_id": f"in-{cid}", "decision_id": "d1", "client_order_id": cid, "symbol": "BTCTMN", "side": side,
            "order_type": "LIMIT", "qty": Decimal(qty), "limit_price": Decimal(limit), "purpose": purpose, "environment": env,
            "autonomy": AutonomyMode.AUTONOMOUS, "created_at": T}
    return OrderIntent(intent_hash=content_id(body), **body)  # type: ignore[arg-type]


def _verdict(intent: OrderIntent, *, qty: Decimal | None = None, ttl: int = 30, at: datetime = T) -> RiskVerdict:
    return RiskVerdict(verdict_id="v", intent_hash=intent.intent_hash, outcome=RiskOutcome.APPROVE, approved_qty=qty or intent.qty,
                       checks=(RiskCheck(name="x", passed=True, detail=""),), limits_version="l", evaluated_at=at,
                       expires_at=at + timedelta(seconds=ttl))


def _bar(minute: int, o: int, h: int, lo: int, c: int) -> Bar:
    return Bar(T + timedelta(minutes=minute), Decimal(o), Decimal(h), Decimal(lo), Decimal(c), Decimal(1))


def _engine(venue: BacktestVenue | ShadowVenue, cash: int = 5_000_000) -> tuple[ExecutionEngine, Ledger]:
    led = Ledger(Decimal(cash))
    eng = ExecutionEngine(venue, led, Protections(LIM), KillSwitch(), metas={"BTCTMN": META}, max_consecutive_errors=3)
    return eng, led


def _plan() -> ExitPlan:
    return ExitPlan("c1", Decimal("99000000"), Decimal("102000000"), T + timedelta(hours=1))


def test_backtest_entry_fills_next_bar_open_not_signal_close() -> None:
    venue = BacktestVenue(fee_rate=FEE, slippage_bps=Decimal(5), entry_ttl_s=900)
    eng, led = _engine(venue)
    intent = _intent()
    eng.submit(intent, _verdict(intent), now=T, plan=_plan())
    assert "BTCTMN" not in led.positions  # nothing fills at decision time
    venue.on_bar("BTCTMN", _bar(15, 100_100_000, 100_500_000, 100_000_000, 100_300_000))
    eng.poll(now=T + timedelta(minutes=15))
    pos = led.positions["BTCTMN"]
    assert pos.qty == Decimal("0.01")
    expected_px = Decimal(100_100_000) * (1 + Decimal(5) / 10000)
    assert led.cash_tmn == Decimal(5_000_000) - Decimal("0.01") * expected_px * (1 + FEE)
    assert eng.order("wa-1").state is OrderState.FILLED


def test_entry_limit_not_reached_expires() -> None:
    venue = BacktestVenue(fee_rate=FEE, slippage_bps=Decimal(5), entry_ttl_s=900)
    eng, led = _engine(venue)
    intent = _intent(limit="100000000")
    eng.submit(intent, _verdict(intent), now=T, plan=_plan())
    venue.on_bar("BTCTMN", _bar(15, 100_500_000, 100_900_000, 100_100_000, 100_600_000))
    eng.poll(now=T + timedelta(minutes=15))
    venue.on_bar("BTCTMN", _bar(30, 100_600_000, 100_900_000, 100_200_000, 100_600_000))
    eng.poll(now=T + timedelta(minutes=30))
    assert eng.order("wa-1").state is OrderState.EXPIRED and "BTCTMN" not in led.positions


def test_submit_refuses_mismatched_hash_expired_verdict_qty_and_environment() -> None:
    venue = BacktestVenue(fee_rate=FEE, slippage_bps=Decimal(5), entry_ttl_s=900)
    eng, _ = _engine(venue)
    a, b = _intent(cid="a"), _intent(cid="b")
    with pytest.raises(VerdictMismatch):
        eng.submit(a, _verdict(b), now=T, plan=None)
    with pytest.raises(VerdictMismatch):
        eng.submit(a, _verdict(a), now=T + timedelta(seconds=31), plan=None)
    with pytest.raises(VerdictMismatch):
        eng.submit(a, _verdict(a, qty=Decimal("0.001")), now=T, plan=None)
    live = _intent(cid="c", env=Environment.LIVE)
    with pytest.raises(VerdictMismatch):
        eng.submit(live, _verdict(live), now=T, plan=None)
    assert venue.submitted == []


@pytest.mark.parametrize("change", [{"qty": Decimal("0.001")}, {"limit_price": Decimal("200000000")}, {"symbol": "ETHTMN"}])
def test_submit_recomputes_intent_hash_before_sending(change: dict[str, object]) -> None:
    venue = ShadowVenue(fee_rate=FEE, book=lambda s: _book("100000000", "100100000"))
    eng, led = _engine(venue)
    original = _intent(env=Environment.SHADOW)
    altered = original.model_copy(update=change)
    with pytest.raises(VerdictMismatch):
        eng.submit(altered, _verdict(original), now=T, plan=None)
    assert venue.submitted == [] and led.positions == {}


def test_submit_at_verdict_expiry_is_refused() -> None:
    venue = ShadowVenue(fee_rate=FEE, book=lambda s: _book("100000000", "100100000"))
    eng, _ = _engine(venue)
    i = _intent(env=Environment.SHADOW)
    with pytest.raises(VerdictMismatch):
        eng.submit(i, _verdict(i), now=T + timedelta(seconds=30), plan=None)
    assert venue.submitted == []


def test_exit_accounts_all_depth_fills_before_classifying_dust() -> None:
    from wallex_agent.portfolio.ledger import Fill

    book = OrderBook(bids=((Decimal(100000000), Decimal("0.0097")), (Decimal(99950000), Decimal("0.0003"))),
                     asks=((Decimal(100100000), Decimal(1)),), received_at=T)
    venue = ShadowVenue(fee_rate=Decimal(0), book=lambda s: book)
    eng, led = _engine(venue)
    led.apply_fill(Fill("BTCTMN", "BUY", Decimal("0.01"), Decimal(100000000), Decimal(0), "TMN", T,
                        "entry", "ENTRY", "d1", "v"), plan=_plan())
    i = _intent(side="SELL", purpose=OrderPurpose.EXIT_STOP, limit="99000000", env=Environment.SHADOW)
    eng.submit(i, _verdict(i), now=T, plan=None)
    assert eng.order(i.client_order_id).filled_qty == Decimal("0.01")
    assert led.positions == {} and led.dust == {}
    assert led.cash_tmn == Decimal(4999985)
    assert led.realized_pnl_tmn == Decimal(-15)
    assert led.trades[0].net_pnl_tmn == Decimal(-15)


def test_canceled_order_late_fill_is_reconciled_exactly_once() -> None:
    venue = ShadowVenue(fee_rate=Decimal(0), book=lambda s: _book("100000000", "100100000", ask_qty="0.005"))
    eng, led = _engine(venue)
    i = _intent(env=Environment.SHADOW)
    eng.submit(i, _verdict(i), now=T, plan=_plan())
    order = venue.query(i.client_order_id)
    assert order is not None and order.state is OrderState.CANCELED
    order.fills.append(VenueFill("late", Decimal("0.002"), Decimal(100100000), Decimal(0), "TMN", T + timedelta(seconds=1)))
    eng.poll(now=T + timedelta(seconds=2))
    eng.poll(now=T + timedelta(seconds=3))
    assert led.positions["BTCTMN"].qty == Decimal("0.007")
    assert led.cash_tmn == Decimal(4299300)
    assert len(venue.submitted) == 1


def test_unchanged_canceled_observation_does_not_duplicate_events() -> None:
    venue = ShadowVenue(fee_rate=Decimal(0), book=lambda s: _book("100000000", "100100000", ask_qty="0.005"))
    eng, _ = _engine(venue)
    i = _intent(env=Environment.SHADOW)
    eng.submit(i, _verdict(i), now=T, plan=_plan())
    assert eng.poll(now=T + timedelta(seconds=1)) == []


def test_execution_event_contains_actual_cumulative_price_and_fees() -> None:
    venue = ShadowVenue(fee_rate=FEE, book=lambda s: _book("100000000", "100100000"))
    eng, _ = _engine(venue)
    i = _intent(env=Environment.SHADOW)
    final = eng.submit(i, _verdict(i), now=T, plan=_plan())[-1]
    assert final.avg_fill_price == Decimal(100100000)
    assert final.fee == Decimal("2502.5")
    assert final.fee_asset == "TMN"


def test_duplicate_submit_refused() -> None:
    venue = BacktestVenue(fee_rate=FEE, slippage_bps=Decimal(5), entry_ttl_s=900)
    eng, _ = _engine(venue)
    i = _intent()
    eng.submit(i, _verdict(i), now=T, plan=None)
    with pytest.raises(DuplicateSubmit):
        eng.submit(i, _verdict(i), now=T, plan=None)
    assert len(venue.submitted) == 1


def _book(bid: str, ask: str, ask_qty: str = "1", bid_qty: str = "1") -> OrderBook:
    return OrderBook(bids=((Decimal(bid), Decimal(bid_qty)),), asks=((Decimal(ask), Decimal(ask_qty)),), received_at=T)


def test_lost_response_goes_unknown_then_reconciles_without_resubmit() -> None:
    venue = ShadowVenue(fee_rate=FEE, book=lambda s: _book("100000000", "100100000"))
    venue.drop_next_response = True
    eng, led = _engine(venue)
    i = _intent(env=Environment.SHADOW)
    events = eng.submit(i, _verdict(i), now=T, plan=_plan())
    assert any(e.state is OrderState.UNKNOWN for e in events)
    assert eng.order("wa-1").state is OrderState.FILLED  # resolved by client_order_id query
    assert len(venue.submitted) == 1 and led.positions["BTCTMN"].qty == Decimal("0.01")


def test_unresolvable_unknown_stays_unknown_and_never_resubmits() -> None:
    venue = ShadowVenue(fee_rate=FEE, book=lambda s: _book("100000000", "100100000"))
    venue.drop_next_response = True
    venue.forget_orders = True
    eng, _ = _engine(venue)
    i = _intent(env=Environment.SHADOW)
    eng.submit(i, _verdict(i), now=T, plan=None)
    eng.poll(now=T + timedelta(seconds=5))
    assert eng.order("wa-1").state is OrderState.UNKNOWN and len(venue.submitted) == 1
    assert eng.unresolved() == ["wa-1"]


def test_partial_fill_creates_protected_position_immediately() -> None:
    venue = ShadowVenue(fee_rate=FEE, book=lambda s: _book("100000000", "100100000", ask_qty="0.004"))
    eng, led = _engine(venue)
    i = _intent(env=Environment.SHADOW)
    eng.submit(i, _verdict(i), now=T, plan=_plan())
    order = eng.order("wa-1")
    assert order.filled_qty == Decimal("0.004") and order.state is OrderState.CANCELED  # IOC remainder cancelled
    pos = led.positions["BTCTMN"]
    assert pos.qty == Decimal("0.004") and pos.plan is not None and pos.plan.stop_price == Decimal("99000000")
    assert led.reservations == {}  # terminal entry releases its reservation


def test_stop_and_tp_in_same_bar_takes_stop() -> None:
    venue = BacktestVenue(fee_rate=FEE, slippage_bps=Decimal(5), entry_ttl_s=900)
    venue.on_bar("BTCTMN", _bar(15, 100_000_000, 103_000_000, 98_000_000, 101_000_000))
    from wallex_agent.execution.exits import pick_bar_exit

    trig = pick_bar_exit(_plan(), _bar(15, 100_000_000, 103_000_000, 98_000_000, 101_000_000), now=T + timedelta(minutes=30))
    assert trig is not None and trig[0] is OrderPurpose.EXIT_STOP and trig[1] == Decimal("99000000")


def test_backtest_stop_fills_at_stop_or_gap_open() -> None:
    venue = BacktestVenue(fee_rate=FEE, slippage_bps=Decimal(5), entry_ttl_s=900)
    eng, led = _engine(venue)
    led.apply_fill(__import__("wallex_agent.portfolio.ledger", fromlist=["Fill"]).Fill(
        "BTCTMN", "BUY", Decimal("0.01"), Decimal(100_000_000), Decimal(0), "TMN", T, "x", "ENTRY", "d1", "v"), plan=_plan())
    venue.on_bar("BTCTMN", _bar(15, 98_500_000, 99_000_000, 98_000_000, 98_200_000))  # gapped below the stop
    s = _intent(side="SELL", limit="99000000", purpose=OrderPurpose.EXIT_STOP, cid="s1")
    eng.submit(s, _verdict(s, at=T + timedelta(minutes=15)), now=T + timedelta(minutes=15), plan=None)
    assert "BTCTMN" not in led.positions
    trade = led.trades[0]
    assert trade.avg_exit_price == Decimal(98_500_000) * (1 - Decimal(5) / 10000)
    assert trade.exit_reason == "EXIT_STOP" and trade.net_pnl_tmn < 0


def test_no_double_sell_while_exit_pending() -> None:
    venue = BacktestVenue(fee_rate=FEE, slippage_bps=Decimal(5), entry_ttl_s=900)
    eng, led = _engine(venue)
    led.apply_fill(__import__("wallex_agent.portfolio.ledger", fromlist=["Fill"]).Fill(
        "BTCTMN", "BUY", Decimal("0.01"), Decimal(100_000_000), Decimal(0), "TMN", T, "x", "ENTRY", "d1", "v"), plan=_plan())
    s = _intent(side="SELL", limit="99700000", purpose=OrderPurpose.EXIT_TIME, cid="t1")
    eng.submit(s, _verdict(s), now=T, plan=None)
    assert led.positions["BTCTMN"].exit_order_id == "t1"  # coordinator/risk see the pending exit


def test_consecutive_execution_errors_latch_kill() -> None:
    venue = ShadowVenue(fee_rate=FEE, book=lambda s: _book("100000000", "100100000"))
    venue.fail_submit = True
    eng, _ = _engine(venue)
    for k in range(3):
        i = _intent(cid=f"e{k}", env=Environment.SHADOW)
        eng.submit(i, _verdict(i), now=T, plan=None)
    assert eng.kill.latched
