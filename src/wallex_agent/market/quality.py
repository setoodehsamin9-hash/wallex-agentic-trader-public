"""Decision-window data quality `window-quality-1` (Phase 05). One deterministic rule for BACKTEST, SHADOW and LIVE.

Classifies the CLOSED decision bars a cycle would read (the last `needed` buckets before `as_of`):

- STALE            no bar, or the newest bar does not close exactly at `as_of`          -> QualityFlag.STALE (blocks)
- MALFORMED        misaligned / duplicate / unordered timestamps, non-positive prices or impossible OHLC
                                                                                       -> QualityFlag.INCOMPLETE_BAR (blocks)
- COVERAGE_FAILURE more than one missing bucket, two missing in a row, or a missing bucket among the most recent
                   RECENT_REQUIRED buckets (the indicators' live edge)                    -> QualityFlag.GAP (blocks)
- MINOR_GAP        exactly one isolated, older missing bucket: allowed (no flag) and reported, never filled
- OK               complete window

Evidence for the tolerance (reports/phase-05/LIVE_READINESS_FA.md, scripts/phase05_gap_evidence.py): with complete-
bucket aggregation a single missing minute drops a whole 4h bucket, and the previous strict contiguity check then
blocked entries for `needed` buckets (~8 days for F2). Missing data is never interpolated or fabricated here.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from wallex_agent.contracts import QualityFlag

from .bars import Bar

QUALITY_VERSION = "window-quality-1"
MAX_MISSING = 1
RECENT_REQUIRED = 3

Status = Literal["OK", "MINOR_GAP", "COVERAGE_FAILURE", "STALE", "MALFORMED"]


@dataclass(frozen=True)
class WindowQuality:
    status: Status
    missing_buckets: int = 0
    longest_run: int = 0
    detail: str = ""
    version: str = QUALITY_VERSION


def _malformed(bars: Sequence[Bar], interval: int) -> str | None:
    prev: datetime | None = None
    for b in bars:
        if int(b.ts_open.timestamp()) % interval:
            return f"misaligned bar {b.ts_open.isoformat()}"
        if prev is not None and b.ts_open <= prev:
            return f"duplicate or unordered bar {b.ts_open.isoformat()}"
        if min(b.open, b.high, b.low, b.close) <= 0 or b.high < max(b.open, b.close) or b.low > min(b.open, b.close):
            return f"impossible OHLC {b.ts_open.isoformat()}"
        prev = b.ts_open
    return None


def assess_window(bars: Sequence[Bar], as_of: datetime, interval: int, *, needed: int) -> WindowQuality:
    step = timedelta(seconds=interval)
    if not bars or bars[-1].ts_open + step != as_of:
        return WindowQuality("STALE", detail="newest closed bar does not end at the decision time")
    tail = [b for b in bars if b.ts_open >= as_of - needed * step]
    bad = _malformed(tail, interval)
    if bad is not None:
        return WindowQuality("MALFORMED", detail=bad)
    present = {b.ts_open for b in tail}
    expected = [as_of - k * step for k in range(needed, 0, -1)]
    missing = [t not in present for t in expected]
    run = longest = 0
    for m in missing:
        run = run + 1 if m else 0
        longest = max(longest, run)
    n = sum(missing)
    if n == 0:
        return WindowQuality("OK")
    if n <= MAX_MISSING and longest <= 1 and not any(missing[-RECENT_REQUIRED:]):
        return WindowQuality("MINOR_GAP", n, longest, "one isolated older bucket missing (not filled)")
    return WindowQuality("COVERAGE_FAILURE", n, longest, f"{n} missing bucket(s), longest run {longest}")


def quality_flags(q: WindowQuality) -> frozenset[QualityFlag]:
    return {"STALE": frozenset({QualityFlag.STALE}), "MALFORMED": frozenset({QualityFlag.INCOMPLETE_BAR}),
            "COVERAGE_FAILURE": frozenset({QualityFlag.GAP})}.get(q.status, frozenset())
