"""Market metadata and the eligible universe.

`eligible = spot ∩ metadata_available − package_unavailable − confirmed extra restrictions` (fail closed: a symbol
missing from any source is not eligible). Precision comes from `/hector/web/v1/markets` (`amount_precision`,
`price_precision`); minimums from `/v1/markets` (`minQty`, `minNotional`). `stepSize`/`tickSize` in `/v1/markets`
look like digit counts, not increments, so they are not used as increments (Phase 01 finding).
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from wallex_agent.contracts import MarketMeta


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def parse_market_meta(v1_markets: Mapping[str, Any], hector_markets: Mapping[str, Any], *,
                      metadata_version: str) -> dict[str, MarketMeta]:
    v1: Mapping[str, Any] = v1_markets["result"]["symbols"]
    out: dict[str, MarketMeta] = {}
    for m in hector_markets["result"]["markets"]:
        sym = m["symbol"]
        if not m.get("is_spot") or m.get("quote_asset") != "TMN" or sym not in v1:
            continue
        mins = v1[sym]
        out[sym] = MarketMeta(
            symbol=sym,
            quote_asset="TMN",
            price_precision=int(m["price_precision"]),
            amount_precision=int(m["amount_precision"]),
            min_qty=_dec(mins["minQty"]),
            min_notional=_dec(mins["minNotional"]),
            market_order_enabled=bool(m.get("is_market_type_enable")) and bool(mins.get("isMarketTypeEnable", True)),
            metadata_version=metadata_version,
        )
    return out


def unavailable_markets(package: Mapping[str, Any]) -> set[str]:
    return set(package["result"]["unavailableMarkets"])


def eligible_symbols(spot: set[str], metadata_available: set[str], package_unavailable: set[str],
                     extra_restrictions: set[str]) -> frozenset[str]:
    return frozenset((spot & metadata_available) - package_unavailable - extra_restrictions)
