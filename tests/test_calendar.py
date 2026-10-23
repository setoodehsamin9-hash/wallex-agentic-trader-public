from datetime import UTC, datetime, timedelta

from wallex_agent.runtime.calendar import TradingCalendar
from wallex_agent.runtime.config import CompetitionConfig, CutoffLimits, TradingWindowConfig, WindowSpec

# A deliberately arbitrary test window (NOT the owner's hours): 10:00-22:00 local, every day, in UTC+3:30.
TZ = "Asia/Tehran"
TW = TradingWindowConfig(
    version="tw-test-1",
    timezone=TZ,
    windows=(WindowSpec(days=("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"), start="10:00", end="22:00"),),
    no_new_entry_before_close_s=1800,
    flatten_before_close_s=900,
)
NO_CUTOFF = CutoffLimits(no_new_entry_before_end_s=3600, liquidation_start_before_end_s=1800)


def _utc(h: int, m: int = 0, day: int = 22) -> datetime:
    # Tehran is UTC+3:30 on these dates.
    return datetime(2026, 10, day, h, m, tzinfo=UTC)


def test_unconfigured_window_is_always_open() -> None:
    cal = TradingCalendar(None, CompetitionConfig(), NO_CUTOFF)
    st = cal.status(_utc(2))
    assert st.entries_allowed and st.orders_allowed and not st.must_flatten


def test_inside_window_entries_allowed() -> None:
    st = TradingCalendar(TW, CompetitionConfig(), NO_CUTOFF).status(_utc(10))  # 13:30 local
    assert st.in_window and st.entries_allowed and not st.must_flatten
    assert st.window_close == _utc(18, 30)  # 22:00 local


def test_no_new_entry_buffer_before_close() -> None:
    st = TradingCalendar(TW, CompetitionConfig(), NO_CUTOFF).status(_utc(18, 10))  # 21:40 local
    assert st.in_window and not st.entries_allowed and not st.must_flatten
    assert st.reason == "NO_NEW_ENTRY_BEFORE_WINDOW_CLOSE"


def test_flatten_buffer_before_close() -> None:
    st = TradingCalendar(TW, CompetitionConfig(), NO_CUTOFF).status(_utc(18, 20))  # 21:50 local
    assert st.must_flatten and st.orders_allowed and not st.entries_allowed


def test_outside_window_reports_next_open() -> None:
    st = TradingCalendar(TW, CompetitionConfig(), NO_CUTOFF).status(_utc(20))  # 23:30 local
    assert not st.in_window and not st.entries_allowed and not st.orders_allowed
    assert st.reason == "OUTSIDE_TRADING_WINDOW"
    assert st.next_open == _utc(6, 30, day=23)  # 10:00 local next day


def test_window_crossing_midnight() -> None:
    tw = TW.model_copy(update={"windows": (WindowSpec(days=("THU",), start="20:00", end="02:00"),)})
    cal = TradingCalendar(tw, CompetitionConfig(), NO_CUTOFF)
    # 2026-10-22 is a Thursday. 23:30 local Thursday -> inside; 01:00 local Friday -> inside (started Thursday).
    assert cal.status(_utc(20)).in_window
    assert cal.status(_utc(21, 30)).in_window
    assert not cal.status(_utc(23)).in_window  # 02:30 local Friday


def test_competition_cutoff_independent_of_window() -> None:
    comp = CompetitionConfig(start_utc=_utc(6), end_utc=_utc(12))
    cal = TradingCalendar(TW, comp, NO_CUTOFF)
    assert cal.status(_utc(10, 30)).entries_allowed
    st = cal.status(_utc(11, 15))  # 45 min before end: inside no-entry (60 min), inside liquidation (30 min)? no
    assert not st.entries_allowed and not st.must_flatten and st.reason == "NO_NEW_ENTRY_BEFORE_COMPETITION_END"
    st = cal.status(_utc(11, 40))
    assert st.must_flatten and st.reason == "COMPETITION_LIQUIDATION"
    st = cal.status(_utc(12, 0) + timedelta(seconds=1))
    assert st.competition_state == "ENDED" and not st.orders_allowed


def test_before_competition_start() -> None:
    comp = CompetitionConfig(start_utc=_utc(8), end_utc=_utc(12, day=25))
    st = TradingCalendar(TW, comp, NO_CUTOFF).status(_utc(7))
    assert st.competition_state == "BEFORE" and not st.entries_allowed and st.next_open == _utc(8)
