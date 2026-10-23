"""Book-based fill simulator for SHADOW: live public order book, simulated fills, ZERO real writes.

Immediate-or-cancel against the visible depth at submission time: BUY walks asks <= limit, SELL walks bids >= limit;
any unfilled remainder is cancelled (partial fills happen). No queue model or latency model (stated limitation).
Fault injection flags exist for failure drills: drop the response (UNKNOWN path), forget the order, fail submission.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from wallex_agent.contracts import TERMINAL_ORDER_STATES, Environment, FillSource, OrderIntent, OrderState
from wallex_agent.market.book import OrderBook

from .venue import SubmitTimeout, VenueError, VenueFill, VenueOrder


class ShadowVenue:
    environment = Environment.SHADOW
    fill_source = FillSource.SHADOW_SIM

    def __init__(self, *, fee_rate: Decimal, book: Callable[[str], OrderBook | None]) -> None:
        self.fee_rate = fee_rate
        self._book = book
        self.submitted: list[str] = []
        self._orders: dict[str, VenueOrder] = {}
        self.drop_next_response = False
        self.forget_orders = False
        self.fail_submit = False

    def submit(self, intent: OrderIntent, now: datetime) -> VenueOrder:
        if self.fail_submit:
            raise VenueError("injected submit failure")
        self.submitted.append(intent.client_order_id)
        order = VenueOrder(intent.client_order_id, intent, OrderState.OPEN, "SIM_OPEN")
        book = self._book(intent.symbol)
        limit = intent.limit_price
        if book is not None and limit is not None and not book.flags:
            levels = book.asks if intent.side == "BUY" else book.bids
            left = intent.qty
            for price, qty in levels:
                if left <= 0 or (intent.side == "BUY" and price > limit) or (intent.side == "SELL" and price < limit):
                    break
                take = min(left, qty)
                order.fills.append(VenueFill(f"{intent.client_order_id}-f{len(order.fills)}", take, price,
                                             take * price * self.fee_rate, "TMN", now))
                left -= take
        filled = order.filled_qty
        if filled == intent.qty:
            order.state, order.status_raw = OrderState.FILLED, "SIM_FILLED"
        else:
            order.state, order.status_raw = OrderState.CANCELED, "SIM_IOC_REMAINDER_CANCELED" if filled else "SIM_NO_LIQUIDITY"
        if not self.forget_orders:
            self._orders[intent.client_order_id] = order
        if self.drop_next_response:
            self.drop_next_response = False
            raise SubmitTimeout("injected lost response")
        return order

    def query(self, client_order_id: str) -> VenueOrder | None:
        return self._orders.get(client_order_id)

    def cancel(self, client_order_id: str, now: datetime) -> VenueOrder | None:
        order = self._orders.get(client_order_id)
        if order is not None and order.state not in TERMINAL_ORDER_STATES:
            order.state = OrderState.CANCELED
        return order
