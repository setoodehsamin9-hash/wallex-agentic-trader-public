"""Fakes for restart-recovery tests. No network, no credentials, no real orders.

`ExchangeLikeVenue` models the property that matters for LIVE recovery: the venue keeps its orders and balances
OUTSIDE our process, so after our crash it can still be queried by client_order_id (unlike the simulators).
`Crash` stands for the process dying at an exact point (it is not an Exception, so nothing in the kernel catches it).
"""

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from support.synthetic import EV1, synthetic_bars

from wallex_agent.contracts import TERMINAL_ORDER_STATES, Environment, FillSource, OrderIntent, OrderState
from wallex_agent.execution.venue import VenueFill, VenueOrder


class Crash(BaseException):
    """The process died here."""


class ExchangeLikeVenue:
    environment = Environment.SHADOW
    fill_source = FillSource.EXCHANGE

    def __init__(self, *, fee_rate: Decimal, cash: Decimal,
                 mode: Literal["FILL", "REST", "PARTIAL"] = "FILL") -> None:
        self.fee_rate = fee_rate
        self.mode = mode
        self.orders: dict[str, VenueOrder] = {}
        self.submits: list[str] = []
        self.hidden: set[str] = set()
        """Orders the exchange does not (yet) report on lookup."""
        self.crash_before_accept = False
        self.crash_after_accept = False
        self.cash = cash
        self.base: dict[str, Decimal] = {}

    def _fill(self, order: VenueOrder, qty: Decimal, now: datetime) -> None:
        intent = order.intent
        assert intent.limit_price is not None
        price = intent.limit_price
        fee = qty * price * self.fee_rate
        order.fills.append(VenueFill(f"{order.client_order_id}-x{len(order.fills)}", qty, price, fee, "TMN", now))
        asset = intent.symbol[:-3]
        if intent.side == "BUY":
            self.cash -= qty * price + fee
            self.base[asset] = self.base.get(asset, Decimal(0)) + qty
        else:
            self.cash += qty * price - fee
            self.base[asset] = self.base.get(asset, Decimal(0)) - qty

    def submit(self, intent: OrderIntent, now: datetime) -> VenueOrder:
        if self.crash_before_accept:
            raise Crash("died before the request left the process")
        self.submits.append(intent.client_order_id)
        order = VenueOrder(intent.client_order_id, intent, OrderState.OPEN, "NEW")
        if self.mode == "FILL":
            self._fill(order, intent.qty, now)
            order.state, order.status_raw = OrderState.FILLED, "FILLED"
        elif self.mode == "PARTIAL":
            self._fill(order, intent.qty / 2, now)
            order.state, order.status_raw = OrderState.PARTIAL, "PARTIALLY_FILLED"
        self.orders[intent.client_order_id] = order
        if self.crash_after_accept:
            raise Crash("exchange accepted; response lost with the process")
        return order

    def complete(self, client_order_id: str, now: datetime) -> None:
        """The remainder fills while our process is down."""
        order = self.orders[client_order_id]
        left = order.intent.qty - order.filled_qty
        if left > 0:
            self._fill(order, left, now)
        order.state, order.status_raw = OrderState.FILLED, "FILLED"

    def query(self, client_order_id: str) -> VenueOrder | None:
        return None if client_order_id in self.hidden else self.orders.get(client_order_id)

    def cancel(self, client_order_id: str, now: datetime) -> VenueOrder | None:
        order = self.orders.get(client_order_id)
        if order is not None and order.state not in TERMINAL_ORDER_STATES:
            order.state, order.status_raw = OrderState.CANCELED, "CANCELED"
        return order

    def balances(self) -> dict[str, Decimal]:
        return {"TMN": self.cash, **self.base}


class FakePublicClient:
    """Synthetic 1m candles + a book around the last close; records every request. Has no write methods at all."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        self.now = now
        self.calls: list[str] = []
        self.series = {"BTCTMN": synthetic_bars(4, 1e8, 0.0), "ETHTMN": synthetic_bars(4, 5e6, 1.3)}

    def _body(self, name: str) -> Any:
        raw = json.loads((EV1 / name).read_text())["body"]
        return json.loads(raw, parse_float=Decimal) if isinstance(raw, str) else raw

    def candles(self, symbol: str, resolution: str, frm: int, to: int) -> dict[str, Any]:
        self.calls.append(f"GET candles {symbol}")
        bars = [b for b in self.series[symbol] if frm <= b.ts_open.timestamp() <= to and b.ts_open + timedelta(seconds=60) <= self.now()]
        return {"s": "ok", "t": [int(b.ts_open.timestamp()) for b in bars], "o": [str(b.open) for b in bars],
                "h": [str(b.high) for b in bars], "l": [str(b.low) for b in bars], "c": [str(b.close) for b in bars],
                "v": [str(b.volume) for b in bars]}

    def depth(self, symbol: str) -> dict[str, Any]:
        self.calls.append(f"GET depth {symbol}")
        last = [b for b in self.series[symbol] if b.ts_open <= self.now()][-1].close
        return {"result": {"ask": [{"price": last * Decimal("1.0003"), "quantity": Decimal(5)}],
                           "bid": [{"price": last * Decimal("0.9997"), "quantity": Decimal(5)}]}}

    def markets_v1(self) -> Any:
        self.calls.append("GET markets_v1")
        return self._body("_v1_markets.json")

    def markets_hector(self) -> Any:
        self.calls.append("GET markets_hector")
        return self._body("_hector_web_v1_markets.json")

    def package(self, package_id: int) -> Any:
        self.calls.append("GET package")
        return self._body("_v1_prop_packages_183.json")
