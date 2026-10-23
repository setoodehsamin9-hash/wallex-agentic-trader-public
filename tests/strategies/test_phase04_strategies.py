"""Phase 04 strategy families: C1 TIME exit mode (no take-profit, volatility-vs-cost gate) and C2 trend-hold.

Each test names the production rule it pins: a changed gate, a leaked take-profit, a skipped cost check or a lost
trend filter would make one of them fail.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from wallex_agent.features.engine import compute_features
from wallex_agent.runtime.config import C1Params, C2Params, CostModel, StrategySettings
from wallex_agent.strategies import build_strategy, strategy_lookback
from wallex_agent.strategies.c1_breakout import C1Breakout
from wallex_agent.strategies.c2_trend_hold import C2TrendHold

from datetime import UTC, datetime, timedelta

from wallex_agent.contracts import FeatureSet, MarketMeta, MarketSnapshot
from wallex_agent.market.bars import Bar
from wallex_agent.market.snapshot import build_snapshot
from wallex_agent.runtime.config import ExecutionSettings, FeatureSettings

IV = 900
T0 = datetime(2026, 10, 1, tzinfo=UTC)
FS = FeatureSettings(atr_bars=4, trend_bars=6, volume_bars=4, regime_trend_atr=Decimal("1.0"), regime_shock_atr_pct=Decimal("0.05"))
C1 = C1Params(version="c1-test", lookback_bars=4, buffer_atr=Decimal("0.1"), stop_atr=Decimal("1.5"), tp_atr=Decimal("3"),
              max_holding_s=7200, candidate_ttl_s=600, min_edge_over_cost_bps=Decimal("10"))
COSTS = CostModel(fee_rate=Decimal("0.001"), backtest_slippage_bps=Decimal("5"))
EXE = ExecutionSettings(entry_limit_slippage_bps=Decimal("20"), exit_limit_slippage_bps=Decimal("30"), entry_ttl_s=900)
META = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=8, min_qty=Decimal("0.00000001"),
                  min_notional=Decimal(50000), market_order_enabled=True, metadata_version="t")


def bar(i: int, o: int, h: int, lo: int, c: int, v: int = 10) -> Bar:
    return Bar(T0 + timedelta(seconds=IV * i), Decimal(o), Decimal(h), Decimal(lo), Decimal(c), Decimal(v))


def uptrend_breakout() -> list[Bar]:
    """Same shape as tests/strategies/test_c1_features.py: steady up-trend, last bar breaks the prior 4-bar high."""
    return [bar(i, 1000 + 10 * i, 1010 + 10 * i, 990 + 10 * i, 1005 + 10 * i) for i in range(10)] + [bar(10, 1100, 1170, 1095, 1150, 30)]

C1_TIME = C1.model_copy(update={"version": "c1-time-test", "tp_atr": None, "exit_mode": "TIME",
                                "min_atr_cost_ratio": Decimal("1.0")})
C2 = C2Params(version="c2-test", trend_gate=True, entry_trend_atr=Decimal("1.0"), stop_atr=Decimal("2"), max_holding_s=43200,
              candidate_ttl_s=600, min_atr_cost_ratio=Decimal("1.0"))
EXPENSIVE = CostModel(fee_rate=Decimal("0.02"), backtest_slippage_bps=Decimal("5"))  # 410 bps RT > ATR (~293 bps)


def _features(bars: list[Bar]) -> tuple[MarketSnapshot, FeatureSet]:
    snap = build_snapshot(symbol="BTCTMN", as_of=bars[-1].ts_open + timedelta(seconds=IV), bar_interval_seconds=IV, bars=bars,
                          book=None, meta=META, universe_version="u", dataset_id="d", source="FIXTURE", flags=frozenset())
    return snap, compute_features("BTCTMN", snap.snapshot_id, bars, snap.as_of, IV, None, FS, C1.lookback_bars)


def test_c1_time_mode_has_no_take_profit_and_keeps_stop_and_holding() -> None:
    snap, fs = _features(uptrend_breakout())
    [c] = C1Breakout(C1_TIME, COSTS, EXE).candidates(snap, fs)
    assert c.take_profit_price is None and c.stop_price < c.reference_price
    assert c.max_holding_seconds == C1.max_holding_s and c.exit_policy_id == "stop-time-v1"


def test_c1_time_mode_refuses_when_volatility_cannot_pay_costs() -> None:
    snap, fs = _features(uptrend_breakout())
    assert C1Breakout(C1_TIME, EXPENSIVE, EXE).candidates(snap, fs) == []


def test_c1_target_mode_is_unchanged() -> None:
    snap, fs = _features(uptrend_breakout())
    [c] = C1Breakout(C1, COSTS, EXE).candidates(snap, fs)
    assert c.take_profit_price is not None and c.exit_policy_id == "triple-barrier-v1"


def test_c1_time_mode_rejects_a_take_profit_and_target_mode_requires_one() -> None:
    with pytest.raises(ValidationError):
        C1Params.model_validate(C1.model_dump() | {"exit_mode": "TIME"})
    with pytest.raises(ValidationError):
        C1Params.model_validate(C1.model_dump() | {"tp_atr": None})


def test_c2_enters_only_in_an_up_trend_with_positive_momentum() -> None:
    snap, fs = _features(uptrend_breakout())  # close is ~1.85 ATR above SMA(6), 6-bar return > 0
    [c] = C2TrendHold(C2, COSTS, EXE, trend_bars=FS.trend_bars).candidates(snap, fs)
    assert c.strategy_id == "C2_trend_hold" and c.take_profit_price is None
    assert c.stop_price == (c.reference_price - 2 * fs_atr(fs)).to_integral_value(rounding="ROUND_FLOOR")
    strict = C2.model_copy(update={"entry_trend_atr": Decimal("2.0")})
    assert C2TrendHold(strict, COSTS, EXE, trend_bars=FS.trend_bars).candidates(snap, fs) == []


def test_c2_down_trend_gives_no_candidate_but_control_mode_does() -> None:
    bars = [bar(i, 2000 - 10 * i, 2010 - 10 * i, 1990 - 10 * i, 1995 - 10 * i) for i in range(11)]
    snap, fs = _features(bars)
    assert C2TrendHold(C2, COSTS, EXE, trend_bars=FS.trend_bars).candidates(snap, fs) == []
    control = C2Params.model_validate(C2.model_dump() | {"trend_gate": False, "entry_trend_atr": None, "version": "c2-control"})
    [c] = C2TrendHold(control, COSTS, EXE, trend_bars=FS.trend_bars).candidates(snap, fs)
    assert c.exit_policy_id == "stop-time-v1"
    assert C2TrendHold(control, EXPENSIVE, EXE, trend_bars=FS.trend_bars).candidates(snap, fs) == []


def test_strategy_selection_and_lookback() -> None:
    both = StrategySettings(c1=C1, c2=C2, active="C2_trend_hold")
    assert isinstance(build_strategy(both, COSTS, EXE, FS), C2TrendHold)
    assert strategy_lookback(both, FS) == FS.trend_bars
    only_c1 = StrategySettings(c1=C1)
    assert isinstance(build_strategy(only_c1, COSTS, EXE, FS), C1Breakout) and strategy_lookback(only_c1, FS) == C1.lookback_bars
    with pytest.raises(ValidationError):
        StrategySettings(c1=C1, active="C2_trend_hold")


def fs_atr(fs: FeatureSet) -> Decimal:
    value = next(f.value for f in fs.features if f.name == "atr")
    assert value is not None
    return value



def test_c2_control_must_be_explicit() -> None:
    with pytest.raises(ValidationError):  # gate on without a threshold
        C2Params.model_validate(C2.model_dump() | {"entry_trend_atr": None})
    with pytest.raises(ValidationError):  # gate off but a threshold given: ambiguous
        C2Params.model_validate(C2.model_dump() | {"trend_gate": False})
    with pytest.raises(ValidationError):  # omitted flag is never a silent control
        C2Params.model_validate({k: v for k, v in C2.model_dump().items() if k != "trend_gate"})
