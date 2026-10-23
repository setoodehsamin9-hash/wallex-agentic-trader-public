"""MarketSnapshot construction. The snapshot id is the content hash, so the same inputs always give the same id."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Literal

from wallex_agent.contracts import BookTop, DataRef, MarketMeta, MarketSnapshot, QualityFlag
from wallex_agent.contracts.hashing import canonical_json, content_id, sha256_hex

from .bars import Bar


def bars_sha256(bars: Sequence[Bar]) -> str:
    rows = [[int(b.ts_open.timestamp()), str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume)] for b in bars]
    return sha256_hex(canonical_json(rows))


def build_snapshot(
    *,
    symbol: str,
    as_of: datetime,
    bar_interval_seconds: int,
    bars: Sequence[Bar],
    book: BookTop | None,
    meta: MarketMeta,
    universe_version: str,
    dataset_id: str,
    source: Literal["WALLEX_REST", "WALLEX_WS", "WALLEX_MCP", "FIXTURE"],
    flags: frozenset[QualityFlag],
) -> MarketSnapshot:
    if not bars:
        raise ValueError("snapshot needs at least one closed bar")
    if bars[-1].ts_open + timedelta(seconds=bar_interval_seconds) > as_of:
        raise ValueError("look-ahead: last bar is not closed at as_of")
    ref = DataRef(
        dataset_id=dataset_id,
        content_sha256=bars_sha256(bars),
        source=source,
        symbol=symbol,
        first_ts=bars[0].ts_open,
        last_ts=bars[-1].ts_open,
        resolution_seconds=bar_interval_seconds,
    )
    draft = MarketSnapshot(
        snapshot_id="",
        symbol=symbol,
        as_of=as_of,
        bar_interval_seconds=bar_interval_seconds,
        last_closed_bar_open=bars[-1].ts_open,
        candles=ref,
        book=book,
        meta=meta,
        universe_version=universe_version,
        quality_flags=frozenset(flags),
    )
    return draft.model_copy(update={"snapshot_id": content_id(draft.model_dump(mode="python", exclude={"snapshot_id"}))})
