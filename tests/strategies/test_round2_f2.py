"""Phase 04 round 2: F2 time-series momentum in the kernel (chandelier trail, signal exit, regime-r2 features, 1h base).

Each test pins a safety or fidelity rule: the trail only ratchets up and never loosens the approved stop; the signal
exit is a deterministic protective exit on the normal policy/risk path; the new features exist only when configured
(old feature sets and hashes unchanged); the backtest honours the dataset's base resolution.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from support.synthetic import REPO, metadata_dir

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.contracts import FeatureSet, FeatureValue, MarketMeta, MarketSnapshot, OrderPurpose, RegimeLabel
from wallex_agent.execution.exits import action_kind, advance_high_water, effective_stop, pick_bar_exit, pick_book_exit
from wallex_agent.features.engine import compute_features
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import Bar
from wallex_agent.market.book import OrderBook
from wallex_agent.market.dataset import load_dataset, write_dataset
from wallex_agent.market.snapshot import build_snapshot
from wallex_agent.policy import ActionKind
from wallex_agent.portfolio.ledger import ExitPlan
from wallex_agent.recovery.state_log import plan_from_json, plan_json
from wallex_agent.risk.engine import EXIT_PURPOSES
from wallex_agent.runtime.backtest import run_backtest
from wallex_agent.runtime.config import CostModel, ExecutionSettings, F2Params, FeatureSettings, RunConfig, StrategySettings, load_run_config
from wallex_agent.strategies import build_strategy, strategy_history, strategy_lookback
from wallex_agent.strategies.f2_tsm import F2Momentum

T = datetime(2026, 10, 1, tzinfo=UTC)
IV = 14400
META = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=8, min_qty=Decimal("0.00000001"),
                  min_notional=Decimal(50000), market_order_enabled=True, metadata_version="t")
COSTS = CostModel(fee_rate=Decimal("0.0025"), backtest_slippage_bps=Decimal("5"))
EXE = ExecutionSettings(entry_limit_slippage_bps=Decimal("20"), exit_limit_slippage_bps=Decimal("30"), entry_ttl_s=900)
FS = FeatureSettings(atr_bars=2, trend_bars=3, volume_bars=2, regime_trend_atr=Decimal("1"), regime_shock_atr_pct=Decimal("0.5"))
FS2 = FS.model_copy(update={"ema_bars": 3, "regime_er_bars": 3, "regime_rank_bars": 4})
F2 = F2Params(version="f2-test", entry_band_atr=Decimal("0.5"), stop_atr=Decimal("5"), trail_atr=Decimal("5"),
              max_holding_s=604800, candidate_ttl_s=600, regime_filter=False)


def _plan(**kw: Any) -> ExitPlan:
    return ExitPlan("c", Decimal(90), None, T + timedelta(days=7), **kw)


def _bar(o: int, h: int, lo: int, c: int, i: int = 0) -> Bar:
    return Bar(T + timedelta(seconds=IV * i), Decimal(o), Decimal(h), Decimal(lo), Decimal(c), Decimal(1))


# ------------------------------------------------------------------------------------------- exits (kernel)
def test_trail_ratchets_from_previous_high_water_and_never_loosens_the_approved_stop() -> None:
    plan = _plan(trail_distance=Decimal(6))
    assert effective_stop(plan, None) == 90 and effective_stop(plan, Decimal(92)) == 90  # never below initial stop
    assert effective_stop(plan, Decimal(110)) == 104
    assert pick_bar_exit(plan, _bar(106, 107, 105, 106), now=T, high_water=Decimal(110)) is None
    trig = pick_bar_exit(plan, _bar(106, 107, 103, 104), now=T, high_water=Decimal(110))
    assert trig == (OrderPurpose.EXIT_STOP, Decimal(104))
    assert effective_stop(_plan(), Decimal(500)) == 90  # no trail configured -> fixed stop


def test_high_water_advances_after_the_check_and_only_upwards() -> None:
    assert advance_high_water(None, _bar(100, 105, 99, 104)) == 105
    assert advance_high_water(Decimal(110), _bar(100, 105, 99, 104)) == 110
    book = OrderBook(bids=((Decimal(120), Decimal(1)),), asks=((Decimal(121), Decimal(1)),), received_at=T)
    assert advance_high_water(Decimal(110), book) == 120


def test_signal_exit_has_its_own_purpose_after_stop_and_on_the_protective_path() -> None:
    plan = _plan(signal_exit=True)
    assert pick_bar_exit(plan, _bar(100, 101, 99, 100), now=T) == (OrderPurpose.EXIT_SIGNAL, Decimal(100))
    assert pick_bar_exit(plan, _bar(100, 101, 89, 95), now=T) == (OrderPurpose.EXIT_STOP, Decimal(90))
    book = OrderBook(bids=((Decimal(99), Decimal(1)),), asks=((Decimal(100), Decimal(1)),), received_at=T)
    assert pick_book_exit(plan, book, now=T) == (OrderPurpose.EXIT_SIGNAL, Decimal(99))
    assert OrderPurpose.EXIT_SIGNAL in EXIT_PURPOSES and action_kind(OrderPurpose.EXIT_SIGNAL) is ActionKind.PROTECTIVE_EXIT
    assert action_kind(OrderPurpose.EXIT_AI) is ActionKind.AI_EXIT and action_kind(OrderPurpose.KILL) is ActionKind.LIQUIDATION


def test_plan_persistence_keeps_trail_and_reads_legacy_plans() -> None:
    plan = _plan(trail_distance=Decimal("6.5"), signal_exit=True)
    assert plan_from_json(plan_json(plan)) == plan
    legacy = {"candidate_id": "c", "stop_price": "90", "take_profit_price": None,
              "deadline": (T + timedelta(days=7)).isoformat()}
    assert plan_from_json(legacy) == _plan()


# ------------------------------------------------------------------------------------------------ features
def _closes(cs: list[int]) -> list[Bar]:
    return [Bar(T + timedelta(seconds=IV * i), Decimal(c), Decimal(c + 2), Decimal(c - 2), Decimal(c), Decimal(1))
            for i, c in enumerate(cs)]


def _fs(bars: list[Bar], settings: FeatureSettings) -> tuple[MarketSnapshot, FeatureSet]:
    as_of = bars[-1].ts_open + timedelta(seconds=IV)
    snap = build_snapshot(symbol="BTCTMN", as_of=as_of, bar_interval_seconds=IV, bars=bars, book=None, meta=META,
                          universe_version="u", dataset_id="d", source="FIXTURE", flags=frozenset())
    return snap, compute_features("BTCTMN", snap.snapshot_id, bars, as_of, IV, None, settings, 3)


def test_round2_features_exist_only_when_configured() -> None:
    bars = _closes([100, 104, 101, 106, 103, 108, 107, 112, 110, 115])
    _, old = _fs(bars, FS)
    assert old.feature_engine_version == "features-1.0.0" and not any(".ema." in f.feature_id for f in old.features)
    _, new = _fs(bars, FS2)
    f = {x.feature_id: x.value for x in new.features}
    assert new.feature_engine_version == "features-1.1.0"
    e = 100.0
    for c in [104, 101, 106, 103, 108, 107, 112, 110, 115]:
        e = c * 0.5 + e * 0.5
    assert float(f["BTCTMN.ema.3"] or 0) == pytest.approx(e)
    assert f["BTCTMN.close_lag.3"] == Decimal(107)
    # ER(3) = |115-107| / (|112-107| + |110-112| + |115-110|) = 8/12 vs the previous 4 ERs (all < 8/12 here)
    ers = [abs(bars[i].close - bars[i - 3].close) / sum(abs(bars[j].close - bars[j - 1].close) for j in range(i - 2, i + 1))
           for i in range(3, 10)]
    assert f["BTCTMN.er_rank.3"] == Decimal(sum(1 for x in ers[-5:-1] if x < ers[-1])) / 4
    assert f["BTCTMN.vol_rank.2"] is not None and Decimal(0) <= (f["BTCTMN.vol_rank.2"] or Decimal(0)) <= 1


# ------------------------------------------------------------------------------------------------ strategy
def _manual(**v: Decimal | None) -> tuple[MarketSnapshot, FeatureSet]:
    bars = _closes([100, 101, 102, 103, 104])
    snap, _ = _fs(bars, FS)
    vals = {"close.1": Decimal(110), "atr.2": Decimal(4), "ema.3": Decimal(100), "close_lag.3": Decimal(105),
            "er_rank.3": Decimal("0.7"), "vol_rank.2": Decimal("0.5"), **v}
    feats = tuple(FeatureValue(feature_id=f"BTCTMN.{k}", name=k.split(".")[0], value=x, unit="", window_bars=0)
                  for k, x in vals.items())
    return snap, FeatureSet(snapshot_id=snap.snapshot_id, feature_engine_version="features-1.1.0", features=feats,
                            deterministic_regime=RegimeLabel.RANGE)


def _f2(regime: bool) -> F2Momentum:
    return F2Momentum(F2.model_copy(update={"regime_filter": regime}), COSTS, EXE, FS2)


def test_f2_candidate_has_wide_atr_stop_trail_and_no_take_profit() -> None:
    snap, fs = _manual()
    [c] = _f2(False).candidates(snap, fs)
    assert c.reference_price == 110 and c.stop_price == 110 - 5 * 4 and c.trail_distance == 20
    assert c.take_profit_price is None and c.max_holding_seconds == 604800 and c.exit_policy_id == "chandelier-signal-v1"


@pytest.mark.parametrize("override", [{"close.1": Decimal(101)},  # below EMA + 0.5 ATR
                                      {"close_lag.3": Decimal(111)},  # non-positive lag return
                                      {"ema.3": None}])
def test_f2_entry_rule_fails_closed(override: dict[str, Decimal | None]) -> None:
    snap, fs = _manual(**{k: v for k, v in override.items()})
    assert _f2(False).candidates(snap, fs) == []


@pytest.mark.parametrize(("vol", "er", "lag", "allowed"), [
    ("0.96", "0.7", "105", False),  # SHOCK
    ("0.20", "0.7", "105", False),  # QUIET
    ("0.5", "0.7", "105", True),  # TREND_UP (close 110 > close 3 bars ago 105)
    ("0.5", "0.3", "105", True),  # RANGE
])
def test_f2_regime_filter_allows_only_trend_up_and_range(vol: str, er: str, lag: str, allowed: bool) -> None:
    snap, fs = _manual(**{"vol_rank.2": Decimal(vol), "er_rank.3": Decimal(er), "close_lag.3": Decimal(lag)})
    assert bool(_f2(True).candidates(snap, fs)) is allowed
    snap, fs = _manual(**{"vol_rank.2": None})
    assert _f2(True).candidates(snap, fs) == [] and _f2(False).candidates(snap, fs) != []


def test_f2_regime_filter_blocks_down_trend() -> None:
    f2 = F2Momentum(F2.model_copy(update={"regime_filter": True}), COSTS, EXE,
                    FS2.model_copy(update={"regime_er_bars": 4}))  # regime lag window differs from the EMA window
    snap, fs = _manual(**{"close_lag.4": Decimal(115), "er_rank.4": Decimal("0.7")})
    assert f2.candidates(snap, fs) == []


def test_f2_exit_signal_when_close_below_ema() -> None:
    snap, fs = _manual(**{"close.1": Decimal(99)})
    assert _f2(False).exit_signal(snap, fs) is True
    snap, fs = _manual()
    assert _f2(False).exit_signal(snap, fs) is False


def test_f2_build_refuses_missing_feature_settings_and_reports_history() -> None:
    s = StrategySettings(c1=load_run_config(REPO / "config/backtest.toml").strategy.c1, f2=F2, active="F2_tsm")  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        build_strategy(s, COSTS, EXE, FS)
    assert strategy_lookback(s, FS2) == 3
    assert strategy_history(s, FS2) >= 4 + 3 + 2


# ------------------------------------------------------------------------------------- backtest on 1h base
def _hourly(days: int) -> list[Bar]:
    out: list[Bar] = []
    p = 1e8
    for h in range(days * 24):
        drift = 0.004 if (h // 72) % 2 == 0 else -0.0006  # 3-day up legs, 3-day slow fades: EMA exit inside the cap
        o = p
        p = p * (1 + drift + 0.002 * ((h * 7) % 5 - 2) / 2)
        out.append(Bar(datetime(2026, 7, 1, tzinfo=UTC) + timedelta(hours=h), Decimal(round(o)),
                       Decimal(round(max(o, p) * 1.002)), Decimal(round(min(o, p) * 0.998)), Decimal(round(p)), Decimal(2)))
    return out


def _f2_cfg() -> RunConfig:
    raw = load_run_config(REPO / "config/backtest.toml").model_dump(mode="python")
    # wide stop/trail (30 ATR) so the deterministic EMA exit, not the trail, closes trades in this synthetic series
    raw["strategy"]["f2"] = {**F2.model_dump(mode="python"), "stop_atr": Decimal(30), "trail_atr": Decimal(30)}
    raw["strategy"]["active"] = "F2_tsm"
    raw["run"]["bar_interval_seconds"] = IV
    raw["features"].update({"atr_bars": 14, "trend_bars": 24, "volume_bars": 20, "ema_bars": 12})
    raw["limits"]["cutoff"] = {"no_new_entry_before_end_s": IV, "liquidation_start_before_end_s": 2 * 3600}
    raw["backtest"]["dataset"] = "hourly-test"
    return RunConfig.model_validate(raw)


def test_backtest_runs_f2_on_an_hourly_base_dataset(tmp_path: Path) -> None:
    d = tmp_path / "ds"
    write_dataset(d, "hourly-test", {"BTCTMN": _hourly(60)}, extra={"note": "synthetic"}, resolution_seconds=3600)
    metadata_dir(d / "metadata")
    cfg = _f2_cfg()
    res = run_backtest(cfg, load_dataset(d, expected_resolution_seconds=3600), JournalStore(tmp_path / "j.sqlite"),
                       BaselineSelector(), run_id="f2", period=(datetime(2026, 7, 10, tzinfo=UTC), datetime(2026, 8, 30, tzinfo=UTC)))
    rt = res.runtime
    assert rt.strategy.strategy_id == "F2_tsm" and not rt.ledger.positions  # TMN-flat at the end
    reasons = {t.exit_reason for t in rt.ledger.trades}
    assert rt.ledger.trades and reasons <= {"EXIT_STOP", "EXIT_SIGNAL", "EXIT_TIME", "LIQUIDATION"}
    assert "EXIT_SIGNAL" in reasons  # the deterministic signal exit really ran through policy + risk + venue
    signals = [e for e in rt.store.risk_events("f2") if e["kind"] == "EXIT_SIGNAL"]
    assert signals and all(e["strategy_version"] == "f2-test" and e["decision_id"] for e in signals)  # journaled
    entries = [o for o in rt.execution.orders() if o.intent.purpose is OrderPurpose.ENTRY]
    assert entries and all(o.plan is not None and o.plan.trail_distance is not None for o in entries)


def test_backtest_refuses_cutoff_buffers_shorter_than_two_base_bars(tmp_path: Path) -> None:
    d = tmp_path / "ds"
    write_dataset(d, "hourly-test", {"BTCTMN": _hourly(30)}, extra={"note": "synthetic"}, resolution_seconds=3600)
    metadata_dir(d / "metadata")
    raw = _f2_cfg().model_dump(mode="python")
    raw["limits"]["cutoff"]["liquidation_start_before_end_s"] = 1800  # < 2 hourly bars: could end holding a position
    with pytest.raises(ValueError, match="cutoff"):
        run_backtest(RunConfig.model_validate(raw), load_dataset(d, expected_resolution_seconds=3600),
                     JournalStore(tmp_path / "j.sqlite"), BaselineSelector(), run_id="f2-cut")


def test_harness_split_coverage_uses_the_dataset_resolution(tmp_path: Path) -> None:
    from wallex_agent.experiments.runner import _split_metadata  # pyright: ignore[reportPrivateUsage]
    from wallex_agent.experiments.spec import TimeSplit

    d = tmp_path / "ds"
    bars = _hourly(3)
    write_dataset(d, "hourly-test", {"BTCTMN": bars[:30] + bars[31:]}, extra={"note": "synthetic"}, resolution_seconds=3600)
    metadata_dir(d / "metadata")
    ds = load_dataset(d, expected_resolution_seconds=3600)
    end = bars[-1].ts_open + timedelta(hours=1)  # ends exactly at the close of the last hourly bar: covered
    meta = _split_metadata(TimeSplit(name="x", role="TEST", start_utc=bars[0].ts_open, end_utc=end), ds, ("BTCTMN",), IV)
    assert meta["counts"]["BTCTMN"] == {"bars": 71, "missing_minutes": 60}
    with pytest.raises(ValueError, match="coverage"):
        _split_metadata(TimeSplit(name="y", role="TEST", start_utc=bars[0].ts_open, end_utc=end + timedelta(hours=4)),
                        ds, ("BTCTMN",), IV)
