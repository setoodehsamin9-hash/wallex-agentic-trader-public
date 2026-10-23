from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from wallex_agent.portfolio.ledger import Ledger, TradeRecord
from wallex_agent.reporting.performance import PerformanceSummary, compute_summary

D = Decimal
START = datetime(2026, 10, 1, tzinfo=UTC)


def summary(pnls: list[str], *, capital: str = "100", overlapping: bool = False) -> PerformanceSummary:
    ledger = Ledger(D(capital))
    for i, pnl in enumerate(pnls):
        start = START + timedelta(minutes=0 if overlapping else 2 * i)
        ledger.trades.append(TradeRecord(str(i), "BTCTMN", str(i), "qa", start, start + timedelta(minutes=2),
                                         D(1), D(10), D(10) + D(pnl), D("0.25"), D(pnl), "QA"))
    ledger.realized_pnl_tmn = sum((D(p) for p in pnls), D(0))
    ledger.cash_tmn += ledger.realized_pnl_tmn
    ledger.fees_paid_tmn = D("0.25") * len(pnls)
    return compute_summary(run_id="qa", environment="BACKTEST", autonomy="AUTONOMOUS", label="", starting_capital_tmn=D(capital),
        ledger=ledger, marks={}, fee_rate=D(0), decisions=[], execution_errors=0, unresolved_orders=0, kill_latched=False,
        model_id=None, model_provider=None, strategy_versions=["qa"], period=(START.isoformat(), (START + timedelta(minutes=10)).isoformat()))


def test_mixed_trades_keep_financial_precision_and_breakevens() -> None:
    s = summary(["10", "-4", "0"])
    assert (s.wins, s.losses, s.breakeven_trades) == (1, 1, 1)
    assert (s.gross_profit_tmn, s.gross_loss_tmn, s.profit_factor) == (D(10), D(4), D("2.5"))
    assert s.fees_tmn == D("0.75")
    assert s.fee_burden_pct == D("0.75")  # fees / starting capital * 100
    assert s.avg_holding_s == D(120)
    assert s.time_in_market_pct == D(60)
    assert s.avg_trade_pnl_tmn == D(2)


@pytest.mark.parametrize("pnls,wins,losses,factor", [(["5", "1"], 2, 0, None), (["-2", "-1"], 0, 2, D(0)),
                                                     (["0"], 0, 0, None), ([], 0, 0, None)])
def test_undefined_profit_factor_is_null(pnls: list[str], wins: int, losses: int, factor: Decimal | None) -> None:
    s = summary(pnls)
    assert (s.wins, s.losses, s.profit_factor) == (wins, losses, factor)
    if not pnls:
        assert s.win_rate_pct is s.avg_trade_pnl_tmn is s.avg_holding_s is None
        assert s.time_in_market_pct == 0
        assert s.return_pct == 0 and s.gross_profit_tmn == s.gross_loss_tmn == 0


def test_overlapping_positions_use_union_of_holding_intervals() -> None:
    assert summary(["1", "2"], overlapping=True).time_in_market_pct == D(20)


def test_invalid_metric_input_fails_instead_of_inventing_zero() -> None:
    with pytest.raises(ValueError, match="capital"):
        summary([], capital="0")
    with pytest.raises(ValueError):
        summary(["NaN"])
