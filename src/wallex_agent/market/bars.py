"""OHLCV bars from Wallex `/v1/udf/history` (REST) or `wallex_basic_candle_history` (MCP).

Rules (Phase 01/02 evidence): payload status `s` is checked even when the transport said OK; the still-forming last
bar is dropped; observed spacing is compared with the requested resolution; larger bars are aggregated only from
complete 1m buckets; missing data is flagged, never filled.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from wallex_agent.contracts import QualityFlag


class WallexPayloadError(RuntimeError):
    """Wallex returned an error inside an otherwise successful response."""


@dataclass(frozen=True, slots=True)
class Bar:
    ts_open: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


def _dec(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def parse_udf_history(
    payload: Mapping[str, Any], requested_resolution_s: int, *, now: datetime
) -> tuple[list[Bar], frozenset[QualityFlag]]:
    status = payload.get("s")
    if status == "no_data":
        return [], frozenset()
    if status != "ok":
        raise WallexPayloadError(str(payload.get("errmsg") or payload.get("message") or f"s={status!r}"))
    ts: list[int] = [int(x) for x in payload["t"]]
    cols = [payload[k] for k in ("o", "h", "l", "c", "v")]
    if any(len(col) != len(ts) for col in cols):
        raise WallexPayloadError("candle arrays have different lengths")

    flags: set[QualityFlag] = set()
    rows = sorted(set(zip(ts, *cols, strict=True)), key=lambda r: r[0])
    if len({r[0] for r in rows}) != len(rows):
        raise WallexPayloadError("duplicate candle timestamps with different values")
    spacings = {b[0] - a[0] for a, b in zip(rows, rows[1:])}
    observed = min(spacings) if spacings else requested_resolution_s
    if observed != requested_resolution_s:
        flags.add(QualityFlag.RESOLUTION_MISMATCH)
    if any(s > observed for s in spacings):
        flags.add(QualityFlag.GAP)

    bars: list[Bar] = []
    for t, o, h, lo, c, v in rows:
        if t + observed > now.timestamp():
            continue  # still forming
        bar = Bar(datetime.fromtimestamp(t, UTC), _dec(o), _dec(h), _dec(lo), _dec(c), _dec(v))
        if not (bar.low <= min(bar.open, bar.close) and bar.high >= max(bar.open, bar.close) and bar.volume >= 0):
            raise WallexPayloadError(f"inconsistent OHLC at {t}")
        bars.append(bar)
    return bars, frozenset(flags)


def aggregate(bars: Sequence[Bar], interval_s: int, base_s: int = 60) -> list[Bar]:
    """Aggregate base bars into `interval_s` buckets aligned to the Unix epoch. Incomplete buckets are dropped."""
    if interval_s == base_s:
        return list(bars)
    if interval_s % base_s:
        raise ValueError("interval must be a multiple of the base resolution")
    need = interval_s // base_s
    buckets: dict[int, list[Bar]] = {}
    for bar in bars:
        key = int(bar.ts_open.timestamp()) // interval_s * interval_s
        buckets.setdefault(key, []).append(bar)
    out: list[Bar] = []
    for key in sorted(buckets):
        group = sorted(buckets[key], key=lambda b: b.ts_open)
        if len(group) != need:
            continue
        out.append(Bar(
            datetime.fromtimestamp(key, UTC),
            group[0].open,
            max(b.high for b in group),
            min(b.low for b in group),
            group[-1].close,
            sum((b.volume for b in group), Decimal(0)),
        ))
    return out


def closed_before(bars: Sequence[Bar], as_of: datetime, interval_s: int) -> list[Bar]:
    """Bars whose close time is <= as_of (no look-ahead)."""
    return [b for b in bars if b.ts_open + timedelta(seconds=interval_s) <= as_of]
