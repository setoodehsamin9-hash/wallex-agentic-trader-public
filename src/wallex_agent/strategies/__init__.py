"""Signal Engine (DETERMINISTIC). Versioned candidate generators: C1 breakout (TARGET or TIME exit mode), C2
trend-hold (Phase 04) and F2 momentum with a chandelier trail and deterministic signal exit (Phase 04 round 2).
Each emits fully specified SignalCandidates with fixed stop / optional trail / optional TP / time exits.
`strategy.active` in the run config selects exactly one generator; the B0 selector and the AI choose among its
candidates and can never alter their parameters.
"""

from typing import Protocol, runtime_checkable

from wallex_agent.contracts import FeatureSet, MarketSnapshot, SignalCandidate, StrategyBrief
from wallex_agent.runtime.config import CostModel, ExecutionSettings, FeatureSettings, StrategySettings


class Strategy(Protocol):
    strategy_id: str
    version: str

    def candidates(self, snapshot: MarketSnapshot, features: FeatureSet) -> list[SignalCandidate]: ...


@runtime_checkable
class ExitSignalStrategy(Protocol):
    """A strategy with a deterministic exit rule evaluated at each decision close for an open position."""

    def exit_signal(self, snapshot: MarketSnapshot, features: FeatureSet) -> bool: ...


@runtime_checkable
class BriefingStrategy(Protocol):
    """A strategy that can describe itself to the decision agent (versioned, deterministic, parameter-free for the model)."""

    def brief(self, symbol: str, interval_s: int) -> StrategyBrief: ...


def build_strategy(settings: StrategySettings, costs: CostModel, execution: ExecutionSettings,
                   features: FeatureSettings) -> Strategy:
    if settings.active == "F2_tsm":
        from .f2_tsm import F2Momentum

        assert settings.f2 is not None  # guaranteed by StrategySettings validation
        return F2Momentum(settings.f2, costs, execution, features)
    if settings.active == "C2_trend_hold":
        from .c2_trend_hold import C2TrendHold

        assert settings.c2 is not None  # guaranteed by StrategySettings validation
        return C2TrendHold(settings.c2, costs, execution, trend_bars=features.trend_bars)
    from .c1_breakout import C1Breakout

    return C1Breakout(settings.c1, costs, execution)


def strategy_lookback(settings: StrategySettings, features: FeatureSettings) -> int:
    """Bars of prior-window history the active strategy reads (feature `prior_high` / warm-up / GAP checks)."""
    if settings.active == "F2_tsm":
        return features.ema_bars or features.trend_bars
    return features.trend_bars if settings.active == "C2_trend_hold" else settings.c1.lookback_bars


def strategy_history(settings: StrategySettings, features: FeatureSettings) -> int:
    """Bars of history the active strategy's features need (>= the GAP-checked lookback). regime-r2 ranks compare
    against regime_rank_bars previous values of a regime_er_bars / atr_bars series."""
    depth = strategy_lookback(settings, features) + features.atr_bars + 2
    if settings.active == "F2_tsm" and features.regime_rank_bars is not None and features.regime_er_bars is not None:
        depth = max(depth, features.regime_rank_bars + max(features.regime_er_bars, features.atr_bars) + 2)
    return depth
