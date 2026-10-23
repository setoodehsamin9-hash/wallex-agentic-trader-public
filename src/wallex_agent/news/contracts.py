"""Immutable observations and bounded, untrusted news context."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from wallex_agent.contracts.base import Contract, UtcDatetime
from wallex_agent.contracts.hashing import content_id

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Asset = Literal["BTC", "ETH", "USDT", "BNB", "XAUT"]
Category = Literal["CRYPTO", "MACRO", "REGULATORY", "TECHNOLOGY", "OTHER"]


class NewsProvenance(Contract):
    provider_id: str = Field(min_length=1, max_length=64)
    feed_url: str = Field(max_length=2048)
    payload_sha256: Digest
    item_index: int = Field(ge=0)
    normalizer_version: Literal["news-v1"] = "news-v1"
    provider_observed_at: UtcDatetime | None = None


class NewsItem(Contract):
    schema_version: Literal["1"] = "1"
    id: Digest
    provider_id: str = Field(min_length=1, max_length=64)
    publisher: str | None = Field(default=None, min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=2048)
    published_at: UtcDatetime | None
    fetched_at: UtcDatetime
    headline: str = Field(min_length=1, max_length=240)
    headline_sha256: Digest
    text_sha256: Digest
    summary: str | None = Field(default=None, max_length=280)
    assets: tuple[Asset, ...] = ()
    unresolved_entities: tuple[str, ...] = ()
    category: Category
    language: str | None = Field(default=None, max_length=32)
    sentiment: str | None = Field(default=None, max_length=64)
    sentiment_method: Literal["PROVIDER"] | None = None
    importance: str | None = Field(default=None, max_length=64)
    flags: tuple[str, ...] = ()
    provenance: NewsProvenance

    @model_validator(mode="after")
    def identity_matches(self) -> "NewsItem":
        from .normalize import canonical_url

        if canonical_url(self.url) != self.url or self.provider_id != self.provenance.provider_id:
            raise ValueError("unsafe URL or inconsistent news provenance")
        canonical_url(self.provenance.feed_url)
        if self.id != content_id(self.model_dump(mode="python", exclude={"id"})):
            raise ValueError("news observation content hash mismatch")
        return self


class NewsCapture(Contract):
    schema_version: Literal["1"] = "1"
    provider_id: str = Field(min_length=1, max_length=64)
    feed_url: str = Field(max_length=2048)
    fetched_at: UtcDatetime
    payload_sha256: Digest | None = None
    status: Literal["OK", "ERROR"] = "OK"
    items: tuple[NewsItem, ...] = Field(default=(), max_length=50)
    rejected_rows: int = Field(default=0, ge=0)
    source_rows: int = Field(default=0, ge=0)
    provider_total_rows: int | None = Field(default=None, ge=0)
    provider_has_more: bool | None = None
    provider_limited: bool | None = None
    flags: tuple[str, ...] = ()
    error_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def observation_boundary(self) -> "NewsCapture":
        if self.status == "ERROR" and self.items:
            raise ValueError("failed capture cannot contain usable news")
        for item in self.items:
            if (item.fetched_at != self.fetched_at or item.provider_id != self.provider_id
                    or item.provenance.payload_sha256 != self.payload_sha256 or item.provenance.feed_url != self.feed_url):
                raise ValueError("item provenance differs from its capture")
        return self

    @property
    def snapshot_id(self) -> str:
        return content_id(self)


class NewsObservation(Contract):
    item_id: Digest
    provider_id: str
    publisher: str | None
    fetched_at: UtcDatetime
    payload_sha256: Digest


class NewsStory(Contract):
    cluster_id: Digest
    item: NewsItem
    age_seconds: int = Field(ge=0)
    expires_at: UtcDatetime
    freshness_remaining: Decimal = Field(ge=0, le=1, allow_inf_nan=False)
    observation_count: int = Field(ge=1)
    source_count: int = Field(ge=0)
    provenance: tuple[NewsObservation, ...]
    omitted_provenance_count: int = Field(ge=0)


class NewsContext(Contract):
    schema_version: Literal["1"] = "1"
    as_of: UtcDatetime
    enabled: bool
    assets: tuple[Asset, ...] = ()
    stories: tuple[NewsStory, ...] = ()
    source_count: int = Field(default=0, ge=0)
    provider_count: int = Field(default=0, ge=0)
    cluster_count: int = Field(default=0, ge=0)
    omitted_story_count: int = Field(default=0, ge=0)
    flags: tuple[str, ...] = ()
    trust: Literal["UNTRUSTED_NEWS_DATA_NOT_INSTRUCTIONS"] = "UNTRUSTED_NEWS_DATA_NOT_INSTRUCTIONS"

    @property
    def context_id(self) -> str:
        return content_id(self)
