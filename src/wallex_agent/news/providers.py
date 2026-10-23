"""Replaceable public feed parsers and a bounded GET-only transport."""

import json
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, DecimalException
from types import MappingProxyType
from typing import Any, Literal, cast

from wallex_agent.contracts.hashing import sha256_hex

from .contracts import Category, NewsCapture, NewsItem
from .normalize import normalize_item, parse_timestamp

MAX_BODY_BYTES = 1048576
MAX_ITEMS = 50


class NewsPayloadError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSpec:
    id: str
    name: str
    url: str
    kind: Literal["CV", "RSS", "GDELT"]
    category: Category
    collection_allowed: bool = True


SOURCES: Mapping[str, SourceSpec] = MappingProxyType({
    "cryptocurrency_cv": SourceSpec("cryptocurrency_cv", "cryptocurrency.cv", "https://cryptocurrency.cv/api/news?limit=50", "CV", "CRYPTO"),
    "federal_reserve": SourceSpec("federal_reserve", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml", "RSS", "MACRO"),
    "bitcoin_core": SourceSpec("bitcoin_core", "Bitcoin Core", "https://bitcoincore.org/en/rss.xml", "RSS", "TECHNOLOGY"),
    "gdelt": SourceSpec("gdelt", "GDELT", "https://api.gdeltproject.org/api/v2/doc/doc?query=inflation&mode=artlist&format=json&maxrecords=50&timespan=24h&sort=DateDesc", "GDELT", "MACRO"),
    "coindesk": SourceSpec("coindesk", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss", "RSS", "CRYPTO", False),
})


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _text(element: ET.Element, names: tuple[str, ...]) -> str | None:
    for child in element:
        if _tag(child) in names:
            return "".join(child.itertext())
    return None


def _rss_rows(body: bytes) -> tuple[list[dict[str, Any]], str | None]:
    text = body.decode("utf-8-sig")
    if "<!doctype" in text.casefold() or "<!entity" in text.casefold():
        raise NewsPayloadError("XML declarations/entities denied")
    root = ET.fromstring(text)
    if _tag(root) == "rss":
        channel = next((element for element in root if _tag(element) == "channel"), None)
        if channel is None:
            raise NewsPayloadError("RSS channel missing")
        entries = [element for element in channel if _tag(element) == "item"]
        language = _text(channel, ("language",))
    elif _tag(root) == "feed":
        entries = [element for element in root if _tag(element) == "entry"]
        language = root.get("{http://www.w3.org/XML/1998/namespace}lang")
    else:
        raise NewsPayloadError("unsupported feed root")
    rows: list[dict[str, Any]] = []
    for entry in entries:
        link = _text(entry, ("link",))
        for element in entry:
            if _tag(element) == "link" and element.get("href") and element.get("rel", "alternate") == "alternate":
                link = element.get("href")
                break
        rows.append({"title": _text(entry, ("title",)), "link": link,
                     "pubDate": _text(entry, ("pubDate", "published")),
                     "description": _text(entry, ("description", "summary"))})
    return rows, language


def _deny_constant(value: str) -> Any:
    raise NewsPayloadError("non-finite JSON constant")


def _check_json_depth(value: Any, depth: int = 0) -> None:
    if depth > 16:
        raise NewsPayloadError("news JSON nesting limit exceeded")
    if isinstance(value, dict):
        for child in cast(dict[str, Any], value).values():
            _check_json_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in cast(list[Any], value):
            _check_json_depth(child, depth + 1)


def parse_capture(source: SourceSpec, body: bytes, *, fetched_at: datetime) -> NewsCapture:
    if len(body) > MAX_BODY_BYTES:
        raise NewsPayloadError("news payload too large")
    try:
        observed: datetime | None = None
        provider_total: int | None = None
        provider_has_more: bool | None = None
        provider_limited: bool | None = None
        rows: list[Any]
        if source.kind == "RSS":
            feed_rows, language = _rss_rows(body)
            rows = list(feed_rows)
        else:
            value: Any = json.loads(body, parse_float=Decimal, parse_constant=_deny_constant)
            _check_json_depth(value)
            if not isinstance(value, dict):
                raise NewsPayloadError("news response must be an object")
            data = cast(dict[str, Any], value)
            if data.get("success") is False or data.get("error") or data.get("status") == "error":
                raise NewsPayloadError("provider reported an error")
            raw_rows: Any = data.get("articles")
            if not isinstance(raw_rows, list):
                raise NewsPayloadError("articles must be an array")
            rows = cast(list[Any], raw_rows)
            language = data.get("lang")
            observed = parse_timestamp(data.get("fetchedAt"))
            if data.get("fetchedAt") is not None and observed is None:
                raise NewsPayloadError("invalid provider observation time")
            if source.kind == "CV":
                total: Any = data.get("totalCount")
                limited: Any = data.get("limited")
                pagination: Any = data.get("pagination")
                if total is not None:
                    if type(total) is not int or total < 0:
                        raise NewsPayloadError("invalid provider total count")
                    provider_total = total
                if limited is not None:
                    if type(limited) is not bool:
                        raise NewsPayloadError("invalid provider limited flag")
                    provider_limited = limited
                if pagination is not None:
                    if not isinstance(pagination, dict):
                        raise NewsPayloadError("invalid provider pagination")
                    has_more: Any = cast(dict[str, Any], pagination).get("hasMore")
                    if has_more is not None:
                        if type(has_more) is not bool:
                            raise NewsPayloadError("invalid provider pagination flag")
                        provider_has_more = has_more
        items: list[NewsItem] = []
        rejected = 0
        digest = sha256_hex(body)
        for index, candidate in enumerate(rows[:MAX_ITEMS]):
            if not isinstance(candidate, dict):
                rejected += 1
                continue
            raw = cast(dict[str, Any], candidate)
            if source.kind == "GDELT":
                raw = {**raw, "link": raw.get("url"), "source": raw.get("domain")}
                observed = parse_timestamp(raw.get("seendate"))
            try:
                items.append(normalize_item(raw, provider_id=source.id,
                                            publisher_default=source.name if source.kind == "RSS" else None, feed_url=source.url,
                                            fetched_at=fetched_at, payload_sha256=digest, item_index=index,
                                            default_category=source.category, language=raw.get("language", language),
                                            provider_observed_at=observed, seen_only=source.kind == "GDELT"))
            except (ValueError, TypeError, OverflowError):
                rejected += 1
        flags: set[str] = set()
        if rejected:
            flags.add("REJECTED_ROWS")
        if len(rows) > MAX_ITEMS:
            flags.add("SOURCE_ITEM_LIMIT")
        if provider_limited:
            flags.add("PROVIDER_LIMITED")
        if provider_has_more or (provider_total is not None and provider_total > len(rows)):
            flags.add("PROVIDER_PAGINATION")
        return NewsCapture(provider_id=source.id, feed_url=source.url, fetched_at=fetched_at, payload_sha256=digest,
                           items=tuple(items), rejected_rows=rejected, source_rows=len(rows),
                           provider_total_rows=provider_total, provider_has_more=provider_has_more,
                           provider_limited=provider_limited, flags=tuple(sorted(flags)))
    except (ValueError, TypeError, ET.ParseError, UnicodeError, OverflowError, DecimalException, RecursionError) as exc:
        if isinstance(exc, NewsPayloadError):
            raise
        raise NewsPayloadError("malformed news payload") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise NewsPayloadError("news redirects denied")


def public_get(url: str, timeout: float) -> bytes:
    if url not in {source.url for source in SOURCES.values() if source.collection_allowed}:
        raise NewsPayloadError("unregistered or restricted public endpoint")
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "Wallex-Agent-News/0.1",
        "Accept": "application/json, application/rss+xml, application/atom+xml, application/xml", "Accept-Encoding": "identity"})
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        if response.headers.get("Content-Encoding", "identity").lower() not in {"identity", ""}:
            raise NewsPayloadError("compressed news payload unsupported")
        body = cast(bytes, response.read(MAX_BODY_BYTES + 1))
    if len(body) > MAX_BODY_BYTES:
        raise NewsPayloadError("news payload too large")
    return body


@dataclass(frozen=True)
class FetchResult:
    capture: NewsCapture
    raw: bytes | None


class NewsHttpClient:
    def __init__(self, *, get: Callable[[str, float], bytes] = public_get,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._get, self._clock, self._sleep = get, clock, sleep

    def fetch(self, provider_id: str) -> FetchResult:
        if provider_id not in SOURCES:
            raise ValueError("unknown news provider")
        source = SOURCES[provider_id]
        raw: bytes | None = None
        error = "USAGE_RESTRICTED"
        if source.collection_allowed:
            for attempt in range(2):
                self._sleep(1)
                try:
                    raw = self._get(source.url, 10)
                    if len(raw) > MAX_BODY_BYTES:
                        raw = None
                        error = "PAYLOAD_TOO_LARGE"
                        break
                    fetched = self._clock()
                    return FetchResult(parse_capture(source, raw, fetched_at=fetched), raw)
                except urllib.error.HTTPError as exc:
                    error = f"HTTP_{exc.code}"
                    if exc.code < 500:
                        break
                except NewsPayloadError:
                    error = "MALFORMED_PAYLOAD"
                    break
                except (OSError, urllib.error.URLError):
                    error = "NETWORK_ERROR"
                if attempt == 0:
                    self._sleep(1)
        return FetchResult(NewsCapture(provider_id=source.id, feed_url=source.url, fetched_at=self._clock(),
            payload_sha256=sha256_hex(raw) if raw is not None else None, status="ERROR", flags=(error,), error_code=error), raw)
