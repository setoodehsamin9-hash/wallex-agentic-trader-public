"""Risk Engine (ADR-002 §4). Deterministic, final, and the ONLY producer of executable OrderIntents.

Entry: `evaluate_entry(draft, ctx)` runs every check, sizes from the per-trade risk budget, applies caps, reserves
TMN and returns `(RiskVerdict, OrderIntent | None)`. Any failed check -> REJECT, no reservation, no intent.
A limit that is not configured fails closed. After a human approval, `recheck(intent, candidate, ctx)` must be able
to approve the SAME quantity on fresh state or it rejects (a changed quantity needs a new approval).
Exits: `evaluate_exit` checks the position, a single pending exit, and that orders may be sent now. Kill switch and
loss protections never block exits.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, ROUND_FLOOR, Decimal

from wallex_agent.contracts import (
    AutonomyMode,
    Environment,
    MarketMeta,
    MarketSnapshot,
    OrderIntent,
    OrderPurpose,
    QualityFlag,
    RiskCheck,
    RiskOutcome,
    RiskPosture,
    RiskVerdict,
    SignalCandidate,
)
from wallex_agent.contracts.hashing import content_id
from wallex_agent.market.book import OrderBook
from wallex_agent.portfolio.ledger import Ledger, reservation_id, reservation_key
from wallex_agent.runtime.calendar import WindowStatus
from wallex_agent.runtime.config import CostModel, ExecutionSettings, Limits

from .kill_switch import KillSwitch
from .protections import Protections

BPS = Decimal(10000)
ZERO = Decimal(0)
BLOCKING_FLAGS = frozenset({QualityFlag.STALE, QualityFlag.GAP, QualityFlag.CROSSED_BOOK, QualityFlag.ONE_SIDED_BOOK,
                            QualityFlag.INCOMPLETE_BAR, QualityFlag.RESOLUTION_MISMATCH, QualityFlag.SYMBOL_NOT_ELIGIBLE})
EXIT_PURPOSES = frozenset({OrderPurpose.EXIT_STOP, OrderPurpose.EXIT_TAKE_PROFIT, OrderPurpose.EXIT_TIME,
                           OrderPurpose.EXIT_SIGNAL, OrderPurpose.EXIT_AI, OrderPurpose.LIQUIDATION, OrderPurpose.KILL})


@dataclass(frozen=True, slots=True)
class IntentDraft:
    decision_id: str
    candidate: SignalCandidate
    autonomy: AutonomyMode
    environment: Environment
    posture: RiskPosture = RiskPosture.NORMAL


@dataclass(frozen=True, slots=True)
class RiskContext:
    now: datetime
    snapshot: MarketSnapshot
    book: OrderBook | None
    marks: dict[str, Decimal]
    eligible: frozenset[str]
    window: WindowStatus
    require_book: bool
    environment: Environment
    session_key: str = "default"


class _Checks:
    def __init__(self) -> None:
        self.items: list[RiskCheck] = []

    def add(self, name: str, passed: bool, detail: str, key: str | None = None) -> bool:
        self.items.append(RiskCheck(name=name, passed=passed, detail=detail, limit_key=key))
        return passed

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.items)


class RiskEngine:
    def __init__(self, limits: Limits, costs: CostModel, execution: ExecutionSettings, ledger: Ledger, kill: KillSwitch,
                 protections: Protections, *, starting_capital_tmn: Decimal,
                 fee_rate: Callable[[], Decimal] | None = None) -> None:
        self.limits = limits
        self.costs = costs
        self.execution = execution
        self.ledger = ledger
        self.kill = kill
        self.prot = protections
        self.starting_capital = starting_capital_tmn
        self._fee = fee_rate or (lambda: costs.fee_rate)

    # ------------------------------------------------------------------------------------------------ helpers
    def _verdict(self, intent_hash: str, outcome: RiskOutcome, checks: _Checks, now: datetime,
                 qty: Decimal | None = None, notional: Decimal | None = None, reservation: str | None = None) -> RiskVerdict:
        ttl = self.limits.risk.verdict_ttl_s or 0
        body = {"h": intent_hash, "o": outcome.value, "t": now, "q": qty}
        return RiskVerdict(verdict_id="rv-" + content_id(body)[:16], intent_hash=intent_hash, outcome=outcome,
                           approved_qty=qty if outcome is not RiskOutcome.REJECT else None,
                           approved_notional_tmn=notional if outcome is not RiskOutcome.REJECT else None,
                           checks=tuple(checks.items), limits_version=self.limits.version, evaluated_at=now,
                           expires_at=now + timedelta(seconds=ttl), reservation_id=reservation)

    def pending_entry_symbols(self) -> set[str]:
        return {rid.removeprefix("res-").split("|", 1)[0] for rid in self.ledger.reservations}

    @staticmethod
    def _intent(*, decision_id: str, symbol: str, side: str, qty: Decimal, limit_price: Decimal | None, purpose: OrderPurpose,
                environment: Environment, autonomy: AutonomyMode, now: datetime, salt: str = "") -> OrderIntent:
        seed = content_id({"d": decision_id, "s": symbol, "p": purpose.value, "x": salt})
        body = {"intent_id": "in-" + seed[:16], "decision_id": decision_id, "client_order_id": "wa" + seed[:22],
                "symbol": symbol, "side": side, "order_type": "LIMIT" if limit_price is not None else "MARKET",
                "qty": qty, "limit_price": limit_price, "purpose": purpose, "environment": environment,
                "autonomy": autonomy, "created_at": now}
        return OrderIntent(intent_hash=content_id(body), **body)  # type: ignore[arg-type]

    # ------------------------------------------------------------------------------------------------ entry checks
    def _entry_checks(self, cand: SignalCandidate, ctx: RiskContext, own_reservation: str | None) -> _Checks:
        lim, r = self.limits, self.limits.risk
        ck = _Checks()
        snap = ctx.snapshot
        ck.add("symbol_eligible", cand.symbol in ctx.eligible and snap.symbol == cand.symbol, f"{cand.symbol}")
        ck.add("kill_switch", not self.kill.latched, self.kill.state.reason or "not latched")
        ck.add("trading_window", ctx.window.entries_allowed, ctx.window.reason)
        ck.add("candidate_fresh", ctx.now < cand.expires_at and cand.snapshot_id == snap.snapshot_id,
               f"expires {cand.expires_at.isoformat()}")

        bar_age = (ctx.now - (snap.last_closed_bar_open + timedelta(seconds=snap.bar_interval_seconds))).total_seconds()
        max_bar = lim.data.max_bar_age_s
        bad = snap.quality_flags & BLOCKING_FLAGS
        ck.add("data_quality", max_bar is not None and bar_age <= max_bar and not bad,
               f"bar_age={bar_age:.0f}s flags={sorted(f.value for f in bad)}", "data.max_bar_age_s")
        if ctx.require_book:
            max_book = lim.data.max_book_age_s
            age = (ctx.now - ctx.book.received_at).total_seconds() if ctx.book else None
            ck.add("book_fresh", ctx.book is not None and max_book is not None and age is not None and age <= max_book
                   and not ctx.book.flags, f"age={age}", "data.max_book_age_s")
        if ctx.book is not None:
            spread = ctx.book.spread_bps()
            ck.add("spread", spread is not None and r.max_spread_bps is not None and spread <= r.max_spread_bps,
                   f"spread_bps={spread}", "risk.max_spread_bps")

        fee = self._fee()
        equity = self.ledger.equity(ctx.marks, fee)
        peak = max(self.ledger.peak_equity_tmn, equity)
        dd = (peak - equity) / peak if peak > 0 else ZERO
        loss = self.starting_capital - equity
        ck.add("total_loss_budget", r.total_loss_budget_tmn is not None and loss < r.total_loss_budget_tmn,
               f"loss={loss:.0f}", "risk.total_loss_budget_tmn")
        sess = self.prot.session_loss(ctx.session_key, equity)
        ck.add("session_loss_budget", r.session_loss_budget_tmn is not None and sess < r.session_loss_budget_tmn,
               f"session_loss={sess:.0f}", "risk.session_loss_budget_tmn")
        emergency = r.drawdown_emergency_fraction
        if emergency is None or dd >= emergency:
            ck.add("drawdown_emergency", False, f"drawdown={dd:.4f}", "risk.drawdown_emergency_fraction")
            if emergency is not None:
                self.kill.latch(f"drawdown {dd:.4f} >= emergency {emergency}", at=ctx.now)
        else:
            ck.add("drawdown_emergency", True, f"drawdown={dd:.4f}", "risk.drawdown_emergency_fraction")
        try:
            paused = self.prot.drawdown_paused(ctx.now, dd)
            ck.add("drawdown_pause", not paused, f"drawdown={dd:.4f}", "risk.drawdown_pause_fraction")
        except ValueError as exc:
            ck.add("drawdown_pause", False, str(exc), "risk.drawdown_pause_fraction")
        try:
            until = self.prot.loss_streak_until(ctx.now)
            ck.add("loss_streak_cooldown", until is None, f"until={until.isoformat() if until else None}",
                   "risk.loss_streak_count")
        except ValueError as exc:
            ck.add("loss_streak_cooldown", False, str(exc), "risk.loss_streak_count")

        pending = self.pending_entry_symbols()
        if own_reservation is not None:
            pending.discard(cand.symbol)
        ck.add("one_position_per_symbol", cand.symbol not in self.ledger.positions and cand.symbol not in pending,
               "no pyramiding")
        open_count = len(self.ledger.positions) + len(pending)
        ck.add("max_open_positions", r.max_open_positions is not None and open_count < r.max_open_positions,
               f"open={open_count}", "risk.max_open_positions")
        return ck

    def _size(self, cand: SignalCandidate, ctx: RiskContext, posture: RiskPosture, own_reservation: str | None,
              ck: _Checks) -> tuple[Decimal | None, Decimal, bool]:
        """Returns (qty or None, limit_price, reduced_by_cap)."""
        r, meta = self.limits.risk, ctx.snapshot.meta
        fee = self._fee()
        q = Decimal(1).scaleb(-meta.price_precision)
        limit = (cand.reference_price * (1 + self.execution.entry_limit_slippage_bps / BPS)).quantize(q, rounding=ROUND_FLOOR)
        needed = (r.per_trade_budget_fraction, r.stress_cost_bps, r.max_position_notional_tmn, r.max_total_exposure_tmn,
                  r.max_depth_fraction, r.cautious_size_fraction)
        if any(v is None for v in needed):
            ck.add("sizing", False, "a sizing limit is not configured", "risk.per_trade_budget_fraction")
            return None, limit, False
        assert r.per_trade_budget_fraction is not None and r.stress_cost_bps is not None
        assert r.max_position_notional_tmn is not None and r.max_total_exposure_tmn is not None
        assert r.max_depth_fraction is not None and r.cautious_size_fraction is not None

        own = self.ledger.reservations.get(own_reservation, ZERO) if own_reservation else ZERO
        equity = self.ledger.equity(ctx.marks, fee)
        unit_loss = (limit - cand.stop_price) + limit * fee + cand.stop_price * fee + limit * r.stress_cost_bps / BPS
        if unit_loss <= 0:
            ck.add("sizing", False, "non-positive unit loss", "risk.per_trade_budget_fraction")
            return None, limit, False
        budget = equity * r.per_trade_budget_fraction
        q_risk = budget / unit_loss
        if posture is RiskPosture.CAUTIOUS:
            q_risk *= r.cautious_size_fraction
        exposure = self.ledger.exposure_tmn(ctx.marks) - own
        caps = {
            "max_position_notional": r.max_position_notional_tmn / limit,
            "max_total_exposure": max(ZERO, r.max_total_exposure_tmn - exposure) / limit,
            "spendable": max(ZERO, self.ledger.spendable_tmn + own) / (limit * (1 + fee)),
        }
        if ctx.book is not None:
            caps["liquidity"] = ctx.book.qty_within("BUY", limit) * r.max_depth_fraction
        step = Decimal(1).scaleb(-meta.amount_precision)
        q_cap = min(caps.values())
        qty = min(q_risk, q_cap).quantize(step, rounding=ROUND_DOWN)
        reduced = q_cap < q_risk
        binding = min(caps, key=lambda k: caps[k]) if reduced else "risk_budget"
        ok = qty > 0 and qty >= meta.min_qty and qty * limit >= meta.min_notional
        ck.add("sizing", ok, f"qty={qty} binding={binding} unit_loss={unit_loss:.2f} budget={budget:.0f}",
               "risk.per_trade_budget_fraction")
        ck.add("exposure_caps", ok or qty > 0, f"caps={ {k: str(v.quantize(step, rounding=ROUND_DOWN)) for k, v in caps.items()} }",
               "risk.max_total_exposure_tmn")
        return (qty if ok else None), limit, reduced

    # ------------------------------------------------------------------------------------------------ public API
    def evaluate_entry(self, draft: IntentDraft, ctx: RiskContext) -> tuple[RiskVerdict, OrderIntent | None]:
        cand = draft.candidate
        ck = self._entry_checks(cand, ctx, None)
        qty, limit, reduced = self._size(cand, ctx, draft.posture, None, ck)
        if not ck.ok or qty is None:
            probe = content_id({"d": draft.decision_id, "c": cand.candidate_id, "rejected": True})
            return self._verdict(probe, RiskOutcome.REJECT, ck, ctx.now), None
        intent = self._intent(decision_id=draft.decision_id, symbol=cand.symbol, side="BUY", qty=qty, limit_price=limit,
                              purpose=OrderPurpose.ENTRY, environment=draft.environment, autonomy=draft.autonomy, now=ctx.now)
        amount = qty * limit * (1 + self._fee())
        rid = self.ledger.reserve(reservation_key(cand.symbol, intent.intent_id), amount)
        outcome = RiskOutcome.REDUCE if reduced or draft.posture is RiskPosture.CAUTIOUS else RiskOutcome.APPROVE
        return self._verdict(intent.intent_hash, outcome, ck, ctx.now, qty, qty * limit, rid), intent

    def recheck(self, intent: OrderIntent, cand: SignalCandidate, ctx: RiskContext) -> RiskVerdict:
        rid = reservation_id(intent.symbol, intent.intent_id)
        own = rid if rid in self.ledger.reservations else None
        ck = self._entry_checks(cand, ctx, own)
        ck.add("reservation_held", own is not None, rid)
        qty, _, _ = self._size(cand, ctx, RiskPosture.NORMAL, own, ck)
        same = qty is not None and qty >= intent.qty
        ck.add("approved_qty_still_valid", same, f"now={qty} approved={intent.qty}")
        if not ck.ok:
            if own:
                self.ledger.release(own)
            return self._verdict(intent.intent_hash, RiskOutcome.REJECT, ck, ctx.now)
        assert intent.limit_price is not None
        return self._verdict(intent.intent_hash, RiskOutcome.APPROVE, ck, ctx.now, intent.qty,
                             intent.qty * intent.limit_price, own)

    def evaluate_exit(self, symbol: str, purpose: OrderPurpose, limit_price: Decimal, decision_id: str, ctx: RiskContext,
                      meta: MarketMeta, *, autonomy: AutonomyMode = AutonomyMode.AUTONOMOUS) -> tuple[RiskVerdict, OrderIntent | None]:
        ck = _Checks()
        pos = self.ledger.positions.get(symbol)
        ck.add("exit_purpose", purpose in EXIT_PURPOSES, purpose.value)
        ck.add("position_exists", pos is not None, symbol)
        ck.add("no_pending_exit", pos is not None and pos.exit_order_id is None,
               f"pending={pos.exit_order_id if pos else None}")
        ck.add("orders_allowed", ctx.window.orders_allowed, ctx.window.reason)
        step = Decimal(1).scaleb(-meta.amount_precision)
        qty = pos.qty.quantize(step, rounding=ROUND_DOWN) if pos is not None else ZERO
        ck.add("sellable_qty", qty > 0 and qty >= meta.min_qty and qty * limit_price >= meta.min_notional,
               f"qty={qty} (dust below min_notional cannot be sold)")
        if not ck.ok:
            probe = content_id({"d": decision_id, "s": symbol, "p": purpose.value, "rejected": True, "t": ctx.now})
            return self._verdict(probe, RiskOutcome.REJECT, ck, ctx.now), None
        # The exit coordinator prices exits for its market view (bar trigger level in BACKTEST, bid - slippage on a
        # live book); the risk engine validates and rounds, it does not reprice.
        limit = limit_price.quantize(Decimal(1).scaleb(-meta.price_precision), rounding=ROUND_FLOOR)
        intent = self._intent(decision_id=decision_id, symbol=symbol, side="SELL", qty=qty, limit_price=limit, purpose=purpose,
                              environment=ctx.environment, autonomy=autonomy, now=ctx.now, salt=ctx.now.isoformat())
        return self._verdict(intent.intent_hash, RiskOutcome.APPROVE, ck, ctx.now, qty, qty * limit), intent

    def release_entry(self, intent: OrderIntent) -> None:
        self.ledger.release(reservation_id(intent.symbol, intent.intent_id))
