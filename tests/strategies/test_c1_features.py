from datetime import UTC, datetime, timedelta
from decimal import Decimal

from wallex_agent.contracts import MarketMeta, MarketSnapshot, RegimeLabel
from wallex_agent.features.engine import compute_features, feature_map
from wallex_agent.market.bars import Bar
from wallex_agent.market.snapshot import build_snapshot
from wallex_agent.runtime.config import C1Params, CostModel, ExecutionSettings, FeatureSettings
from wallex_agent.strategies.c1_breakout import C1Breakout

IV = 900
T0 = datetime(2026, 10, 1, tzinfo=UTC)
FS = FeatureSettings(atr_bars=4, trend_bars=6, volume_bars=4, regime_trend_atr=Decimal("1.0"), regime_shock_atr_pct=Decimal("0.05"))
C1 = C1Params(version="c1-test", lookback_bars=4, buffer_atr=Decimal("0.1"), stop_atr=Decimal("1.5"), tp_atr=Decimal("3"),
              max_holding_s=7200, candidate_ttl_s=600, min_edge_over_cost_bps=Decimal("10"))
COSTS = CostModel(fee_rate=Decimal("0.001"), backtest_slippage_bps=Decimal("5"))
EXE = ExecutionSettings(entry_limit_slippage_bps=Decimal("20"), exit_limit_slippage_bps=Decimal("30"), entry_ttl_s=900)
META = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=8, min_qty=Decimal("0.00000001"),
                  min_notional=Decimal(50000), market_order_enabled=True, metadata_version="t")


def _bar(i: int, o: int, h: int, lo: int, c: int, v: int = 10) -> Bar:
    return Bar(T0 + timedelta(seconds=IV * i), Decimal(o), Decimal(h), Decimal(lo), Decimal(c), Decimal(v))


def _uptrend_breakout(last_high: int = 1170) -> list[Bar]:
    bars = [_bar(i, 1000 + 10 * i, 1010 + 10 * i, 990 + 10 * i, 1005 + 10 * i) for i in range(10)]
    # prior highs: max over bars 6..9 = 1100; last bar closes at 1150 with a long upper wick to last_high
    bars.append(_bar(10, 1100, last_high, 1095, 1150, 30))
    return bars


def _snapshot(bars: list[Bar]) -> MarketSnapshot:
    return build_snapshot(symbol="BTCTMN", as_of=bars[-1].ts_open + timedelta(seconds=IV), bar_interval_seconds=IV,
                          bars=bars, book=None, meta=META, universe_version="u", dataset_id="d", source="FIXTURE",
                          flags=frozenset())


def test_features_ignore_future_bars() -> None:
    bars = _uptrend_breakout()
    as_of = bars[-1].ts_open + timedelta(seconds=IV)
    snap = _snapshot(bars)
    future = [*bars, _bar(11, 1150, 5000, 1, 4000, 999)]
    a = compute_features("BTCTMN", snap.snapshot_id, bars, as_of, IV, None, FS, C1.lookback_bars)
    b = compute_features("BTCTMN", snap.snapshot_id, future, as_of, IV, None, FS, C1.lookback_bars)
    assert a == b


def test_c1_uses_prior_window_high_not_signal_bar() -> None:
    bars = _uptrend_breakout(last_high=1300)  # signal bar's own high is far above its close
    snap = _snapshot(bars)
    fs = compute_features("BTCTMN", snap.snapshot_id, bars, snap.as_of, IV, None, FS, C1.lookback_bars)
    assert feature_map(fs)["BTCTMN.prior_high.4"] == Decimal(1100)
    cands = C1Breakout(C1, COSTS, EXE).candidates(snap, fs)
    assert len(cands) == 1
    c = cands[0]
    assert c.reference_price == Decimal(1150) and c.stop_price < c.reference_price < (c.take_profit_price or 0)
    assert c.max_holding_seconds == 7200 and c.expires_at == snap.as_of + timedelta(seconds=600)
    assert set(c.evidence_feature_ids) <= {f.feature_id for f in fs.features}


def test_c1_skips_when_edge_below_cost() -> None:
    bars = _uptrend_breakout()
    snap = _snapshot(bars)
    fs = compute_features("BTCTMN", snap.snapshot_id, bars, snap.as_of, IV, None, FS, C1.lookback_bars)
    expensive = CostModel(fee_rate=Decimal("0.05"), backtest_slippage_bps=Decimal("5"))
    assert C1Breakout(C1, expensive, EXE).candidates(snap, fs) == []


def test_c1_no_candidate_without_breakout() -> None:
    bars = [_bar(i, 1000, 1010, 990, 1000) for i in range(12)]
    snap = _snapshot(bars)
    fs = compute_features("BTCTMN", snap.snapshot_id, bars, snap.as_of, IV, None, FS, C1.lookback_bars)
    assert fs.deterministic_regime is RegimeLabel.RANGE
    assert C1Breakout(C1, COSTS, EXE).candidates(snap, fs) == []


def test_candidate_id_deterministic() -> None:
    bars = _uptrend_breakout()
    snap = _snapshot(bars)
    fs = compute_features("BTCTMN", snap.snapshot_id, bars, snap.as_of, IV, None, FS, C1.lookback_bars)
    s = C1Breakout(C1, COSTS, EXE)
    assert s.candidates(snap, fs)[0].candidate_id == s.candidates(snap, fs)[0].candidate_id


def test_insufficient_history_is_uncertain() -> None:
    bars = [_bar(i, 1000, 1010, 990, 1000) for i in range(3)]
    snap = _snapshot(bars)
    fs = compute_features("BTCTMN", snap.snapshot_id, bars, snap.as_of, IV, None, FS, C1.lookback_bars)
    assert fs.deterministic_regime is RegimeLabel.UNCERTAIN
    assert C1Breakout(C1, COSTS, EXE).candidates(snap, fs) == []
