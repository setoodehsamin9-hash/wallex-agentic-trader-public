"""Order book snapshot from Wallex depth (REST `/v1/depth` or MCP `wallex_basic_get_market_depth`).

Each Wallex depth message is a full snapshot of each side (not a delta). Prices arrive as JSON numbers: callers
must parse with `parse_float=Decimal`; this module converts anything else through `str` to avoid float error.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from wallex_agent.contracts import BookTop, QualityFlag

Level = tuple[Decimal, Decimal]


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True, slots=True)
class OrderBook:
    bids: tuple[Level, ...]
    """Best (highest) first."""
    asks: tuple[Level, ...]
    """Best (lowest) first."""
    received_at: datetime
    flags: frozenset[QualityFlag] = field(default=frozenset())

    def top(self) -> BookTop:
        if not self.bids or not self.asks:
            raise ValueError("one-sided book has no top")
        return BookTop(bid=self.bids[0][0], ask=self.asks[0][0], bid_qty=self.bids[0][1], ask_qty=self.asks[0][1],
                       received_at=self.received_at)

    def spread_bps(self) -> Decimal | None:
        if not self.bids or not self.asks:
            return None
        bid, ask = self.bids[0][0], self.asks[0][0]
        return (ask - bid) / ((ask + bid) / 2) * Decimal(10000)

    def qty_within(self, side: str, limit_price: Decimal) -> Decimal:
        """Visible quantity a BUY (asks <= limit) or SELL (bids >= limit) could take."""
        if side == "BUY":
            return sum((q for p, q in self.asks if p <= limit_price), Decimal(0))
        return sum((q for p, q in self.bids if p >= limit_price), Decimal(0))


def _levels(raw: Any) -> list[Level]:
    rows: list[Mapping[str, Any]] = list(raw or [])
    return [(_dec(x["price"]), _dec(x["quantity"])) for x in rows]


def parse_depth(payload: Mapping[str, Any], *, received_at: datetime) -> OrderBook:
    result: Mapping[str, Any] = payload.get("result", payload)
    asks = tuple(sorted(_levels(result.get("ask")), key=lambda lv: lv[0]))
    bids = tuple(sorted(_levels(result.get("bid")), key=lambda lv: lv[0], reverse=True))
    flags: set[QualityFlag] = set()
    if not asks or not bids:
        flags.add(QualityFlag.ONE_SIDED_BOOK)
    elif bids[0][0] >= asks[0][0]:
        flags.add(QualityFlag.CROSSED_BOOK)
    return OrderBook(bids, asks, received_at, frozenset(flags))
