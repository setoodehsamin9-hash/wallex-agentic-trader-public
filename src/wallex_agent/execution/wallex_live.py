"""LIVE Wallex REST venue (orders via `POST/GET/DELETE /v1/account/orders`) and private account reader.

Status (Phase 05): constructed only by the LIVE runner (runtime/live.py) on the owner's machine; tested ONLY with fake
transports. Field mappings marked PROVISIONAL are first confirmed by the owner's read-only preflight (U05/U06/U09); any
parse problem fails closed (AccountReadError / UNKNOWN), never as an empty account or a success.

Safety semantics:
- `client_id` is our idempotency key; a timeout or 5xx after sending is UNKNOWN (SubmitTimeout) -> engine reconciles
  by `GET /v1/account/orders/{client_id}`; it never resubmits blindly.
- 4xx/`success:false`/429 means not placed -> VenueError.
- The API key is read from the environment by the transport and only ever put in the `X-API-Key` header.
"""

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

from wallex_agent.contracts import Environment, FillSource, OrderIntent, OrderState

from .venue import SubmitTimeout, VenueError, VenueFill, VenueOrder

# PROVISIONAL: Wallex order status strings -> internal state (confirm with real fixtures).
STATUS_MAP = {"NEW": OrderState.OPEN, "PARTIALLY_FILLED": OrderState.PARTIAL, "FILLED": OrderState.FILLED,
              "CANCELED": OrderState.CANCELED, "CANCELLED": OrderState.CANCELED, "REJECTED": OrderState.REJECTED,
              "EXPIRED": OrderState.EXPIRED}


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: dict[str, Any]


class Transport(Protocol):
    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> HttpResult:
        """Raises TimeoutError when the outcome is unknown (sent but no complete answer)."""
        ...


class UrllibPrivateTransport:
    """Real transport. Reads WALLEX_API_KEY at call time; never logs it. Throttles to the documented 20 req / 10 s."""

    def __init__(self, base_url: str = "https://api.wallex.ir", *, timeout_s: float = 10, min_interval_s: float = 0.55,
                 key_provider: Callable[[], str] | None = None) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout_s
        self.min_interval = min_interval_s
        self._last = 0.0
        self._key = key_provider or (lambda: os.environ["WALLEX_API_KEY"])

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> HttpResult:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-API-Key": self._key(), "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                self._last = time.monotonic()
                return HttpResult(resp.status, json.loads(resp.read(), parse_float=Decimal))
        except urllib.error.HTTPError as exc:
            self._last = time.monotonic()
            payload: dict[str, Any]
            try:
                payload = dict(json.loads(exc.read(), parse_float=Decimal))
            except Exception:  # noqa: BLE001 - unreadable error body: the status code alone decides
                payload = {}
            return HttpResult(exc.code, payload)
        except Exception as exc:  # noqa: BLE001 - once sent, ANY failure (reset, IncompleteRead, bad JSON, URLError) is UNKNOWN
            raise TimeoutError(f"{method} {path}: outcome unknown ({type(exc).__name__})") from None


def _dec(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


class WallexLiveVenue:
    environment = Environment.LIVE
    fill_source = FillSource.EXCHANGE

    def __init__(self, transport: Transport) -> None:
        self.t = transport
        self._intents: dict[str, OrderIntent] = {}

    @staticmethod
    def order_body(intent: OrderIntent) -> dict[str, Any]:
        body: dict[str, Any] = {"symbol": intent.symbol, "side": intent.side, "type": intent.order_type,
                                "quantity": format(intent.qty, "f"), "client_id": intent.client_order_id}
        if intent.order_type == "LIMIT" and intent.limit_price is not None:
            body["price"] = format(intent.limit_price, "f")
        return body

    def _parse(self, intent: OrderIntent, result: dict[str, Any]) -> VenueOrder:
        r: dict[str, Any] = result.get("result", result)
        raw = str(r.get("status", "NEW")).upper()
        order = VenueOrder(intent.client_order_id, intent, STATUS_MAP.get(raw, OrderState.UNKNOWN), raw)
        fills: list[dict[str, Any]] = list(r.get("fills", []) or [])
        for i, f in enumerate(fills):  # PROVISIONAL fill fields
            order.fills.append(VenueFill(fill_id=str(f.get("id", f"{intent.client_order_id}-{i}")), qty=_dec(f["quantity"]),
                                         price=_dec(f["price"]), fee=_dec(f.get("fee", 0)), fee_asset=str(f.get("feeAsset", "TMN")),
                                         at=datetime.fromisoformat(str(f["timestamp"]).replace("Z", "+00:00")) if f.get("timestamp")
                                         else datetime.now(UTC)))
        return order

    def submit(self, intent: OrderIntent, now: datetime) -> VenueOrder:
        self._intents[intent.client_order_id] = intent  # remembered BEFORE sending so a lost response can be reconciled
        try:
            res = self.t.request("POST", "/v1/account/orders", self.order_body(intent))
        except TimeoutError as exc:
            raise SubmitTimeout(str(exc)) from None
        if res.status >= 500:
            raise SubmitTimeout(f"HTTP {res.status}: outcome unknown")
        if not isinstance(res.body, dict):  # pyright: ignore[reportUnnecessaryIsInstance] - real JSON may be a list/str
            raise SubmitTimeout(f"HTTP {res.status}: unreadable answer, outcome unknown")
        if res.status >= 400 or res.body.get("success") is False:
            raise VenueError(f"HTTP {res.status}: {res.body.get('message', 'rejected')}")
        try:
            return self._parse(intent, res.body)
        except (ValueError, ArithmeticError, AttributeError, TypeError, KeyError) as exc:
            raise SubmitTimeout(f"HTTP {res.status}: unparseable answer ({type(exc).__name__}), outcome unknown") from None

    def remember(self, intent: OrderIntent) -> None:
        """Restart recovery: register an order rebuilt from the state log so it can be reconciled by client_id."""
        self._intents[intent.client_order_id] = intent

    def query(self, client_order_id: str) -> VenueOrder | None:
        intent = self._intents.get(client_order_id)
        if intent is None:
            return None
        try:
            res = self.t.request("GET", f"/v1/account/orders/{intent.client_order_id}")
        except TimeoutError:
            return None
        if res.status == 404:
            return None
        if res.status >= 400 or res.body.get("success") is False:
            return None
        return self._parse(intent, res.body)

    def cancel(self, client_order_id: str, now: datetime) -> VenueOrder | None:
        try:
            self.t.request("DELETE", f"/v1/account/orders/{client_order_id}")
        except TimeoutError:
            return None
        return self.query(client_order_id)  # a cancel is confirmed only by a fresh lookup (fill-after-cancel race)


class AccountReadError(RuntimeError):
    """A private read did not return a usable answer. Never read as an empty/zero account."""


class WallexAccountReader:
    """Private read-only account access for preflight/reconciliation (PROVISIONAL shapes). Fails closed: an HTTP error,
    `success: false` or a timeout raises AccountReadError instead of looking like an empty account."""

    def __init__(self, transport: Transport) -> None:
        self.t = transport

    def _get(self, path: str) -> HttpResult:
        try:
            res = self.t.request("GET", path)
        except TimeoutError as exc:
            raise AccountReadError(f"GET {path}: no answer") from exc
        if res.status != 200 or not isinstance(res.body, dict) or res.body.get("success") is False:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise AccountReadError(f"GET {path}: HTTP {res.status}")
        return res

    def balances(self) -> dict[str, tuple[Decimal, Decimal]]:
        res = self._get("/v1/account/balances")
        try:
            out: dict[str, tuple[Decimal, Decimal]] = {}
            raw: dict[str, dict[str, Any]] = dict(res.body.get("result", {}).get("balances", {}) or {})
            for asset, b in raw.items():
                value, locked = _dec(b.get("value", 0)), _dec(b.get("locked", 0))
                out[asset] = (value - locked, locked)
            return out
        except (ValueError, ArithmeticError, AttributeError, TypeError, KeyError) as exc:
            raise AccountReadError(f"balances: unexpected shape ({type(exc).__name__})") from None

    def fee_rate(self, symbol: str) -> Decimal | None:
        res = self._get("/v1/account/fee")
        try:
            fees: dict[str, dict[str, Any]] = dict(res.body.get("result", {}) or {})
            f: dict[str, Any] = fees.get(symbol) or {}
            taker: Any = f.get("takerFeeRate") or f.get("taker")
            return _dec(taker) if taker is not None else None
        except (ValueError, ArithmeticError, AttributeError, TypeError, KeyError) as exc:
            raise AccountReadError(f"fee: unexpected shape ({type(exc).__name__})") from None

    def open_orders(self) -> list[dict[str, Any]]:
        res = self._get("/v1/account/openOrders")
        try:
            return list(res.body.get("result", {}).get("orders", []) or [])
        except (ValueError, AttributeError, TypeError) as exc:
            raise AccountReadError(f"openOrders: unexpected shape ({type(exc).__name__})") from None
