"""The owner's LIVE window must be continuous: over days of minutes, entries stay allowed and nothing is flattened
except by the competition end."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from wallex_agent.runtime.calendar import TradingCalendar
from wallex_agent.runtime.config import parse_run_config

REPO = Path(__file__).resolve().parents[1]


def test_live_window_has_no_daily_close() -> None:
    cfg = parse_run_config((REPO / "config" / "live-f2.toml").read_text())
    assert cfg.trading_window is not None
    far = cfg.competition.model_copy(update={"end_utc": None, "start_utc": None})
    cal = TradingCalendar(cfg.trading_window, far, cfg.limits.cutoff)
    t = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
    for _ in range(4 * 24 * 12):  # four days in 5-minute steps
        st = cal.status(t)
        assert st.entries_allowed and not st.must_flatten, (t, st.reason)
        t += timedelta(minutes=5)
