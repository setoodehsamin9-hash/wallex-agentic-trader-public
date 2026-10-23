"""Market data contracts produced by the Market Context Builder and Feature Engine.

Prices and quantities are Decimal end to end; floats never touch money or precision checks.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field

from .base import SCHEMA_VERSION, Contract, UtcDatetime


class QualityFlag(StrEnum):
    STALE = "STALE"
    GAP = "GAP"
    INCOMPLETE_BAR = "INCOMPLETE_BAR"
    RESOLUTION_MISMATCH = "RESOLUTION_MISMATCH"
    """Requested resolution differs from observed spacing (seen in Phase 01: resolution=15 returned 1m bars)."""
    CROSSED_BOOK = "CROSSED_BOOK"
    ONE_SIDED_BOOK = "ONE_SIDED_BOOK"
    SYMBOL_NOT_ELIGIBLE = "SYMBOL_NOT_ELIGIBLE"


class RegimeLabel(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE = "RANGE"
    SHOCK = "SHOCK"
    UNCERTAIN = "UNCERTAIN"


class DataRef(Contract):
    """Points at the exact data a decision saw, so replay reads the same bytes."""

    dataset_id: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["WALLEX_REST", "WALLEX_WS", "WALLEX_MCP", "FIXTURE"]
    symbol: str
    first_ts: UtcDatetime
    last_ts: UtcDatetime
    resolution_seconds: int = Field(gt=0)


class BookTop(Contract):
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    bid_qty: Decimal = Field(ge=0)
    ask_qty: Decimal = Field(ge=0)
    received_at: UtcDatetime
    exchange_ts: UtcDatetime | None = None


class MarketMeta(Contract):
    symbol: str
    quote_asset: Literal["TMN"]
    price_precision: int = Field(ge=0)
    amount_precision: int = Field(ge=0)
    min_qty: Decimal = Field(ge=0)
    min_notional: Decimal = Field(ge=0)
    market_order_enabled: bool
    metadata_version: str


class MarketSnapshot(Contract):
    """Everything known at decision time t. Built only from bars closed at or before `as_of`."""

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    snapshot_id: str
    """Content hash of this snapshot's canonical JSON; decisions reference it."""
    symbol: str
    as_of: UtcDatetime
    bar_interval_seconds: int = Field(gt=0)
    last_closed_bar_open: UtcDatetime
    candles: DataRef
    book: BookTop | None
    meta: MarketMeta
    universe_version: str
    quality_flags: frozenset[QualityFlag] = frozenset()


class FeatureValue(Contract):
    feature_id: str
    """Stable id the AI cites as evidence, e.g. 'BTCTMN.breakout_margin_atr.20'."""
    name: str
    value: Decimal | None
    unit: str
    window_bars: int = Field(ge=0)


class FeatureSet(Contract):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    snapshot_id: str
    feature_engine_version: str
    features: tuple[FeatureValue, ...]
    deterministic_regime: RegimeLabel
    """Rule-based regime label. The AI may disagree in its own assessment; both are journaled."""
