"""Decision-window data quality `window-quality-1` (shared by BACKTEST and SHADOW/LIVE): one isolated missing bucket
is tolerated and recorded, real coverage failures / stale feeds / malformed aggregates block, nothing is filled."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from wallex_agent.contracts import QualityFlag
from wallex_agent.market.bars import Bar
from wallex_agent.market.quality import assess_window, quality_flags

IV = 14400
T0 = datetime(2026, 9, 1, tzinfo=UTC)


def bars(n: int, *, skip: tuple[int, ...] = ()) -> list[Bar]:
    return [Bar(T0 + timedelta(seconds=IV * i), Decimal(100), Decimal(101), Decimal(99), Decimal(100), Decimal(1))
            for i in range(n) if i not in skip]


def as_of(n: int) -> datetime:
    return T0 + timedelta(seconds=IV * n)


def test_complete_window_is_ok() -> None:
    q = assess_window(bars(60), as_of(60), IV, needed=49)
    assert q.status == "OK" and q.missing_buckets == 0 and quality_flags(q) == frozenset()


def test_one_isolated_old_missing_bucket_is_tolerated_and_recorded() -> None:
    q = assess_window(bars(60, skip=(30,)), as_of(60), IV, needed=49)
    assert q.status == "MINOR_GAP" and q.missing_buckets == 1 and quality_flags(q) == frozenset()


def test_two_missing_buckets_or_a_recent_one_is_a_coverage_failure() -> None:
    assert assess_window(bars(60, skip=(30, 40)), as_of(60), IV, needed=49).status == "COVERAGE_FAILURE"
    assert assess_window(bars(60, skip=(30, 31)), as_of(60), IV, needed=49).longest_run == 2
    recent = assess_window(bars(60, skip=(58,)), as_of(60), IV, needed=49)
    assert recent.status == "COVERAGE_FAILURE" and quality_flags(recent) == frozenset({QualityFlag.GAP})


def test_missing_last_bar_is_stale() -> None:
    q = assess_window(bars(59), as_of(60), IV, needed=49)
    assert q.status == "STALE" and quality_flags(q) == frozenset({QualityFlag.STALE})
    assert assess_window([], as_of(60), IV, needed=49).status == "STALE"


def test_misaligned_duplicate_or_impossible_bars_are_malformed() -> None:
    b = bars(60)
    shifted = b[:-1] + [Bar(b[-1].ts_open + timedelta(minutes=1), *(b[-1].open, b[-1].high, b[-1].low, b[-1].close, b[-1].volume))]
    assert assess_window(shifted, as_of(60) + timedelta(minutes=1), IV, needed=49).status == "MALFORMED"
    assert assess_window(b[:-1] + [b[-2], b[-1]], as_of(60), IV, needed=49).status == "MALFORMED"
    bad = b[:-1] + [Bar(b[-1].ts_open, Decimal(100), Decimal(99), Decimal(98), Decimal(100), Decimal(1))]  # high < open
    q = assess_window(bad, as_of(60), IV, needed=49)
    assert q.status == "MALFORMED" and quality_flags(q) == frozenset({QualityFlag.INCOMPLETE_BAR})


def test_window_shorter_than_needed_counts_the_unseen_buckets_as_missing() -> None:
    q = assess_window(bars(20), as_of(20), IV, needed=49)
    assert q.status == "COVERAGE_FAILURE" and q.missing_buckets == 29
