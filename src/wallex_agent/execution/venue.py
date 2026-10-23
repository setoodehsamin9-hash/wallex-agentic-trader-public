"""Venue interface implemented by the backtest simulator, the shadow simulator and the LIVE Wallex REST adapter.

The execution engine is the only caller. A venue never decides anything: it accepts an intent, reports what the
market/exchange did, and answers lookups by client_order_id (the reconciliation key).
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from wallex_agent.contracts import Environment, FillSource, OrderIntent, OrderState


class SubmitTimeout(RuntimeError):
    """The submission outcome is unknown (timeout / lost response). The order may exist on the venue."""


class VenueError(RuntimeError):
    """The venue definitively rejected or failed the request; nothing was placed."""


@dataclass(frozen=True, slots=True)
class VenueFill:
    fill_id: str
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str
    at: datetime


@dataclass
class VenueOrder:
    client_order_id: str
    intent: OrderIntent
    state: OrderState
    status_raw: str
    fills: list[VenueFill] = field(default_factory=lambda: [])

    @property
    def filled_qty(self) -> Decimal:
        return sum((f.qty for f in self.fills), Decimal(0))


class Venue(Protocol):
    environment: Environment
    fill_source: FillSource

    def submit(self, intent: OrderIntent, now: datetime) -> VenueOrder: ...

    def query(self, client_order_id: str) -> VenueOrder | None: ...

    def cancel(self, client_order_id: str, now: datetime) -> VenueOrder | None: ...
