from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from wallex_agent.portfolio.ledger import Fill, InsufficientFunds, Ledger

T = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def _fill(side: str, qty: str, price: str, fee: str, fee_asset: str = "TMN", purpose: str = "ENTRY", minutes: int = 0) -> Fill:
    return Fill(symbol="BTCTMN", side=side, qty=Decimal(qty), price=Decimal(price), fee=Decimal(fee), fee_asset=fee_asset,
                at=T + timedelta(minutes=minutes), client_order_id=f"c-{side}-{minutes}", purpose=purpose,
                decision_id="d1", strategy_version="c1-test")


def test_round_trip_realized_pnl_after_fees_in_tmn() -> None:
    led = Ledger(Decimal(5_000_000))
    led.apply_fill(_fill("BUY", "0.01", "100000000", "2500"), plan=None)  # cost 1,000,000 + fee 2,500
    assert led.cash_tmn == Decimal(5_000_000 - 1_002_500)
    led.apply_fill(_fill("SELL", "0.01", "110000000", "2750", purpose="EXIT_TAKE_PROFIT", minutes=30), plan=None)
    assert led.cash_tmn == Decimal(5_000_000 - 1_002_500 + 1_100_000 - 2_750)
    assert led.realized_pnl_tmn == Decimal(1_100_000 - 2_750 - 1_002_500)
    assert led.fees_paid_tmn == Decimal(5_250)
    assert led.positions == {} and len(led.trades) == 1
    t = led.trades[0]
    assert t.net_pnl_tmn == led.realized_pnl_tmn and t.exit_reason == "EXIT_TAKE_PROFIT" and t.fees_tmn == Decimal(5250)


def test_fee_in_coin_reduces_sellable_qty() -> None:
    led = Ledger(Decimal(5_000_000))
    led.apply_fill(_fill("BUY", "0.01", "100000000", "0.000025", fee_asset="BTC"), plan=None)
    assert led.positions["BTCTMN"].qty == Decimal("0.009975")
    assert led.fees_paid_tmn == Decimal("2500")  # valued at fill price


def test_coin_entry_fee_round_trip_matches_actual_cash_loss() -> None:
    led = Ledger(Decimal(5000))
    led.apply_fill(_fill("BUY", "10", "100", "1", fee_asset="BTC"), plan=None)
    assert led.cash_tmn == Decimal(4000)
    assert led.positions["BTCTMN"].cost_tmn == Decimal(1000)
    assert led.unrealized_pnl_tmn({"BTCTMN": Decimal(100)}, Decimal(0)) == Decimal(-100)
    led.apply_fill(_fill("SELL", "9", "100", "0", minutes=1), plan=None)
    assert led.cash_tmn == Decimal(4900)
    assert led.realized_pnl_tmn == Decimal(-100)
    assert led.trades[0].net_pnl_tmn == Decimal(-100)
    assert led.fees_paid_tmn == Decimal(100)


def test_partial_sale_after_coin_entry_fee_uses_cash_cost_basis() -> None:
    led = Ledger(Decimal(5000))
    led.apply_fill(_fill("BUY", "10", "100", "1", fee_asset="BTC"), plan=None)
    led.apply_fill(_fill("SELL", "4.5", "120", "0", minutes=1), plan=None)
    assert led.positions["BTCTMN"].qty == Decimal("4.5")
    assert led.positions["BTCTMN"].cost_tmn == Decimal(500)
    assert led.realized_pnl_tmn == Decimal(40)
    assert led.cash_tmn == Decimal(4540)


@pytest.mark.parametrize("side,qty,fee", [("BUY", "100", "1"), ("SELL", "20", "1")])
def test_rejected_fill_does_not_change_any_accounting(side: str, qty: str, fee: str) -> None:
    led = Ledger(Decimal(5000))
    led.apply_fill(_fill("BUY", "10", "100", "0"), plan=None)
    before = (led.cash_tmn, led.fees_paid_tmn, led.realized_pnl_tmn,
              led.positions["BTCTMN"].qty, led.positions["BTCTMN"].cost_tmn)
    with pytest.raises((InsufficientFunds, ValueError)):
        led.apply_fill(_fill(side, qty, "100", fee), plan=None)
    assert (led.cash_tmn, led.fees_paid_tmn, led.realized_pnl_tmn,
            led.positions["BTCTMN"].qty, led.positions["BTCTMN"].cost_tmn) == before


@pytest.mark.parametrize("side,fee,asset", [("BUY", "-1", "TMN"), ("BUY", "10", "BTC"),
                                          ("BUY", "1", "ETH"), ("SELL", "-1", "TMN"),
                                          ("WRONG", "0", "TMN")])
def test_invalid_fill_is_refused_before_mutation(side: str, fee: str, asset: str) -> None:
    led = Ledger(Decimal(5000))
    led.apply_fill(_fill("BUY", "10", "100", "0"), plan=None)
    with pytest.raises(ValueError):
        led.apply_fill(_fill(side, "10", "100", fee, fee_asset=asset), plan=None)
    assert led.cash_tmn == Decimal(4000)
    assert led.fees_paid_tmn == Decimal(0)
    assert led.positions["BTCTMN"].qty == Decimal(10)
    assert led.positions["BTCTMN"].cost_tmn == Decimal(1000)


def test_partial_exit_keeps_position_and_proportional_cost() -> None:
    led = Ledger(Decimal(5_000_000))
    led.apply_fill(_fill("BUY", "0.02", "100000000", "0"), plan=None)
    led.apply_fill(_fill("SELL", "0.01", "90000000", "0", purpose="EXIT_STOP", minutes=5), plan=None)
    assert led.positions["BTCTMN"].qty == Decimal("0.01")
    assert led.realized_pnl_tmn == Decimal(-100_000)
    assert led.trades == []  # trade closes only when the position is flat (or dust)


def test_dust_closes_trade_and_is_reported() -> None:
    led = Ledger(Decimal(5_000_000))
    led.apply_fill(_fill("BUY", "0.0100001", "100000000", "0"), plan=None)
    led.apply_fill(_fill("SELL", "0.01", "100000000", "0", purpose="LIQUIDATION", minutes=5), plan=None,
                   dust_qty=Decimal("0.0000005"))
    assert "BTCTMN" not in led.positions and led.dust["BTCTMN"] == Decimal("0.0000001") and len(led.trades) == 1


def test_reservations_reduce_spendable_and_cannot_overspend() -> None:
    led = Ledger(Decimal(1_000_000))
    rid = led.reserve("intent-a", Decimal(600_000))
    assert led.spendable_tmn == Decimal(400_000)
    with pytest.raises(InsufficientFunds):
        led.reserve("intent-b", Decimal(500_000))
    led.release(rid)
    assert led.spendable_tmn == Decimal(1_000_000)


def test_equity_marks_open_position_at_liquidation_value_and_tracks_drawdown() -> None:
    led = Ledger(Decimal(5_000_000))
    led.apply_fill(_fill("BUY", "0.01", "100000000", "0"), plan=None)
    eq1 = led.mark({"BTCTMN": Decimal("100000000")}, T, fee_rate=Decimal("0.0025"))
    assert eq1 == Decimal(4_000_000) + Decimal("997500")
    eq2 = led.mark({"BTCTMN": Decimal("90000000")}, T + timedelta(minutes=1), fee_rate=Decimal(0))
    assert eq2 == Decimal(4_900_000)
    assert led.peak_equity_tmn == Decimal(5_000_000)
    assert led.max_drawdown_fraction == Decimal(100_000) / Decimal(5_000_000)
    assert led.unrealized_pnl_tmn({"BTCTMN": Decimal("90000000")}, Decimal(0)) == Decimal(-100_000)


def test_sell_more_than_held_is_refused() -> None:
    led = Ledger(Decimal(5_000_000))
    led.apply_fill(_fill("BUY", "0.01", "100000000", "0"), plan=None)
    with pytest.raises(ValueError):
        led.apply_fill(_fill("SELL", "0.02", "100000000", "0", minutes=1), plan=None)
