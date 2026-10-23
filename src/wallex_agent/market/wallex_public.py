"""Public (no-key) Wallex REST client. Read-only by construction: it has no method that writes.

JSON numbers are parsed as Decimal. A response with `success: false` is an error even with HTTP 200.
"""

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from decimal import Decimal
from typing import Any, cast

from .bars import WallexPayloadError

Getter = Callable[[str, float], bytes]


def urllib_get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "wallex-agentic-trader/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return cast(bytes, response.read())


class WallexPublicClient:
    def __init__(self, base_url: str = "https://api.wallex.ir", get: Getter = urllib_get, *,
                 timeout_s: float = 20.0, min_interval_s: float = 0.3) -> None:
        self._base = base_url.rstrip("/")
        self._get = get
        self._timeout = timeout_s
        self._min_interval = min_interval_s
        self._last = 0.0

    def _call(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        wait = self._min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        url = self._base + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        raw = self._get(url, self._timeout)
        self._last = time.monotonic()
        data = cast(dict[str, Any], json.loads(raw, parse_float=Decimal))
        if data.get("success") is False:
            raise WallexPayloadError(str(data.get("message") or "success=false"))
        return data

    def candles(self, symbol: str, resolution: str, frm: int, to: int) -> dict[str, Any]:
        """`/v1/udf/history`. Wallex caps the range per call; keep frm..to within one day for 1m bars."""
        return self._call("/v1/udf/history", {"symbol": symbol, "resolution": resolution, "from": frm, "to": to})

    def depth(self, symbol: str) -> dict[str, Any]:
        return self._call("/v1/depth", {"symbol": symbol})

    def markets_v1(self) -> dict[str, Any]:
        return self._call("/v1/markets")

    def markets_hector(self) -> dict[str, Any]:
        return self._call("/hector/web/v1/markets")

    def package(self, package_id: int) -> dict[str, Any]:
        return self._call(f"/v1/prop/packages/{package_id}")
