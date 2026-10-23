"""Startup warm-up `warmup-1` (Phase 05): native 1h history for slow strategies (F2 needs ~187 days of 4h bars).

Order of sources: (1) the validated local cache (hash-checked dataset directory, written by a previous warm-up) or an
explicitly configured validated seed (e.g. the hash-pinned historical pack); (2) public Wallex `udf/history` hourly
reads for the missing tail only, in bounded 7-day chunks; (3) the live minute stream thereafter (`combine_hours`
prefers complete minute-derived hours and uses native hourly candles only where the minute feed has a gap).

Every bar is closed (forming candle dropped by the strict parser), UTC, hour-aligned, deduplicated (a revised candle
from the exchange wins over the cached value and is counted), never later than `now`, and never invented. The window
must reach back `required_hours`, miss at most 1% of its hours, end within 3 hours of `now`, and come only from
1h-resolution responses; otherwise the report is not ok and callers fail closed. No private API, no credentials.
"""

import shutil
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from wallex_agent.contracts import Contract, QualityFlag, UtcDatetime

from .bars import Bar, WallexPayloadError, aggregate, parse_udf_history
from .dataset import DatasetIntegrityError, load_dataset, write_dataset
from .snapshot import bars_sha256

WARMUP_VERSION = "warmup-1"
HOUR = timedelta(hours=1)
CHUNK = timedelta(days=7)
MAX_MISSING_FRACTION = 0.01
MAX_STALENESS = timedelta(hours=3)


class CandleSource(Protocol):
    def candles(self, symbol: str, resolution: str, frm: int, to: int) -> dict[str, Any]: ...


class WarmupReport(Contract):
    version: Literal["warmup-1"] = WARMUP_VERSION
    symbol: str
    as_of: UtcDatetime
    required_hours: int = Field(gt=0)
    bars: int = Field(ge=0)
    first_open: UtcDatetime | None = None
    last_open: UtcDatetime | None = None
    missing_hours: int = Field(ge=0)
    sources: dict[str, int]
    conflicts: int = Field(ge=0)
    sha256: str
    ok: bool
    reason: str
    notes: tuple[str, ...] = ()
    public_requests: int = Field(ge=0)


class WarmupUnavailable(RuntimeError):
    """The minimum warm-up history could not be established; no session starts on it (fail closed)."""


class HourlyCache:
    """One hash-checked dataset directory per symbol (`<root>/<SYMBOL>/manifest.json`)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self, symbol: str) -> tuple[list[Bar], str | None]:
        d = self.root / symbol
        if not (d / "manifest.json").exists():
            return [], None
        try:
            return list(load_dataset(d, expected_resolution_seconds=3600).bars.get(symbol, [])), None
        except (DatasetIntegrityError, OSError, ValueError, KeyError):
            return [], "CACHE_INVALID"

    def save(self, symbol: str, bars: Sequence[Bar]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp, target = self.root / f".{symbol}.tmp", self.root / symbol
        shutil.rmtree(tmp, ignore_errors=True)
        write_dataset(tmp, f"warmup-{symbol}", {symbol: list(bars)}, extra={"source": WARMUP_VERSION}, resolution_seconds=3600)
        shutil.rmtree(target, ignore_errors=True)  # a crash here only loses the cache: the next start refetches
        tmp.rename(target)


def combine_hours(minute_bars: Sequence[Bar], native_hours: Sequence[Bar]) -> list[Bar]:
    """Complete minute-derived hours win; a native hourly candle fills an hour the minute feed left incomplete."""
    out = {b.ts_open: b for b in native_hours}
    out.update({b.ts_open: b for b in aggregate(list(minute_bars), 3600)})
    return [out[t] for t in sorted(out)]


def _closed_aligned(bars: Sequence[Bar], now: datetime) -> list[Bar]:
    return [b for b in bars if int(b.ts_open.timestamp()) % 3600 == 0 and b.ts_open + HOUR <= now]


def fetch_hourly(client: CandleSource, symbol: str, start: datetime, now: datetime) -> tuple[list[Bar], set[str], int]:
    bars: list[Bar] = []
    notes: set[str] = set()
    calls = 0
    cursor = start
    while cursor < now:
        end = min(now, cursor + CHUNK - timedelta(seconds=1))
        calls += 1
        try:
            got, flags = parse_udf_history(client.candles(symbol, "60", int(cursor.timestamp()), int(end.timestamp())), 3600, now=now)
        except (WallexPayloadError, OSError, KeyError, ValueError):
            notes.add("PUBLIC_ERROR")
            got, flags = [], frozenset[QualityFlag]()
        if QualityFlag.RESOLUTION_MISMATCH in flags:
            notes.add("RESOLUTION_MISMATCH")
        else:
            bars.extend(got)
        cursor = end + timedelta(seconds=1)
    return bars, notes, calls


def bootstrap_hourly(symbol: str, *, client: CandleSource, cache: HourlyCache, seed: Path | None, now: datetime,
                     required_hours: int, overlap_hours: int = 1) -> tuple[list[Bar], WarmupReport]:
    notes: set[str] = set()
    floor = now.replace(minute=0, second=0, microsecond=0)
    start = floor - required_hours * HOUR
    cached, note = cache.load(symbol)
    if note:
        notes.add(note)
    if not cached and seed is not None:
        try:
            cached = list(load_dataset(seed, expected_resolution_seconds=3600).bars.get(symbol, []))
            notes.add("SEED")
        except (DatasetIntegrityError, OSError, ValueError, KeyError):
            notes.add("SEED_INVALID")
    cached = [b for b in _closed_aligned(cached, now) if b.ts_open >= start - CHUNK]
    covers_start = bool(cached) and cached[0].ts_open <= start
    fetch_from = cached[-1].ts_open + HOUR - overlap_hours * HOUR if covers_start else start
    public, fetch_notes, calls = fetch_hourly(client, symbol, fetch_from, now)
    notes |= fetch_notes
    merged = {b.ts_open: ("cache", b) for b in cached}
    conflicts = 0
    for b in _closed_aligned(public, now):
        prior = merged.get(b.ts_open)
        if prior is not None and prior[1] != b:
            conflicts += 1
        merged[b.ts_open] = ("public", b)
    keys = sorted(merged)
    window = [merged[t] for t in keys if t >= start]
    bars = [b for _, b in window]
    expected = int((floor - start) / HOUR)
    missing = max(0, expected - len(bars))
    if "RESOLUTION_MISMATCH" in notes:
        ok, reason = False, "RESOLUTION_MISMATCH"
    elif not bars or bars[0].ts_open > start + timedelta(hours=24):
        ok, reason = False, "INSUFFICIENT_HISTORY"
    elif missing > MAX_MISSING_FRACTION * expected:
        ok, reason = False, "INSUFFICIENT_COVERAGE"
    elif bars[-1].ts_open + HOUR < now - MAX_STALENESS:
        ok, reason = False, "STALE_HISTORY"
    else:
        ok, reason = True, "OK"
    if keys and "RESOLUTION_MISMATCH" not in notes:
        cache.save(symbol, [merged[t][1] for t in keys])
    report = WarmupReport(symbol=symbol, as_of=now.astimezone(UTC), required_hours=required_hours, bars=len(bars),
                          first_open=bars[0].ts_open if bars else None, last_open=bars[-1].ts_open if bars else None,
                          missing_hours=missing, sources={"cache": sum(1 for s, _ in window if s == "cache"),
                                                          "public": sum(1 for s, _ in window if s == "public")},
                          conflicts=conflicts, sha256=bars_sha256(bars) if bars else "", ok=ok, reason=reason,
                          notes=tuple(sorted(notes)), public_requests=calls)
    return bars, report
