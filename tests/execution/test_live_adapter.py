"""LIVE adapter + preflight on FAKES only (no private Wallex call, no key)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from wallex_agent.contracts import AutonomyMode, Environment, MarketMeta, OrderIntent, OrderPurpose, OrderState, RiskCheck, RiskOutcome, RiskVerdict
from wallex_agent.contracts.hashing import content_id
from wallex_agent.execution.engine import ExecutionEngine
from wallex_agent.execution.venue import SubmitTimeout, VenueError
from wallex_agent.execution.wallex_live import HttpResult, WallexLiveVenue
from wallex_agent.portfolio.ledger import Ledger
from wallex_agent.risk.kill_switch import KillSwitch
from wallex_agent.risk.protections import Protections
from wallex_agent.runtime.config import load_run_config
from wallex_agent.runtime.preflight import run_preflight

T = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[2]
META = MarketMeta(symbol="BTCTMN", quote_asset="TMN", price_precision=0, amount_precision=6, min_qty=Decimal("0.000001"),
                  min_notional=Decimal(50000), market_order_enabled=True, metadata_version="t")


def _intent() -> OrderIntent:
    body = {"intent_id": "in-1", "decision_id": "d", "client_order_id": "wa123", "symbol": "BTCTMN", "side": "BUY",
            "order_type": "LIMIT", "qty": Decimal("0.0123"), "limit_price": Decimal("22100000000"), "purpose": OrderPurpose.ENTRY,
            "environment": Environment.LIVE, "autonomy": AutonomyMode.AUTONOMOUS, "created_at": T}
    return OrderIntent(intent_hash=content_id(body), **body)  # type: ignore[arg-type]


class FakeTransport:
    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.sent: list[tuple[str, str, dict[str, Any] | None]] = []

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> HttpResult:
        self.sent.append((method, path, body))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def test_order_request_shape() -> None:
    t = FakeTransport([HttpResult(200, {"success": True, "result": {"status": "NEW"}})])
    order = WallexLiveVenue(t).submit(_intent(), T)
    method, path, body = t.sent[0]
    assert (method, path) == ("POST", "/v1/account/orders")
    assert body == {"symbol": "BTCTMN", "side": "BUY", "type": "LIMIT", "quantity": "0.0123", "price": "22100000000", "client_id": "wa123"}
    assert order.state is OrderState.OPEN


def test_timeout_and_5xx_are_unknown_4xx_is_rejection() -> None:
    with pytest.raises(SubmitTimeout):
        WallexLiveVenue(FakeTransport([TimeoutError("lost")])).submit(_intent(), T)
    with pytest.raises(SubmitTimeout):
        WallexLiveVenue(FakeTransport([HttpResult(502, {})])).submit(_intent(), T)
    with pytest.raises(VenueError):
        WallexLiveVenue(FakeTransport([HttpResult(429, {"message": "rate"})])).submit(_intent(), T)


def test_engine_reconciles_live_lost_response_without_resubmit() -> None:
    t = FakeTransport([TimeoutError("lost"), HttpResult(200, {"result": {"status": "FILLED", "fills": [
        {"id": "f1", "quantity": "0.0123", "price": "22050000000", "fee": "678075", "feeAsset": "TMN", "timestamp": "2026-10-22T09:00:01Z"}]}})])
    led = Ledger(Decimal(5_000_000_000))
    eng = ExecutionEngine(WallexLiveVenue(t), led, Protections(load_run_config(REPO / "config/backtest.toml").limits.risk), KillSwitch(),
                          metas={"BTCTMN": META}, max_consecutive_errors=3)
    i = _intent()
    v = RiskVerdict(verdict_id="v", intent_hash=i.intent_hash, outcome=RiskOutcome.APPROVE, approved_qty=i.qty,
                    checks=(RiskCheck(name="x", passed=True, detail=""),), limits_version="l", evaluated_at=T, expires_at=T + timedelta(seconds=30))
    eng.submit(i, v, now=T, plan=None)
    assert [m for m, _, _ in t.sent] == ["POST", "GET"]  # exactly one submission, then a lookup by client_id
    assert eng.order("wa123").state is OrderState.FILLED and led.positions["BTCTMN"].qty == Decimal("0.0123")


class FakeAccount:
    def __init__(self, tmn: str = "5000000", extra: dict[str, tuple[Decimal, Decimal]] | None = None, orders: int = 0) -> None:
        self.b = {"TMN": (Decimal(tmn), Decimal(0)), **(extra or {})}
        self.o = orders

    def balances(self) -> dict[str, tuple[Decimal, Decimal]]:
        return self.b

    def fee_rate(self, symbol: str) -> Decimal | None:
        return Decimal("0.0025")

    def open_orders(self) -> list[dict[str, Any]]:
        return [{}] * self.o


def test_preflight_fails_closed_without_account_and_with_shipped_config() -> None:
    cfg = load_run_config(REPO / "config/shadow.toml")
    rep = run_preflight(cfg, now=T, account=None, metas={"BTCTMN": META}, eligible=frozenset({"BTCTMN"}), marks={},
                        require_clean_tree=False)
    assert not rep.ok
    names = {n for n, ok, _ in rep.checks if not ok}
    assert {"config_complete", "competition_clock", "account_access"} <= names


def test_preflight_detects_account_mismatch() -> None:
    cfg = load_run_config(REPO / "config/shadow.toml")
    kw: dict[str, Any] = {"now": T, "metas": {"BTCTMN": META, "ETHTMN": META.model_copy(update={"symbol": "ETHTMN"})},
                          "eligible": frozenset({"BTCTMN", "ETHTMN"}), "marks": {"BTCTMN": Decimal(22_000_000_000)},
                          "require_clean_tree": False}
    ok_checks = {n: ok for n, ok, _ in run_preflight(cfg, account=FakeAccount(), **kw).checks}
    assert ok_checks["spendable_tmn"] and ok_checks["no_open_orders"] and ok_checks["no_unexpected_assets"]
    bad = {n: ok for n, ok, _ in run_preflight(cfg, account=FakeAccount(tmn="4000000", extra={"BTC": (Decimal("0.01"), Decimal(0))}, orders=1), **kw).checks}
    assert not bad["spendable_tmn"] and not bad["no_unexpected_assets"] and not bad["no_open_orders"]


def test_live_venue_can_look_up_an_order_adopted_after_restart() -> None:
    t = FakeTransport([HttpResult(200, {"result": {"status": "CANCELED"}})])
    venue = WallexLiveVenue(t)
    assert venue.query("wa123") is None and t.sent == []  # unknown in this process
    venue.remember(_intent())
    found = venue.query("wa123")
    assert found is not None and found.state is OrderState.CANCELED and t.sent == [("GET", "/v1/account/orders/wa123", None)]
