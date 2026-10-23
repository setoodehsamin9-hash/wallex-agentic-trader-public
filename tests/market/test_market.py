import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from wallex_agent.contracts import QualityFlag
from wallex_agent.market.bars import Bar, WallexPayloadError, aggregate, parse_udf_history
from wallex_agent.market.book import parse_depth
from wallex_agent.market.meta import eligible_symbols, parse_market_meta, unavailable_markets
from wallex_agent.market.snapshot import build_snapshot

REPO = Path(__file__).resolve().parents[2]
EV1 = REPO / "research" / "phase-01" / "evidence"
EV2 = REPO / "research" / "phase-02" / "evidence"


def _body(name: str) -> Any:
    raw = json.loads((EV1 / name).read_text())["body"]
    return json.loads(raw, parse_float=Decimal) if isinstance(raw, str) else raw


def _utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def test_forming_bar_dropped() -> None:
    payload = json.loads(json.loads((EV2 / "mcp_public_call_candles_r1.json").read_text())["result"]["content"][0]["text"])
    last_t = payload["t"][-1]
    bars, _ = parse_udf_history(payload, 60, now=_utc(last_t + 35))
    assert bars[-1].ts_open == _utc(last_t - 60)
    assert len(bars) == len(payload["t"]) - 1


def test_resolution_mismatch_flagged() -> None:
    payload = _body("_v1_udf_history_15.json")  # requested 15m, Wallex returned 1m spacing
    _, flags = parse_udf_history(payload, 900, now=_utc(payload["t"][-1] + 3600))
    assert QualityFlag.RESOLUTION_MISMATCH in flags


def test_payload_error_is_error_even_when_transport_ok() -> None:
    with pytest.raises(WallexPayloadError, match="Invalid resolution"):
        parse_udf_history({"errmsg": "Invalid resolution!", "s": "error"}, 300, now=datetime.now(UTC))


def test_gap_flagged() -> None:
    payload = _body("_v1_udf_history.json")
    for key in ("t", "o", "h", "l", "c", "v"):
        del payload[key][100:105]
    _, flags = parse_udf_history(payload, 60, now=_utc(payload["t"][-1] + 3600))
    assert QualityFlag.GAP in flags


def test_prices_are_decimal() -> None:
    bars, _ = parse_udf_history(_body("_v1_udf_history.json"), 60, now=datetime(2030, 1, 1, tzinfo=UTC))
    assert all(isinstance(getattr(bars[0], f), Decimal) for f in ("open", "high", "low", "close", "volume"))


def test_aggregate_only_complete_buckets() -> None:
    t0 = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    one = [Bar(t0 + timedelta(minutes=i), Decimal(100 + i), Decimal(101 + i), Decimal(99 + i), Decimal(100 + i), Decimal(1))
           for i in range(12)]
    agg = aggregate(one, 300)
    assert [b.ts_open for b in agg] == [t0, t0 + timedelta(minutes=5)]  # third bucket (2 bars) incomplete
    assert agg[0].open == Decimal(100) and agg[0].close == Decimal(104)
    assert agg[0].high == Decimal(105) and agg[0].low == Decimal(99) and agg[0].volume == Decimal(5)


def test_depth_parsed_as_decimal_and_sorted() -> None:
    book = parse_depth(_body("_v1_depth.json"), received_at=datetime(2026, 10, 22, tzinfo=UTC))
    assert isinstance(book.asks[0][0], Decimal)
    assert book.asks[0][0] < book.asks[-1][0] and book.bids[0][0] > book.bids[-1][0]
    top = book.top()
    assert top.ask > top.bid


def test_crossed_book_flagged() -> None:
    payload = {"result": {"ask": [{"price": 100, "quantity": 1}], "bid": [{"price": 101, "quantity": 1}]}}
    assert QualityFlag.CROSSED_BOOK in parse_depth(payload, received_at=datetime(2026, 10, 22, tzinfo=UTC)).flags


def test_eligible_formula_matches_phase01_snapshot() -> None:
    hector = _body("_hector_web_v1_markets.json")
    spot = {m["symbol"] for m in hector["result"]["markets"] if m["is_spot"]}
    metadata_available = set(_body("_v1_markets.json")["result"]["symbols"])
    blocked = unavailable_markets(_body("_v1_prop_packages_183.json"))
    expected = set(json.loads((EV1 / "eligible_symbols.json").read_text())["symbols"])
    assert eligible_symbols(spot, metadata_available, blocked, set()) == expected
    assert len(expected) == 120
    tradable = parse_market_meta(_body("_v1_markets.json"), hector, metadata_version="phase01")
    assert "BTCTMN" in tradable and all(s.endswith("TMN") for s in tradable)  # we only trade TMN quotes


def test_market_meta_uses_hector_precision_and_v1_minimums() -> None:
    meta = parse_market_meta(_body("_v1_markets.json"), _body("_hector_web_v1_markets.json"), metadata_version="phase01")
    btc = meta["BTCTMN"]
    assert btc.amount_precision == 8 and btc.price_precision == 0
    assert btc.min_notional == Decimal(50000) and btc.market_order_enabled


def test_snapshot_id_stable_and_content_sensitive() -> None:
    t0 = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
    bars = [Bar(t0 + timedelta(minutes=15 * i), Decimal(1), Decimal(2), Decimal(1), Decimal(2), Decimal(1)) for i in range(3)]
    meta = parse_market_meta(_body("_v1_markets.json"), _body("_hector_web_v1_markets.json"), metadata_version="phase01")["BTCTMN"]
    kw: dict[str, Any] = {"symbol": "BTCTMN", "as_of": t0 + timedelta(minutes=45), "bar_interval_seconds": 900,
                          "bars": bars, "book": None, "meta": meta, "universe_version": "u1", "dataset_id": "d",
                          "source": "FIXTURE", "flags": frozenset()}
    a, b = build_snapshot(**kw), build_snapshot(**kw)
    assert a.snapshot_id == b.snapshot_id and len(a.snapshot_id) == 64
    kw2: dict[str, Any] = {**kw, "universe_version": "u2"}
    assert build_snapshot(**kw2).snapshot_id != a.snapshot_id
    assert a.last_closed_bar_open == bars[-1].ts_open
