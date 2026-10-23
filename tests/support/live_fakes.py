"""Fakes for the LIVE runner: a public client serving synthetic candles/books and a private exchange transport that
fills LIMIT orders at their price. No network, no key. Every private call is recorded."""

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from support.synthetic import EV1, synthetic_bars

from wallex_agent.execution.wallex_live import HttpResult


class FakePublicClient:
    def __init__(self, now: Callable[[], datetime]) -> None:
        self.now = now
        self.series = {"BTCTMN": synthetic_bars(4, 1e8, 0.0), "ETHTMN": synthetic_bars(4, 5e6, 1.3)}

    def _body(self, name: str) -> Any:
        raw = json.loads((EV1 / name).read_text())["body"]
        return json.loads(raw, parse_float=Decimal) if isinstance(raw, str) else raw

    def last(self, symbol: str) -> Decimal:
        return [b for b in self.series[symbol] if b.ts_open <= self.now()][-1].close

    def candles(self, symbol: str, resolution: str, frm: int, to: int) -> dict[str, Any]:
        bars = [b for b in self.series[symbol] if frm <= b.ts_open.timestamp() <= to and b.ts_open + timedelta(seconds=60) <= self.now()]
        return {"s": "ok", "t": [int(b.ts_open.timestamp()) for b in bars], "o": [str(b.open) for b in bars],
                "h": [str(b.high) for b in bars], "l": [str(b.low) for b in bars], "c": [str(b.close) for b in bars],
                "v": [str(b.volume) for b in bars]}

    def depth(self, symbol: str) -> dict[str, Any]:
        last = self.last(symbol)
        return {"result": {"ask": [{"price": last * Decimal("1.0003"), "quantity": Decimal(5)}],
                           "bid": [{"price": last * Decimal("0.9997"), "quantity": Decimal(5)}]}}

    def markets_v1(self) -> Any:
        return self._body("_v1_markets.json")

    def markets_hector(self) -> Any:
        return self._body("_hector_web_v1_markets.json")

    def package(self, package_id: int) -> Any:
        return self._body("_v1_prop_packages_183.json")


class FakeExchange:
    """Private Wallex REST fake: balances, fee, open orders, LIMIT orders filled at their price (fee in TMN)."""

    def __init__(self, tmn: Decimal, *, fee: str = "0.0025", now: Callable[[], datetime], open_orders: int = 0,
                 locked: Decimal = Decimal(0), fail_reads: bool = False) -> None:
        self.bal: dict[str, list[Decimal]] = {"TMN": [tmn, locked]}
        self.fee = Decimal(fee)
        self.now = now
        self.orders: dict[str, dict[str, Any]] = {}
        self.preexisting = open_orders
        self.fail_reads = fail_reads
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> HttpResult:
        self.calls.append((method, path))
        if self.fail_reads and method == "GET":
            return HttpResult(401, {"success": False, "message": "unauthorized"})
        if (method, path) == ("GET", "/v1/account/balances"):
            return HttpResult(200, {"success": True, "result": {"balances": {
                a: {"value": str(v + lk), "locked": str(lk)} for a, (v, lk) in self.bal.items()}}})
        if (method, path) == ("GET", "/v1/account/fee"):
            return HttpResult(200, {"success": True, "result": {s: {"takerFeeRate": str(self.fee)} for s in ("BTCTMN", "ETHTMN")}})
        if (method, path) == ("GET", "/v1/account/openOrders"):
            return HttpResult(200, {"success": True, "result": {"orders": [{"id": i} for i in range(self.preexisting)]}})
        if (method, path) == ("POST", "/v1/account/orders"):
            assert body is not None
            qty, price = Decimal(body["quantity"]), Decimal(body["price"])
            base = body["symbol"][:-3]
            fee = (qty * price * self.fee).quantize(Decimal(1))
            if body["side"] == "BUY":
                self.bal["TMN"][0] -= qty * price + fee
                self.bal.setdefault(base, [Decimal(0), Decimal(0)])[0] += qty
            else:
                self.bal[base][0] -= qty
                self.bal["TMN"][0] += qty * price - fee
            order = {"status": "FILLED", "fills": [{"id": f"f-{body['client_id']}", "quantity": str(qty), "price": str(price),
                                                    "fee": str(fee), "feeAsset": "TMN", "timestamp": self.now().isoformat()}]}
            self.orders[body["client_id"]] = order
            return HttpResult(200, {"success": True, "result": order})
        if method == "GET" and path.startswith("/v1/account/orders/"):
            cid = path.rsplit("/", 1)[1]
            return HttpResult(200, {"success": True, "result": self.orders[cid]}) if cid in self.orders else HttpResult(404, {})
        if method == "DELETE" and path.startswith("/v1/account/orders/"):
            return HttpResult(200, {"success": True})
        raise AssertionError(f"unexpected private call {method} {path}")
