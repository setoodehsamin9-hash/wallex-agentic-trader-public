"""What a decision provider is given for one cycle. Frozen; everything the model may cite is in here."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from wallex_agent.contracts import DecisionProvenance, FeatureSet, MarketSnapshot, SignalCandidate, StrategyBrief, ValidatedDecision


@dataclass(frozen=True)
class PositionView:
    position_id: str
    symbol: str
    qty: str
    avg_cost_tmn: str
    stop_price: str
    take_profit_price: str | None
    deadline: str
    unrealized_pnl_tmn: str


@dataclass(frozen=True)
class DecisionContext:
    snapshot: MarketSnapshot
    features: FeatureSet
    candidates: tuple[SignalCandidate, ...]
    positions: tuple[PositionView, ...]
    risk_status: dict[str, Any]
    recent_decisions: tuple[dict[str, Any], ...]
    now: datetime
    deadline: datetime
    run_id: str = ""
    decision_key: str = ""
    """Stable key (run, symbol, as_of) used to record/replay this decision."""
    strategy_brief: StrategyBrief | None = None
    """Active strategy's versioned brief (F2). None keeps the pre-Phase-05 prompt rendering unchanged."""


class DecisionProvider(Protocol):
    source_name: str

    def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
        """Never raises for model faults: every failure is a ValidatedDecision with effective_action WAIT."""
        ...
