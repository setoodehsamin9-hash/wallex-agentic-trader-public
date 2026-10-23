"""Performance metrics for a run, computed only from the ledger (recorded fills) and the decision journal.

The competition result is realized TMN: `realized_pnl_tmn` and `realized_return_pct` are the headline numbers;
equity-based figures (incl. unrealized) are risk metrics. Nothing here estimates or extrapolates profit.
"""

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from pydantic import BaseModel, ConfigDict

from wallex_agent.contracts import CycleStatus, DecisionRecord, DecisionSource, ValidationStatus
from wallex_agent.portfolio.ledger import Ledger

ZERO = Decimal(0)
HUNDRED = Decimal(100)


class PerformanceSummary(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    run_id: str
    environment: str
    autonomy: str
    label: str = ""
    period_start: str | None = None
    period_end: str | None = None
    starting_capital_tmn: Decimal
    final_equity_tmn: Decimal
    cash_tmn: Decimal
    realized_pnl_tmn: Decimal
    unrealized_pnl_tmn: Decimal
    fees_tmn: Decimal
    net_pnl_tmn: Decimal
    realized_return_pct: Decimal
    return_pct: Decimal
    peak_equity_tmn: Decimal
    max_drawdown_pct: Decimal
    trades: int
    wins: int
    losses: int
    win_rate_pct: Decimal | None
    avg_trade_pnl_tmn: Decimal | None
    breakeven_trades: int = 0
    gross_profit_tmn: Decimal = ZERO
    gross_loss_tmn: Decimal = ZERO
    """Positive magnitudes of winning/losing CLOSED trade net P&L, after fees (not pre-fee turnover)."""
    profit_factor: Decimal | None = None
    """gross_profit / gross_loss; undefined (null), including all-wins, when loss is zero."""
    fee_burden_pct: Decimal = ZERO
    """All paid fees / starting capital * 100. Does not imply profitability."""
    avg_holding_s: Decimal | None = None
    time_in_market_pct: Decimal | None = None
    """Union of position holding intervals / evaluation duration, not summed across symbols."""
    open_positions: list[dict[str, str]]
    exposure_tmn: Decimal
    dust: dict[str, str]
    decisions_total: int
    no_candidate_cycles: int
    wait_decisions: int
    proposed: int
    awaiting_approval: int
    risk_rejected: int
    blocked: int
    outside_window_or_cutoff: int
    entries_submitted: int
    ai_calls: int
    ai_valid: int
    ai_invalid_by_status: dict[str, int]
    ai_latency_ms_p50: int | None
    ai_latency_ms_p95: int | None
    ai_latency_ms_max: int | None
    execution_errors: int
    unresolved_orders: int
    kill_switch_latched: bool
    top_reason_codes: list[tuple[str, int]]
    top_risk_blocks: list[tuple[str, int]]
    model_id: str | None = None
    model_provider: str | None = None
    responding_models: list[str] = []
    strategy_versions: list[str] = []

    @classmethod
    def empty(cls, *, run_id: str, environment: str, autonomy: str, starting_capital_tmn: Decimal) -> "PerformanceSummary":
        z = ZERO
        return cls(run_id=run_id, environment=environment, autonomy=autonomy, starting_capital_tmn=starting_capital_tmn,
                   final_equity_tmn=starting_capital_tmn, cash_tmn=starting_capital_tmn, realized_pnl_tmn=z,
                   unrealized_pnl_tmn=z, fees_tmn=z, net_pnl_tmn=z, realized_return_pct=z, return_pct=z,
                   peak_equity_tmn=starting_capital_tmn, max_drawdown_pct=z, trades=0, wins=0, losses=0, win_rate_pct=None,
                   avg_trade_pnl_tmn=None, open_positions=[], exposure_tmn=z, dust={}, decisions_total=0,
                   no_candidate_cycles=0, wait_decisions=0, proposed=0, awaiting_approval=0, risk_rejected=0, blocked=0,
                   outside_window_or_cutoff=0, entries_submitted=0, ai_calls=0, ai_valid=0, ai_invalid_by_status={},
                   ai_latency_ms_p50=None, ai_latency_ms_p95=None, ai_latency_ms_max=None, execution_errors=0,
                   unresolved_orders=0, kill_switch_latched=False, top_reason_codes=[], top_risk_blocks=[])


def _pct(x: Decimal) -> Decimal:
    return (x * HUNDRED).quantize(Decimal("0.0001"))


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def _seconds(span: timedelta) -> Decimal:
    return Decimal(span.days * 86400 + span.seconds) + Decimal(span.microseconds) / 1000000


def _holding(ledger: Ledger, period: tuple[str | None, str | None]) -> tuple[Decimal | None, Decimal | None]:
    intervals = [(t.opened_at, t.closed_at) for t in ledger.trades]
    if any(a.utcoffset() != timedelta(0) or b.utcoffset() != timedelta(0) or b < a for a, b in intervals):
        raise ValueError("invalid trade holding interval")
    average = sum((_seconds(b - a) for a, b in intervals), ZERO) / len(intervals) if intervals else None
    if period[0] is None or period[1] is None:
        return average, None
    start, end = datetime.fromisoformat(period[0]), datetime.fromisoformat(period[1])
    if start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0) or start >= end:
        raise ValueError("invalid evaluation period")
    intervals += [(p.opened_at, end) for p in ledger.positions.values()]
    clipped = sorted((max(a, start), min(b, end)) for a, b in intervals if b > start and a < end)
    duration, cursor = ZERO, start
    for a, b in clipped:
        if b > cursor:
            duration += _seconds(b - max(a, cursor))
            cursor = b
    return average, _pct(duration / _seconds(end - start))


def compute_summary(*, run_id: str, environment: str, autonomy: str, label: str, starting_capital_tmn: Decimal,
                    ledger: Ledger, marks: dict[str, Decimal], fee_rate: Decimal, decisions: list[DecisionRecord],
                    execution_errors: int, unresolved_orders: int, kill_latched: bool, model_id: str | None,
                    model_provider: str | None, strategy_versions: list[str], period: tuple[str | None, str | None]) -> PerformanceSummary:
    if not starting_capital_tmn.is_finite() or starting_capital_tmn <= 0:
        raise ValueError("starting capital must be finite and positive")
    if not all(t.net_pnl_tmn.is_finite() for t in ledger.trades):
        raise ValueError("trade P&L must be finite")
    equity = ledger.mark(marks, ledger.equity_curve[-1][0], fee_rate=fee_rate) if ledger.equity_curve else ledger.equity(marks, fee_rate)
    trades = ledger.trades
    wins = sum(1 for t in trades if t.net_pnl_tmn > 0)
    losses = sum(1 for t in trades if t.net_pnl_tmn < 0)
    gross_profit = sum((t.net_pnl_tmn for t in trades if t.net_pnl_tmn > 0), ZERO)
    gross_loss = -sum((t.net_pnl_tmn for t in trades if t.net_pnl_tmn < 0), ZERO)
    holding, time_in_market = _holding(ledger, period)
    status = Counter(d.status for d in decisions)
    llm = [d for d in decisions if d.provenance is not None and d.provenance.source is DecisionSource.LLM]
    latencies = [d.provenance.latency_ms for d in llm if d.provenance is not None]
    invalid = Counter(d.validated.status.value for d in llm if d.validated is not None and not d.validated.status.answered)
    reasons: Counter[str] = Counter()
    blocks: Counter[str] = Counter()
    responders: set[str] = set()
    for d in decisions:
        if d.validated is not None and d.validated.decision is not None:
            reasons.update(r.value for r in d.validated.decision.reason_codes)
        if d.risk_verdict is not None:
            blocks.update(c.name for c in d.risk_verdict.checks if not c.passed)
        if d.block_reason:
            blocks.update([d.block_reason])
        if d.provenance is not None and d.provenance.model_id:
            responders.add(d.provenance.model_id)
    windowish = sum(1 for d in decisions if d.block_reason and any(
        k in d.block_reason for k in ("WINDOW", "COMPETITION", "CUTOFF")))
    unreal = ledger.unrealized_pnl_tmn(marks, fee_rate)
    return PerformanceSummary(
        run_id=run_id, environment=environment, autonomy=autonomy, label=label, period_start=period[0], period_end=period[1],
        starting_capital_tmn=starting_capital_tmn, final_equity_tmn=equity,
        cash_tmn=ledger.cash_tmn, realized_pnl_tmn=ledger.realized_pnl_tmn,
        unrealized_pnl_tmn=unreal, fees_tmn=ledger.fees_paid_tmn,
        net_pnl_tmn=equity - starting_capital_tmn,
        realized_return_pct=_pct(ledger.realized_pnl_tmn / starting_capital_tmn),
        return_pct=_pct((equity - starting_capital_tmn) / starting_capital_tmn),
        peak_equity_tmn=ledger.peak_equity_tmn.quantize(Decimal("1")), max_drawdown_pct=_pct(ledger.max_drawdown_fraction),
        trades=len(trades), wins=wins, losses=losses, breakeven_trades=len(trades) - wins - losses,
        gross_profit_tmn=gross_profit, gross_loss_tmn=gross_loss,
        profit_factor=gross_profit / gross_loss if gross_loss else None,
        fee_burden_pct=_pct(ledger.fees_paid_tmn / starting_capital_tmn),
        avg_holding_s=holding, time_in_market_pct=time_in_market,
        win_rate_pct=_pct(Decimal(wins) / len(trades)) if trades else None,
        avg_trade_pnl_tmn=sum((t.net_pnl_tmn for t in trades), ZERO) / len(trades) if trades else None,
        open_positions=[{"symbol": s, "qty": str(p.qty), "cost_tmn": str(p.cost_tmn.quantize(Decimal("1"))),
                         "stop": str(p.plan.stop_price) if p.plan else "", "take_profit": str(p.plan.take_profit_price) if p.plan else ""}
                        for s, p in ledger.positions.items()],
        exposure_tmn=ledger.exposure_tmn(marks).quantize(Decimal("1")), dust={k: str(v) for k, v in ledger.dust.items()},
        decisions_total=len(decisions), no_candidate_cycles=status[CycleStatus.NO_CANDIDATES], wait_decisions=status[CycleStatus.WAITED],
        proposed=status[CycleStatus.PROPOSED], awaiting_approval=status[CycleStatus.AWAITING_APPROVAL],
        risk_rejected=status[CycleStatus.RISK_REJECTED], blocked=status[CycleStatus.BLOCKED], outside_window_or_cutoff=windowish,
        entries_submitted=status[CycleStatus.SUBMITTED] + status[CycleStatus.FILLED] + status[CycleStatus.PARTIALLY_FILLED],
        ai_calls=len(llm), ai_valid=sum(1 for d in llm if d.validated is not None and d.validated.status is ValidationStatus.VALID),
        ai_invalid_by_status=dict(invalid), ai_latency_ms_p50=int(median(latencies)) if latencies else None,
        ai_latency_ms_p95=_p95(latencies), ai_latency_ms_max=max(latencies) if latencies else None,
        execution_errors=execution_errors, unresolved_orders=unresolved_orders, kill_switch_latched=kill_latched,
        top_reason_codes=reasons.most_common(6), top_risk_blocks=blocks.most_common(6), model_id=model_id,
        model_provider=model_provider, responding_models=sorted(responders), strategy_versions=strategy_versions)


def summary_json(summary: PerformanceSummary) -> dict[str, Any]:
    return summary.model_dump(mode="json")
