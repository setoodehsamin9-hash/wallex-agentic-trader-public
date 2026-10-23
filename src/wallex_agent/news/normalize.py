"""Deterministic text, URL, time and asset normalization. No LLM or remote reads."""

import ipaddress
import re
import unicodedata
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from wallex_agent.contracts.hashing import content_id

from .contracts import Asset, Category, NewsItem, NewsProvenance

ASSETS: tuple[Asset, ...] = ("BTC", "ETH", "USDT", "BNB", "XAUT")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    parser = _Text()
    parser.feed(value)
    text = unicodedata.normalize("NFKC", " ".join(parser.parts))
    return " ".join("".join(char for char in text if unicodedata.category(char) != "Cf").split())


def canonical_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(char) < 32 for char in value):
        raise ValueError("invalid news URL")
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None
            or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))):
        raise ValueError("unsafe news URL")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("IP news URLs are not allowed")
    if parsed.port not in {None, 80 if parsed.scheme == "http" else 443}:
        raise ValueError("unexpected news URL port")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if any(key.casefold() in {"api_key", "apikey", "access_token", "authorization", "token", "signature"} for key, _ in pairs):
        raise ValueError("credential-like news URL parameter")
    filtered = [(key, val) for key, val in pairs if not key.casefold().startswith("utm_")
                and key.casefold() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}]
    query = urlencode(sorted(filtered) if len({key for key, _ in filtered}) == len(filtered) else filtered)
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", query, ""))


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        try:
            parsed = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    try:
        return parsed.astimezone(UTC) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def map_assets(text: str) -> tuple[tuple[Asset, ...], tuple[str, ...]]:
    remaining = unicodedata.normalize("NFKC", text)
    unresolved: list[str] = []
    for phrase in ("Bitcoin Cash", "Bitcoin SV", "Ethereum Classic"):
        if re.search(r"\b" + re.escape(phrase) + r"\b", remaining, re.I):
            unresolved.append(phrase)
            remaining = re.sub(r"\b" + re.escape(phrase) + r"\b", " ", remaining, flags=re.I)
    found: set[Asset] = set()
    if re.search(r"\bTether Gold\b", remaining, re.I):
        found.add("XAUT")
        remaining = re.sub(r"\bTether Gold\b", " ", remaining, flags=re.I)
    names: dict[Asset, str] = {"BTC": "bitcoin", "ETH": "ethereum", "USDT": "tether", "BNB": "binance coin", "XAUT": "tether gold"}
    for asset, name in names.items():
        if re.search(r"\b" + asset + r"\b", remaining) or re.search(r"\b" + name + r"\b", remaining, re.I):
            found.add(asset)
    return tuple(asset for asset in ASSETS if asset in found), tuple(unresolved)


def normalize_item(raw: dict[str, Any], *, provider_id: str, publisher_default: str | None, feed_url: str,
                   fetched_at: datetime, payload_sha256: str, item_index: int, default_category: Category,
                   language: Any = None, provider_observed_at: datetime | None = None,
                   seen_only: bool = False) -> NewsItem:
    headline = clean_text(raw.get("title"))
    if not headline:
        raise ValueError("news title missing")
    summary = clean_text(raw.get("description")) or None
    headline_digest = content_id({"headline": headline.casefold()})
    text_digest = content_id({"headline": headline.casefold(), "summary": summary.casefold() if summary else None})
    published = None if seen_only else parse_timestamp(raw.get("pubDate"))
    flags = {"UNTRUSTED_CONTENT"}
    if published is None:
        flags.add("MISSING_PUBLICATION_TIME")
    publisher = clean_text(raw.get("source"))[:120] or publisher_default
    if publisher is None:
        flags.add("MISSING_PUBLISHER")
    if seen_only:
        flags.add("SEEN_TIME_IS_NOT_PUBLICATION")
    if len(headline) > 240 or (summary is not None and len(summary) > 280):
        flags.add("TEXT_TRUNCATED")
    headline = headline[:240]
    summary = summary[:280] if summary else None
    assets, unresolved = map_assets(headline + " " + (summary or ""))
    category: Category = default_category
    supplied = clean_text(raw.get("category")).casefold()
    if supplied in {"macro", "global", "economy", "economics", "monetary-policy"}:
        category = "MACRO"
    elif supplied in {"regulatory", "regulation", "policy"}:
        category = "REGULATORY"
    sentiment = clean_text(raw.get("sentiment"))[:64] or None
    provenance = NewsProvenance(provider_id=provider_id, feed_url=feed_url, payload_sha256=payload_sha256,
                                item_index=item_index, provider_observed_at=provider_observed_at)
    data: dict[str, Any] = {
        "schema_version": "1", "provider_id": provider_id,
        "publisher": publisher,
        "url": canonical_url(raw.get("link")), "published_at": published, "fetched_at": fetched_at,
        "headline": headline, "headline_sha256": headline_digest, "text_sha256": text_digest,
        "summary": summary, "assets": assets, "unresolved_entities": unresolved,
        "category": category, "language": clean_text(language)[:32] or None,
        "sentiment": sentiment, "sentiment_method": "PROVIDER" if sentiment else None,
        "importance": clean_text(raw.get("importance"))[:64] or None,
        "flags": tuple(sorted(flags)), "provenance": provenance.model_dump(mode="python"),
    }
    return NewsItem(id=content_id(data), **data)
