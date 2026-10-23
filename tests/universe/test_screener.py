"""Market screener (Freqtrade pairlist concept, our thresholds): proposal only, ineligible never ranked, missing data and
unchecked spread fail closed, thresholds come from the run's own config. No network."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from wallex_agent.runtime.config import parse_run_config
from wallex_agent.universe import screen, settings_for, spread_candidates

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
CFG = parse_run_config((REPO / "config" / "live-f2.toml").read_text())  # template is fail-closed for LIVE start; parse only
S = settings_for(CFG)


def _m(symbol: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"symbol": symbol, "quote_asset": "TMN", "is_spot": True, "is_new": False, "spot_is_new": False,
                            "is_zero_fee": False, "is_market_type_enable": True, "created_at": "2020-04-01T00:00:00Z",
                            "price": "22000000000", "price_precision": 0, "quote_volume_24h": 117028699114.29,
                            "highest_24h_price": "22600000000", "lowest_24h_price": "21500000000", "change_24h": 1.7}
    return base | kw


def _hector(*markets: dict[str, Any]) -> dict[str, Any]:
    return {"result": {"markets": list(markets)}}


def test_thresholds_are_derived_from_our_config() -> None:
    risk = CFG.limits.risk
    assert risk.max_position_notional_tmn is not None and S.min_quote_volume_tmn == risk.max_position_notional_tmn / Decimal("0.01")
    assert S.max_spread_bps == risk.max_spread_bps and S.min_age_days >= 180  # F2 needs ~187 days of 4h history
    round_trip_bps = 2 * (CFG.costs.fee_rate * 10000 + CFG.costs.backtest_slippage_bps)
    assert S.min_range_pct == round_trip_bps * Decimal("1.5") / 100  # 1.5 x the round trip, in percent


def test_ranks_only_eligible_markets_and_nothing_passes_without_a_spread_check() -> None:
    h = _hector(_m("BTCTMN"), _m("ETHTMN", quote_volume_24h=5e10), _m("XYZTMN"))
    first = screen(h, frozenset({"BTCTMN", "ETHTMN"}), now=NOW, settings=S)
    assert {r.symbol for r in first.rows} == {"BTCTMN", "ETHTMN"} and first.selected == ()
    assert spread_candidates(first, limit=5) == ["BTCTMN", "ETHTMN"]
    final = screen(h, frozenset({"BTCTMN", "ETHTMN"}), now=NOW, settings=S, spreads={"BTCTMN": Decimal(8), "ETHTMN": Decimal(12)})
    assert final.selected == ("BTCTMN", "ETHTMN") and [r.rank for r in final.rows] == [1, 2]


def test_each_filter_fails_closed() -> None:
    cases = {"LOW_VOLUME": _m("A", quote_volume_24h=1e6), "TOO_NEW_FOR_WARMUP": _m("A", created_at="2026-09-01T00:00:00Z"),
             "RANGE_BELOW_COST_HURDLE": _m("A", highest_24h_price="22010000000", lowest_24h_price="22000000000"),
             "RANGE_SHOCK": _m("A", highest_24h_price="40000000000", lowest_24h_price="20000000000"),
             "RANGE_UNKNOWN": _m("A", lowest_24h_price=None), "PRICE_STEP_TOO_COARSE": _m("A", price="500", price_precision=0),
             "MARKET_ORDERS_DISABLED": _m("A", is_market_type_enable=False)}
    for reason, market in cases.items():
        rep = screen(_hector(market), frozenset({"A"}), now=NOW, settings=S, spreads={"A": Decimal(5)})
        assert reason in rep.rows[0].reasons and rep.selected == (), reason
    wide = screen(_hector(_m("A")), frozenset({"A"}), now=NOW, settings=S, spreads={"A": Decimal(80)})
    unknown = screen(_hector(_m("A")), frozenset({"A"}), now=NOW, settings=S, spreads={"A": None})
    assert "WIDE_SPREAD" in wide.rows[0].reasons and "SPREAD_UNKNOWN" in unknown.rows[0].reasons


def test_zero_fee_is_reported_not_assumed() -> None:
    rep = screen(_hector(_m("A", is_zero_fee=True)), frozenset({"A"}), now=NOW, settings=S, spreads={"A": Decimal(5)})
    assert rep.rows[0].zero_fee_flag and S.min_range_pct == settings_for(CFG).min_range_pct  # the flag does NOT lower the hurdle
