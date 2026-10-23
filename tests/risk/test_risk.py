from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from wallex_agent.contracts import (
    AutonomyMode,
    BookTop,
    DataRef,
    Environment,
    MarketMeta,
    MarketSnapshot,
    OrderPurpose,
    QualityFlag,
    RiskOutcome,
    RiskPosture,
    RiskVerdict,
    SignalCandidate,
)
from wallex_agent.market.book import OrderBook
from wallex_agent.portfolio.ledger import Fill, Ledger
from wallex_agent.risk.engine import IntentDraft, RiskContext, RiskEngine
from wallex_agent.risk.kill_switch import KillSwitch
from wallex_agent.risk.protections import Protections
from wallex_agent.runtime.calendar import WindowStatus
from wallex_agent.runtime.config import (
    CostModel,
    CutoffLimits,
    DataLimits,
    ExecutionSettings,
    KillLimits,
    Limits,
    RiskLimits,
)

T = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
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


def _snapshot(flags: frozenset[QualityFlag] = frozenset(), book: BookTop | None = None) -> MarketSnapshot:
    ref = DataRef(dataset_id="d", content_sha256="0" * 64, source="FIXTURE", symbol="BTCTMN", first_ts=T, last_ts=T,
                  resolution_seconds=900)
    return MarketSnapshot(snapshot_id="snap", symbol="BTCTMN", as_of=T, bar_interval_seconds=900,
                          last_closed_bar_open=T - timedelta(seconds=900), candles=ref, book=book, meta=META,
                          universe_version="u", quality_flags=flags)


def _cand(ref: str = "100000000", stop: str = "99000000") -> SignalCandidate:
    return SignalCandidate(candidate_id="c1", snapshot_id="snap", strategy_id="C1_breakout", strategy_version="v",
                           symbol="BTCTMN", reference_price=Decimal(ref), stop_price=Decimal(stop),
                           take_profit_price=Decimal(ref) * 2, max_holding_seconds=3600, exit_policy_id="tb",
                           estimated_round_trip_cost_bps=Decimal(60), evidence_feature_ids=(), created_at=T,
                           expires_at=T + timedelta(minutes=10))


class Rig:
    def __init__(self, cash: int = 5_000_000) -> None:
        self.ledger = Ledger(Decimal(cash))
        self.kill = KillSwitch()
        self.prot = Protections(LIMITS.risk)
        self.engine = RiskEngine(LIMITS, COSTS, EXE, self.ledger, self.kill, self.prot, starting_capital_tmn=Decimal(cash))

    def ctx(self, **kw: object) -> RiskContext:
        base: dict[str, object] = {"now": T, "snapshot": _snapshot(), "book": None, "marks": {}, "eligible": frozenset({"BTCTMN"}),
                                   "window": OPEN, "require_book": False, "environment": Environment.BACKTEST}
        base.update(kw)
        return RiskContext(**base)  # type: ignore[arg-type]

    def draft(self, cand: SignalCandidate | None = None, posture: RiskPosture = RiskPosture.NORMAL, decision: str = "d1") -> IntentDraft:
        return IntentDraft(decision_id=decision, candidate=cand or _cand(), autonomy=AutonomyMode.AUTONOMOUS,
                           environment=Environment.BACKTEST, posture=posture)


def _failed(verdict: RiskVerdict) -> set[str]:
    return {c.name for c in verdict.checks if not c.passed}


def test_size_from_risk_budget_rounds_down_and_respects_caps() -> None:
    rig = Rig()
    verdict, intent = rig.engine.evaluate_entry(rig.draft(), rig.ctx())
    assert verdict.outcome is RiskOutcome.APPROVE and intent is not None
    entry = Decimal("100000000") * (1 + Decimal(20) / 10000)
    unit_loss = (entry - Decimal("99000000")) + entry * Decimal("0.0025") + Decimal("99000000") * Decimal("0.0025") + entry * Decimal(10) / 10000
    expected = (Decimal(25000) / unit_loss).quantize(Decimal("0.000001"), rounding="ROUND_DOWN")
    assert intent.qty == expected and intent.qty * entry <= Decimal(1_500_000)
    assert intent.limit_price == entry and intent.side == "BUY" and intent.purpose is OrderPurpose.ENTRY
    assert verdict.intent_hash == intent.intent_hash and verdict.approved_qty == intent.qty


def test_position_cap_reduces() -> None:
    rig = Rig()
    verdict, intent = rig.engine.evaluate_entry(rig.draft(_cand(stop="99900000")), rig.ctx())  # tight stop -> big qty
    assert verdict.outcome is RiskOutcome.REDUCE and intent is not None
    assert intent.qty * intent.limit_price <= Decimal(1_500_000)  # type: ignore[operator]


def test_cautious_never_increases_size() -> None:
    rig = Rig()
    _, normal = rig.engine.evaluate_entry(rig.draft(), rig.ctx())
    rig2 = Rig()
    _, cautious = rig2.engine.evaluate_entry(rig2.draft(posture=RiskPosture.CAUTIOUS), rig2.ctx())
    assert normal is not None and cautious is not None and cautious.qty <= normal.qty / 2 + Decimal("0.000001")


def test_reject_unknown_symbol_kill_and_window() -> None:
    rig = Rig()
    v, i = rig.engine.evaluate_entry(rig.draft(), rig.ctx(eligible=frozenset[str]()))
    assert v.outcome is RiskOutcome.REJECT and i is None and "symbol_eligible" in _failed(v)
    rig.kill.latch("test", at=T)
    v, _ = rig.engine.evaluate_entry(rig.draft(), rig.ctx())
    assert "kill_switch" in _failed(v)
    rig2 = Rig()
    closed = WindowStatus(False, False, False, False, "OUTSIDE_TRADING_WINDOW", None, T + timedelta(hours=9), "ACTIVE", "tw")
    v, _ = rig2.engine.evaluate_entry(rig2.draft(), rig2.ctx(window=closed))
    assert v.outcome is RiskOutcome.REJECT and "trading_window" in _failed(v)


def test_reject_stale_data_and_missing_book_when_required() -> None:
    rig = Rig()
    v, _ = rig.engine.evaluate_entry(rig.draft(), rig.ctx(snapshot=_snapshot(frozenset({QualityFlag.GAP}))))
    assert "data_quality" in _failed(v)
    v, _ = rig.engine.evaluate_entry(rig.draft(), rig.ctx(now=T + timedelta(hours=1)))
    assert "data_quality" in _failed(v) or "candidate_fresh" in _failed(v)
    v, _ = rig.engine.evaluate_entry(rig.draft(), rig.ctx(require_book=True))
    assert "book_fresh" in _failed(v)


def test_wide_spread_rejected() -> None:
    rig = Rig()
    book = OrderBook(bids=((Decimal("99000000"), Decimal(1)),), asks=((Decimal("100000000"), Decimal(1)),), received_at=T)
    v, _ = rig.engine.evaluate_entry(rig.draft(), rig.ctx(book=book, require_book=True))
    assert "spread" in _failed(v)


def test_reservations_prevent_double_spend_same_tick() -> None:
    rig = Rig(cash=600_000)
    c2 = SignalCandidate.model_validate({**_cand().model_dump(), "candidate_id": "c2", "symbol": "ETHTMN"})
    meta_ctx = rig.ctx(eligible=frozenset({"BTCTMN", "ETHTMN"}))
    v1, i1 = rig.engine.evaluate_entry(rig.draft(), meta_ctx)
    _, i2 = rig.engine.evaluate_entry(rig.draft(c2, decision="d2"), replace(meta_ctx, snapshot=meta_ctx.snapshot.model_copy(update={"symbol": "ETHTMN", "meta": META.model_copy(update={"symbol": "ETHTMN"})})))
    assert i1 is not None and v1.reservation_id is not None
    spent = i1.qty * i1.limit_price * Decimal("1.0025")  # type: ignore[operator]
    if i2 is not None:
        spent += i2.qty * i2.limit_price * Decimal("1.0025")  # type: ignore[operator]
    assert spent <= Decimal(600_000)
    assert rig.ledger.spendable_tmn >= 0


def test_one_position_per_symbol() -> None:
    rig = Rig()
    _, intent = rig.engine.evaluate_entry(rig.draft(), rig.ctx())
    assert intent is not None
    v, _ = rig.engine.evaluate_entry(rig.draft(decision="d2"), rig.ctx())
    assert "one_position_per_symbol" in _failed(v)


def test_drawdown_uses_open_position_marks_and_emergency_latches_kill() -> None:
    rig = Rig()
    rig.ledger.apply_fill(Fill("ETHTMN", "BUY", Decimal(1), Decimal(1_000_000), Decimal(0), "TMN", T, "x", "ENTRY", "d0", "v"), plan=None)
    marks = {"ETHTMN": Decimal(870_000)}  # unrealized -130k -> 2.6% drawdown (> pause 2%)
    v, _ = rig.engine.evaluate_entry(rig.draft(), rig.ctx(marks=marks))
    assert "drawdown_pause" in _failed(v) and not rig.kill.latched
    v, _ = rig.engine.evaluate_entry(rig.draft(), rig.ctx(marks={"ETHTMN": Decimal(790_000)}))  # 4.2%
    assert "drawdown_emergency" in _failed(v) and rig.kill.latched


def test_drawdown_pause_lifts_after_cooldown_and_retriggers_only_on_deeper_low() -> None:
    prot = Protections(LIMITS.risk)  # pause 2%, cooldown 3600 s
    assert prot.drawdown_paused(T, Decimal("0.025"))
    assert prot.drawdown_paused(T + timedelta(minutes=30), Decimal("0.025"))
    assert not prot.drawdown_paused(T + timedelta(hours=1, seconds=1), Decimal("0.025"))  # same depth after cooldown
    assert not prot.drawdown_paused(T + timedelta(hours=2), Decimal("0.022"))  # recovered a bit
    assert prot.drawdown_paused(T + timedelta(hours=3), Decimal("0.03"))  # new deeper low -> pause again


def test_loss_streak_cooldown_blocks_entries() -> None:
    rig = Rig()
    for k in range(3):
        rig.prot.record_exit(Decimal(-1000), T - timedelta(minutes=30 - k))
    v, _ = rig.engine.evaluate_entry(rig.draft(), rig.ctx())
    assert "loss_streak_cooldown" in _failed(v)
    v, _ = rig.engine.evaluate_entry(rig.draft(), rig.ctx(now=T + timedelta(hours=2),
                                                          snapshot=_snapshot().model_copy(update={"as_of": T + timedelta(hours=2), "last_closed_bar_open": T + timedelta(hours=2) - timedelta(seconds=900)})))
    assert "loss_streak_cooldown" not in _failed(v)


def test_verdict_lists_limit_keys_and_expires() -> None:
    rig = Rig()
    v, _ = rig.engine.evaluate_entry(rig.draft(), rig.ctx())
    keys = {c.limit_key for c in v.checks if c.limit_key}
    assert "risk.per_trade_budget_fraction" in keys and "risk.max_total_exposure_tmn" in keys
    assert v.expires_at == T + timedelta(seconds=30) and v.limits_version == "lim-t"


def test_missing_limit_fails_closed() -> None:
    rig = Rig()
    rig.engine = RiskEngine(LIMITS.model_copy(update={"risk": RISK.model_copy(update={"per_trade_budget_fraction": None})}),
                            COSTS, EXE, rig.ledger, rig.kill, rig.prot, starting_capital_tmn=Decimal(5_000_000))
    v, i = rig.engine.evaluate_entry(rig.draft(), rig.ctx())
    assert v.outcome is RiskOutcome.REJECT and i is None


def test_recheck_after_approval_requires_same_qty() -> None:
    rig = Rig()
    _, intent = rig.engine.evaluate_entry(rig.draft(), rig.ctx())
    assert intent is not None
    ok = rig.engine.recheck(intent, _cand(), rig.ctx(now=T + timedelta(seconds=20)))
    assert ok.outcome is RiskOutcome.APPROVE and ok.approved_qty == intent.qty
    rig.kill.latch("drill", at=T)
    bad = rig.engine.recheck(intent, _cand(), rig.ctx(now=T + timedelta(seconds=40)))
    assert bad.outcome is RiskOutcome.REJECT


def test_exit_checks_sellable_and_double_sell() -> None:
    rig = Rig()
    rig.ledger.apply_fill(Fill("BTCTMN", "BUY", Decimal("0.01"), Decimal(100_000_000), Decimal(0), "TMN", T, "x", "ENTRY", "d0", "v"), plan=None)
    v, intent = rig.engine.evaluate_exit("BTCTMN", OrderPurpose.EXIT_STOP, Decimal(99_000_000), "d0", rig.ctx(), META)
    assert v.outcome is RiskOutcome.APPROVE and intent is not None and intent.qty == Decimal("0.01") and intent.side == "SELL"
    rig.ledger.positions["BTCTMN"].exit_order_id = intent.client_order_id
    v2, i2 = rig.engine.evaluate_exit("BTCTMN", OrderPurpose.EXIT_TAKE_PROFIT, Decimal(101_000_000), "d0", rig.ctx(), META)
    assert v2.outcome is RiskOutcome.REJECT and i2 is None and "no_pending_exit" in _failed(v2)


def test_exits_allowed_under_kill_switch() -> None:
    rig = Rig()
    rig.ledger.apply_fill(Fill("BTCTMN", "BUY", Decimal("0.01"), Decimal(100_000_000), Decimal(0), "TMN", T, "x", "ENTRY", "d0", "v"), plan=None)
    rig.kill.latch("drill", at=T)
    v, _ = rig.engine.evaluate_exit("BTCTMN", OrderPurpose.KILL, Decimal(99_000_000), "d0", rig.ctx(), META)
    assert v.outcome is RiskOutcome.APPROVE
