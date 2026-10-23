"""Round-2 research simulator semantics (scripts/phase04r2_lib.py): TEST guard, fills, trailing stop, fees."""

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _lib() -> ModuleType:
    spec = importlib.util.spec_from_file_location("phase04r2_lib", SCRIPTS / "phase04r2_lib.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase04r2_lib"] = mod
    spec.loader.exec_module(mod)
    return mod


L = _lib()
T0 = 1_700_000_000 - 1_700_000_000 % 3600


def series(rows: Sequence[tuple[float, float, float, float]]) -> object:
    n = len(rows)
    return L.Series("X", 3600, [T0 + i * 3600 for i in range(n)], [r[0] for r in rows], [r[1] for r in rows],
                    [r[2] for r in rows], [r[3] for r in rows], [1.0] * n)


def test_test_window_is_locked() -> None:
    assert L.DEV_END_TS == L.ts("2026-10-13")
    with pytest.raises(RuntimeError, match="TEST window is locked"):
        L.load_series("1h", "BTCTMN", 3600, allow_test=True)


def test_blocked_daily_series_refused() -> None:
    with pytest.raises(RuntimeError, match="BLOCKED"):
        L.load_series("1d", "BTCTMN", 86400)


def test_entry_next_open_and_gap_through_stop_fills_at_worse_open() -> None:
    zero = L.Costs("z", 0.0, 0.0)
    s = series([(100, 100, 100, 100), (100, 101, 99, 100), (80, 81, 79, 80), (80, 80, 80, 80)])
    plan = L.Plan(entry={0: 5.0}, allowed={0: 5.0})
    r = L.simulate(s, plan, L.ExitRule(stop_atr=2, trail_atr=None, max_hold_s=10**9), zero, T0, T0 + 4 * 3600)
    (t,) = r.trades
    assert t.entry_t == T0 + 3600 and t.entry_px == 100  # next bar open, never the signal close
    assert t.reason == "STOP" and t.exit_px == 80  # stop 90 gapped through: worse open


def test_entry_skipped_when_next_open_exceeds_cap() -> None:
    zero = L.Costs("z", 0.0, 0.0)
    s = series([(100, 100, 100, 100), (101, 102, 100, 101), (101, 101, 101, 101)])
    r = L.simulate(s, L.Plan(entry={0: 5.0}, allowed={0: 5.0}), L.ExitRule(2, None, 10**9), zero, T0, T0 + 3 * 3600)
    assert r.trades == []


def test_chandelier_trail_ratchets_up_and_fees_apply_both_sides() -> None:
    c = L.Costs("c", 0.001, 0.0)
    rows = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 110, 100, 110), (110, 110, 103, 104), (104, 104, 104, 104)]
    r = L.simulate(series(rows), L.Plan(entry={0: 2.0}, allowed={0: 2.0}), L.ExitRule(5, 3, 10**9), c, T0, T0 + 5 * 3600)
    (t,) = r.trades
    assert t.reason == "STOP" and t.exit_px == pytest.approx(104.0)  # trail = 110 - 3x2 = 104 > initial 90
    assert t.net == pytest.approx(104 * 0.999 / (100 * 1.001) - 1)


def test_flat_at_slice_end() -> None:
    zero = L.Costs("z", 0.0, 0.0)
    s = series([(100, 100, 100, 100)] * 4)
    r = L.simulate(s, L.Plan(entry={0: 1.0}, allowed={0: 1.0}), L.ExitRule(50, None, 10**9), zero, T0, T0 + 4 * 3600)
    assert [x.reason for x in r.trades] == ["END"] and r.equity[-1][1] == pytest.approx(1.0)
