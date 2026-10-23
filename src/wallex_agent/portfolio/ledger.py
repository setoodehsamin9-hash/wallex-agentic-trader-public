"""Ledger: the system's trade truth (reconciled with Wallex in LIVE). Decimal only.

- Cash is total TMN; reservations hold TMN for pending entries; spendable = cash - reservations.
  While an entry is partially filled its reservation is kept until the order is terminal, which double-counts
  conservatively (spendable can only be under-, never over-stated).
- Positions are built from net fills after fees. A fee charged in the base asset reduces the sellable quantity.
- Realized P&L is booked on sells against proportional cost basis (which includes entry fees).
- Equity marks open positions at liquidation value (mark x (1 - fee)); peak equity and max drawdown include
  unrealized losses, not only closed trades.
- `on_reservation` journals every reserve/release (state log) so restart recovery can rebuild reservations.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

ZERO = Decimal(0)


class InsufficientFunds(RuntimeError):
    pass


def reservation_key(symbol: str, intent_id: str) -> str:
    return f"{symbol}|{intent_id}"


def reservation_id(symbol: str, intent_id: str) -> str:
    """The id `Ledger.reserve(reservation_key(...))` returns; shared by risk and execution."""
    return f"res-{reservation_key(symbol, intent_id)}"


@dataclass(frozen=True, slots=True)
class ExitPlan:
    candidate_id: str
    stop_price: Decimal
    take_profit_price: Decimal | None
    deadline: datetime
    trail_distance: Decimal | None = None
    signal_exit: bool = False
    """Set by the runtime when the strategy's deterministic exit rule fires; the exit coordinator then sells."""


@dataclass(frozen=True, slots=True)
class Fill:
    symbol: str
    side: Literal["BUY", "SELL"] | str
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str
    at: datetime
    client_order_id: str
    purpose: str
    decision_id: str
    strategy_version: str


@dataclass
class Position:
    position_id: str
    symbol: str
    qty: Decimal
    cost_tmn: Decimal
    opened_at: datetime
    decision_id: str
    strategy_version: str
    plan: ExitPlan | None
    high_water: Decimal | None = None
    """Highest observed price since entry (chandelier trail). Not persisted: after a restart it restarts from the
    next observation, so the effective stop can only fall back to the approved stop_price, never below it."""
    entry_qty: Decimal = ZERO
    entry_notional_tmn: Decimal = ZERO
    exit_qty: Decimal = ZERO
    exit_notional_tmn: Decimal = ZERO
    fees_tmn: Decimal = ZERO
    realized_tmn: Decimal = ZERO
    exit_order_id: str | None = None
    """client_order_id of the single pending exit; prevents double sells."""
    last_exit_reason: str | None = None


@dataclass(frozen=True, slots=True)
class TradeRecord:
    trade_id: str
    symbol: str
    decision_id: str
    strategy_version: str
    opened_at: datetime
    closed_at: datetime
    qty: Decimal
    avg_entry_price: Decimal
    avg_exit_price: Decimal
    fees_tmn: Decimal
    net_pnl_tmn: Decimal
    exit_reason: str


@dataclass
class Ledger:
    starting_cash_tmn: Decimal
    cash_tmn: Decimal = field(init=False)
    reservations: dict[str, Decimal] = field(default_factory=lambda: {})
    positions: dict[str, Position] = field(default_factory=lambda: {})
    trades: list[TradeRecord] = field(default_factory=lambda: [])
    dust: dict[str, Decimal] = field(default_factory=lambda: {})
    realized_pnl_tmn: Decimal = ZERO
    fees_paid_tmn: Decimal = ZERO
    peak_equity_tmn: Decimal = field(init=False)
    max_drawdown_fraction: Decimal = ZERO
    last_equity_tmn: Decimal = field(init=False)
    equity_curve: list[tuple[datetime, Decimal]] = field(default_factory=lambda: [])
    _seq: int = 0
    on_reservation: Callable[[str, str, Decimal], None] | None = field(default=None, repr=False, compare=False)
    """Called with ("RESERVE" | "RELEASE", reservation_id, amount) after the change is made."""

    def __post_init__(self) -> None:
        self.cash_tmn = self.starting_cash_tmn
        self.peak_equity_tmn = self.starting_cash_tmn
        self.last_equity_tmn = self.starting_cash_tmn

    # --- reservations -------------------------------------------------------------------------------------------
    @property
    def reserved_tmn(self) -> Decimal:
        return sum(self.reservations.values(), ZERO)

    @property
    def spendable_tmn(self) -> Decimal:
        return self.cash_tmn - self.reserved_tmn

    def reserve(self, key: str, amount: Decimal) -> str:
        if amount <= 0:
            raise ValueError("reservation must be positive")
        if amount > self.spendable_tmn:
            raise InsufficientFunds(f"need {amount}, spendable {self.spendable_tmn}")
        rid = f"res-{key}"
        if rid in self.reservations:
            raise ValueError(f"duplicate reservation {rid}")
        self.reservations[rid] = amount
        if self.on_reservation is not None:
            self.on_reservation("RESERVE", rid, amount)
        return rid

    def release(self, reservation_id: str) -> None:
        amount = self.reservations.pop(reservation_id, None)
        if amount is not None and self.on_reservation is not None:
            self.on_reservation("RELEASE", reservation_id, amount)

    # --- fills --------------------------------------------------------------------------------------------------
    def apply_fill(self, fill: Fill, *, plan: ExitPlan | None, dust_qty: Decimal = ZERO) -> TradeRecord | None:
        if not all(v.is_finite() for v in (fill.qty, fill.price, fill.fee)):
            raise ValueError("fill amounts must be finite")
        if fill.qty <= 0 or fill.price <= 0:
            raise ValueError("fill qty and price must be positive")
        if fill.side not in ("BUY", "SELL") or fill.fee < 0:
            raise ValueError("invalid fill side or negative fee")
        if not fill.symbol.endswith("TMN") or fill.fee_asset not in ("TMN", fill.symbol[:-3]):
            raise ValueError("unsupported fill market or fee asset")
        notional = fill.qty * fill.price
        fee_in_tmn = fill.fee_asset == "TMN"
        fee_tmn = fill.fee if fee_in_tmn else fill.fee * fill.price

        if fill.side == "BUY":
            spent = notional + (fill.fee if fee_in_tmn else ZERO)
            if spent > self.cash_tmn:
                raise InsufficientFunds("buy fill exceeds cash")
            net_qty = fill.qty - (ZERO if fee_in_tmn else fill.fee)
            if net_qty <= 0:
                raise ValueError("buy fee leaves no sellable quantity")
            self.cash_tmn -= spent
            self.fees_paid_tmn += fee_tmn
            pos = self.positions.get(fill.symbol)
            if pos is None:
                self._seq += 1
                pos = Position(f"pos-{self._seq}-{fill.symbol}", fill.symbol, ZERO, ZERO, fill.at, fill.decision_id,
                               fill.strategy_version, plan)
                self.positions[fill.symbol] = pos
            elif plan is not None and pos.plan is None:
                pos.plan = plan
            pos.qty += net_qty
            # A base-asset fee is already reflected in net_qty. Basis is the actual TMN debit.
            pos.cost_tmn += spent
            pos.entry_qty += fill.qty
            pos.entry_notional_tmn += notional
            pos.fees_tmn += fee_tmn
            return None

        pos = self.positions.get(fill.symbol)
        sold = fill.qty + (ZERO if fee_in_tmn else fill.fee)
        if pos is None or sold > pos.qty:
            raise ValueError(f"sell {sold} exceeds held {pos.qty if pos else 0} for {fill.symbol}")
        proceeds = notional - (fill.fee if fee_in_tmn else ZERO)
        if proceeds < 0:
            raise ValueError("sell fee exceeds proceeds")
        cost_part = pos.cost_tmn * sold / pos.qty
        pnl = proceeds - cost_part
        self.fees_paid_tmn += fee_tmn
        self.cash_tmn += proceeds
        self.realized_pnl_tmn += pnl
        pos.realized_tmn += pnl
        pos.cost_tmn -= cost_part
        pos.qty -= sold
        pos.exit_qty += fill.qty
        pos.exit_notional_tmn += notional
        pos.fees_tmn += fee_tmn
        pos.last_exit_reason = fill.purpose
        if pos.qty <= dust_qty:
            if pos.qty > 0:
                self.dust[fill.symbol] = self.dust.get(fill.symbol, ZERO) + pos.qty
            trade = TradeRecord(
                trade_id=pos.position_id, symbol=pos.symbol, decision_id=pos.decision_id,
                strategy_version=pos.strategy_version, opened_at=pos.opened_at, closed_at=fill.at, qty=pos.entry_qty,
                avg_entry_price=pos.entry_notional_tmn / pos.entry_qty, avg_exit_price=pos.exit_notional_tmn / pos.exit_qty,
                fees_tmn=pos.fees_tmn, net_pnl_tmn=pos.realized_tmn, exit_reason=fill.purpose)
            self.trades.append(trade)
            del self.positions[fill.symbol]
            return trade
        return None

    # --- valuation ----------------------------------------------------------------------------------------------
    def equity(self, marks: dict[str, Decimal], fee_rate: Decimal) -> Decimal:
        value = self.cash_tmn
        for sym, pos in self.positions.items():
            mark = marks.get(sym)
            value += pos.qty * (mark if mark is not None else pos.cost_tmn / pos.qty if pos.qty else ZERO) * (1 - fee_rate)
        return value

    def unrealized_pnl_tmn(self, marks: dict[str, Decimal], fee_rate: Decimal) -> Decimal:
        total = ZERO
        for sym, pos in self.positions.items():
            mark = marks.get(sym)
            if mark is not None:
                total += pos.qty * mark * (1 - fee_rate) - pos.cost_tmn
        return total

    def exposure_tmn(self, marks: dict[str, Decimal]) -> Decimal:
        held = sum((p.qty * marks.get(s, p.cost_tmn / p.qty if p.qty else ZERO) for s, p in self.positions.items()), ZERO)
        return held + self.reserved_tmn

    def mark(self, marks: dict[str, Decimal], at: datetime, *, fee_rate: Decimal) -> Decimal:
        eq = self.equity(marks, fee_rate)
        self.last_equity_tmn = eq
        if eq > self.peak_equity_tmn:
            self.peak_equity_tmn = eq
        dd = (self.peak_equity_tmn - eq) / self.peak_equity_tmn if self.peak_equity_tmn > 0 else ZERO
        if dd > self.max_drawdown_fraction:
            self.max_drawdown_fraction = dd
        self.equity_curve.append((at, eq))
        return eq
