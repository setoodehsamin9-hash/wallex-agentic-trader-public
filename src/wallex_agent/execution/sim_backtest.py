"""Bar-based fill simulator for BACKTEST. Explicit, conservative assumptions (reported with every backtest):

- Entries (LIMIT BUY): never at the signal bar's close. Fill on a LATER bar at open x (1 + slippage) if that is
  <= limit; else at the limit if the bar trades strictly below it; else wait; expire once a bar opens at or after
  created_at + entry_ttl.
- EXIT_STOP: stop-market at the stop level on the bar that touched it: price = min(stop, bar.open) x (1 - slippage)
  (a gap through the stop fills at the worse open).
- EXIT_TAKE_PROFIT: resting limit: price = max(tp, bar.open), no slippage.
- Other sells (time, AI, liquidation, kill): next bar open x (1 - slippage) if >= limit, else at limit if the bar
  trades strictly above it, else wait/expire and the exit coordinator re-issues.
- Fee = notional x fee_rate in TMN. No partial fills, no queue model, candle volume ignored (stated limitation).
"""

from datetime import datetime, timedelta
from decimal import Decimal

from wallex_agent.contracts import TERMINAL_ORDER_STATES, Environment, FillSource, OrderIntent, OrderPurpose, OrderState
from wallex_agent.market.bars import Bar

from .venue import VenueFill, VenueOrder

BPS = Decimal(10000)


class BacktestVenue:
    environment = Environment.BACKTEST
    fill_source = FillSource.BACKTEST_SIM

    def __init__(self, *, fee_rate: Decimal, slippage_bps: Decimal, entry_ttl_s: int) -> None:
        self.fee_rate = fee_rate
        self.slip = slippage_bps / BPS
        self.ttl = timedelta(seconds=entry_ttl_s)
        self.submitted: list[str] = []
        self._orders: dict[str, VenueOrder] = {}
        self._bar: dict[str, Bar] = {}

    def _fill(self, order: VenueOrder, price: Decimal, at: datetime) -> None:
        qty = order.intent.qty
        order.fills.append(VenueFill(f"{order.client_order_id}-f{len(order.fills)}", qty, price, qty * price * self.fee_rate,
                                     "TMN", at))
        order.state, order.status_raw = OrderState.FILLED, "SIM_FILLED"

    def _try(self, order: VenueOrder, bar: Bar) -> None:
        intent = order.intent
        limit = intent.limit_price
        assert limit is not None
        if bar.ts_open < intent.created_at:
            return
        if intent.side == "BUY":
            px = bar.open * (1 + self.slip)
            if px <= limit:
                self._fill(order, px, bar.ts_open)
            elif bar.low < limit:
                self._fill(order, limit, bar.ts_open)
        else:
            px = bar.open * (1 - self.slip)
            if px >= limit:
                self._fill(order, px, bar.ts_open)
            elif bar.high > limit:
                self._fill(order, limit, bar.ts_open)
        if order.state not in TERMINAL_ORDER_STATES and bar.ts_open >= intent.created_at + self.ttl:
            order.state, order.status_raw = OrderState.EXPIRED, "SIM_EXPIRED"

    def on_bar(self, symbol: str, bar: Bar) -> None:
        self._bar[symbol] = bar
        for order in self._orders.values():
            if order.intent.symbol == symbol and order.state is OrderState.OPEN:
                self._try(order, bar)

    def submit(self, intent: OrderIntent, now: datetime) -> VenueOrder:
        order = VenueOrder(intent.client_order_id, intent, OrderState.OPEN, "SIM_OPEN")
        self._orders[intent.client_order_id] = order
        self.submitted.append(intent.client_order_id)
        bar = self._bar.get(intent.symbol)
        limit = intent.limit_price
        if bar is not None and limit is not None and intent.side == "SELL":
            if intent.purpose is OrderPurpose.EXIT_STOP:
                self._fill(order, min(limit, bar.open) * (1 - self.slip), now)
            elif intent.purpose is OrderPurpose.EXIT_TAKE_PROFIT:
                self._fill(order, max(limit, bar.open), now)
        return order

    def query(self, client_order_id: str) -> VenueOrder | None:
        return self._orders.get(client_order_id)

    def cancel(self, client_order_id: str, now: datetime) -> VenueOrder | None:
        order = self._orders.get(client_order_id)
        if order is not None and order.state not in TERMINAL_ORDER_STATES:
            order.state, order.status_raw = OrderState.CANCELED, "SIM_CANCELED"
        return order
