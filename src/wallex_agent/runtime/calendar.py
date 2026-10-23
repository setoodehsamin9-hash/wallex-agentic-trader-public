"""Trading calendar: competition clock + recurring trading windows + no-entry / flatten buffers.

Four separate notions, combined only at the end of `status`:
1. competition start/end (independent of daily windows),
2. recurring allowed trading windows in an IANA timezone,
3. no-new-entry period (before a window closes, before competition end),
4. forced-flatten period (before a window closes, before competition end).
If no trading window is configured the market is treated as always open (BACKTEST/SHADOW only; LIVE refuses).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from .config import CompetitionConfig, CutoffLimits, TradingWindowConfig

_DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


@dataclass(frozen=True, slots=True)
class WindowStatus:
    in_window: bool
    entries_allowed: bool
    orders_allowed: bool
    """Exits/liquidation may be submitted (inside a window and competition not ended)."""
    must_flatten: bool
    reason: str
    window_close: datetime | None
    next_open: datetime | None
    competition_state: Literal["UNBOUNDED", "BEFORE", "ACTIVE", "ENDED"]
    window_version: str | None


class TradingCalendar:
    def __init__(self, window: TradingWindowConfig | None, competition: CompetitionConfig, cutoff: CutoffLimits) -> None:
        self._w = window
        self._c = competition
        self._cut = cutoff
        self._tz = ZoneInfo(window.timezone) if window else None

    def _intervals_around(self, now: datetime) -> list[tuple[datetime, datetime]]:
        assert self._w is not None and self._tz is not None
        local_now = now.astimezone(self._tz)
        out: list[tuple[datetime, datetime]] = []
        for offset in range(-1, 9):
            day = (local_now + timedelta(days=offset)).date()
            name = _DAYS[day.weekday()]
            for spec in self._w.windows:
                if name not in spec.days:
                    continue
                s = datetime.combine(day, time.fromisoformat(spec.start), self._tz)
                e = datetime.combine(day, time.fromisoformat(spec.end), self._tz)
                if e <= s:
                    e += timedelta(days=1)
                out.append((s.astimezone(UTC), e.astimezone(UTC)))
        return sorted(out)

    def _window(self, now: datetime) -> tuple[bool, datetime | None, datetime | None]:
        """(inside, close of current window, next window open after now)."""
        if self._w is None:
            return True, None, None
        intervals = self._intervals_around(now)
        current = [iv for iv in intervals if iv[0] <= now < iv[1]]
        close = max(iv[1] for iv in current) if current else None
        nxt = next((s for s, _ in intervals if s > now and (close is None or s >= close)), None)
        return bool(current), close, nxt

    def status(self, now: datetime) -> WindowStatus:
        inside, close, nxt = self._window(now)
        version = self._w.version if self._w else None
        start, end = self._c.start_utc, self._c.end_utc
        comp: Literal["UNBOUNDED", "BEFORE", "ACTIVE", "ENDED"] = "UNBOUNDED" if start is None and end is None else "ACTIVE"

        if start is not None and now < start:
            open_at = start if self._window(start)[0] else self._window(start)[2]
            return WindowStatus(inside, False, False, False, "COMPETITION_NOT_STARTED", close, open_at, "BEFORE", version)
        if end is not None and now >= end:
            return WindowStatus(inside, False, False, False, "COMPETITION_ENDED", close, None, "ENDED", version)
        if not inside:
            return WindowStatus(False, False, False, False, "OUTSIDE_TRADING_WINDOW", None, nxt, comp, version)

        reason = "OPEN"
        entries, flatten = True, False
        if end is not None:
            left = (end - now).total_seconds()
            if self._cut.liquidation_start_before_end_s is not None and left <= self._cut.liquidation_start_before_end_s:
                entries, flatten, reason = False, True, "COMPETITION_LIQUIDATION"
            elif self._cut.no_new_entry_before_end_s is not None and left <= self._cut.no_new_entry_before_end_s:
                entries, reason = False, "NO_NEW_ENTRY_BEFORE_COMPETITION_END"
        if close is not None and self._w is not None and not flatten:
            left = (close - now).total_seconds()
            if left <= self._w.flatten_before_close_s:
                entries, flatten, reason = False, True, "FLATTEN_BEFORE_WINDOW_CLOSE"
            elif entries and left <= self._w.no_new_entry_before_close_s:
                entries, reason = False, "NO_NEW_ENTRY_BEFORE_WINDOW_CLOSE"
        return WindowStatus(True, entries, True, flatten, reason, close, nxt, comp, version)
