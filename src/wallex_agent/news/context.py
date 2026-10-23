"""Point-in-time context assembly; no network, trading policy or LLM calls."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from pydantic import Field

from wallex_agent.contracts.base import Contract
from wallex_agent.contracts.hashing import content_id

from .contracts import Asset, NewsCapture, NewsContext, NewsItem, NewsObservation, NewsStory
from .normalize import ASSETS, clean_text
from .store import NewsIntegrityError, NewsReader


class ContextLimits(Contract):
    max_stories: int = Field(default=6, ge=1, le=16)
    max_context_chars: int = Field(default=12000, ge=2048, le=50000)
    max_age_seconds: int = Field(default=86400, ge=1)
    max_provenance_per_story: int = Field(default=4, ge=1, le=8)


def cluster_items(items: Sequence[NewsItem]) -> tuple[tuple[NewsItem, ...], ...]:
    ordered = sorted({item.id: item for item in items}.values(), key=lambda item: item.id)
    parents = list(range(len(ordered)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    keys: dict[str, int] = {}
    for index, item in enumerate(ordered):
        headline = clean_text(item.headline).casefold()
        parsed = urlsplit(item.url)
        identity_keys = ["url:" + item.url] if parsed.path != "/" or parsed.query else []
        if len(headline) >= 40 and len(headline.split()) >= 5 and item.published_at is not None:
            identity_keys.append("headline:" + item.published_at.date().isoformat() + ":" + item.headline_sha256)
        if item.summary and len(item.summary) >= 40:
            identity_keys.append("content:" + content_id({"text_sha256": item.text_sha256,
                                                          "published_at": item.published_at}))
        for key in identity_keys:
            if key in keys:
                parents[root(index)] = root(keys[key])
            else:
                keys[key] = index
    groups: dict[int, list[NewsItem]] = defaultdict(list)
    for index, item in enumerate(ordered):
        groups[root(index)].append(item)
    return tuple(sorted((tuple(group) for group in groups.values()), key=lambda group: tuple(item.id for item in group)))


class CachedNewsProvider:
    def __init__(self, archive: NewsReader, *, enabled: bool = False, limits: ContextLimits | None = None) -> None:
        if type(enabled) is not bool:
            raise ValueError("news enabled flag must be a host-owned boolean")
        self._archive, self._enabled = archive, enabled
        self._limits = limits or ContextLimits()

    def context(self, decision_time: datetime, *, assets: tuple[str, ...] = ()) -> NewsContext:
        if decision_time.utcoffset() != timedelta(0):
            raise ValueError("news decision time must be timezone-aware UTC")
        if any(asset not in ASSETS for asset in assets):
            raise ValueError("unknown news asset")
        selected_assets: tuple[Asset, ...] = tuple(asset for asset in ASSETS if asset in assets)
        if not self._enabled:
            return NewsContext(as_of=decision_time, enabled=False, assets=selected_assets, flags=("DISABLED",))
        try:
            captures = self._archive.captures()
        except (NewsIntegrityError, OSError):
            return NewsContext(as_of=decision_time, enabled=True, assets=selected_assets, flags=("ARCHIVE_UNAVAILABLE",))
        visible = [capture for capture in captures if capture.fetched_at <= decision_time]
        latest: dict[str, NewsCapture] = {}
        for capture in sorted(visible, key=lambda item: (item.fetched_at, item.snapshot_id)):
            latest[capture.provider_id] = capture
        flags: set[str] = {"UNTRUSTED_CONTENT"}
        for provider, capture in latest.items():
            if capture.status == "ERROR":
                flags.add("PROVIDER_FAILURE:" + provider)
            if capture.rejected_rows or {"SOURCE_ITEM_LIMIT", "PROVIDER_LIMITED", "PROVIDER_PAGINATION"} & set(capture.flags):
                flags.add("PARTIAL_PROVIDER:" + provider)
            if (decision_time - capture.fetched_at).total_seconds() > self._limits.max_age_seconds:
                flags.add("STALE_CAPTURE:" + provider)
        versions: dict[tuple[str, str], dict[str, NewsItem]] = defaultdict(dict)
        for capture in visible:
            if capture.status != "OK":
                continue
            for item in capture.items:
                parsed = urlsplit(item.url)
                identity = item.url if parsed.path != "/" or parsed.query else item.id
                versions[(item.provider_id, identity)][item.id] = item
        eligible: list[NewsItem] = []
        histories: dict[str, tuple[NewsItem, ...]] = {}
        for family_by_id in versions.values():
            family = tuple(family_by_id.values())
            last_fetched = max(item.fetched_at for item in family)
            current = tuple(item for item in family if item.fetched_at == last_fetched)
            signatures = {content_id(item.model_dump(mode="python", exclude={"id", "fetched_at", "publisher", "provenance"}))
                          for item in current}
            if len(signatures) > 1:
                flags.add("CONFLICTING_VERSIONS_EXCLUDED")
                continue
            for item in current:
                if item.published_at is None:
                    flags.add("UNDATED_STORIES_EXCLUDED")
                    continue
                if item.published_at > decision_time:
                    flags.add("FUTURE_PUBLICATIONS_EXCLUDED")
                    continue
                elapsed = (decision_time - item.published_at).total_seconds()
                if elapsed >= self._limits.max_age_seconds:
                    flags.add("STALE_STORIES_EXCLUDED")
                    continue
                global_story = not item.assets and item.category in {"MACRO", "REGULATORY"}
                if selected_assets and not (set(item.assets) & set(selected_assets)) and not global_story:
                    continue
                eligible.append(item)
                histories[item.id] = tuple(observation for observation in family
                    if observation.published_at is not None and observation.published_at <= decision_time)
        groups = cluster_items(eligible)
        groups = tuple(sorted(groups, key=lambda group: (
            -max((item.published_at or datetime.min.replace(tzinfo=UTC)).timestamp() for item in group),
            tuple(item.id for item in group))))
        stories: list[NewsStory] = []
        story_publishers: list[set[str]] = []
        selected_publishers: set[str] = set()
        provider_count = sum(capture.status == "OK" for capture in latest.values())
        for group in groups:
            representative = max(group, key=lambda item: (item.fetched_at, item.id))
            assert representative.published_at is not None
            age = int((decision_time - representative.published_at).total_seconds())
            observations = sorted({observation.id: observation for item in group for observation in histories[item.id]}.values(),
                                  key=lambda item: (item.fetched_at, item.id), reverse=True)
            provenance = tuple(NewsObservation(item_id=item.id, provider_id=item.provider_id, publisher=item.publisher,
                fetched_at=item.fetched_at, payload_sha256=item.provenance.payload_sha256)
                for item in observations[:self._limits.max_provenance_per_story])
            story = NewsStory(cluster_id=content_id({"observations": [item.id for item in group]}), item=representative,
                age_seconds=age, expires_at=representative.published_at + timedelta(seconds=self._limits.max_age_seconds),
                freshness_remaining=Decimal(self._limits.max_age_seconds - age) / self._limits.max_age_seconds,
                observation_count=len(observations), source_count=len({item.publisher.casefold() for item in group if item.publisher is not None}),
                provenance=provenance, omitted_provenance_count=len(observations) - len(provenance))
            tentative = NewsContext(as_of=decision_time, enabled=True, assets=selected_assets, stories=tuple([*stories, story]),
                source_count=len(selected_publishers | {item.publisher.casefold() for item in group if item.publisher is not None}), provider_count=provider_count,
                cluster_count=len(groups), omitted_story_count=len(groups) - len(stories) - 1,
                flags=tuple(sorted(flags | {"CONTEXT_TRUNCATED"})))
            if len(stories) == self._limits.max_stories or len(tentative.model_dump_json()) > self._limits.max_context_chars:
                flags.add("CONTEXT_TRUNCATED")
                break
            stories.append(story)
            story_publishers.append({item.publisher.casefold() for item in group if item.publisher is not None})
            selected_publishers.update(item.publisher.casefold() for item in group if item.publisher is not None)
            if story.omitted_provenance_count:
                flags.add("PROVENANCE_TRUNCATED_IN_CONTEXT")
        if not stories:
            flags.add("EMPTY")
        while True:
            if not any(story.omitted_provenance_count for story in stories):
                flags.discard("PROVENANCE_TRUNCATED_IN_CONTEXT")
            result = NewsContext(as_of=decision_time, enabled=True, assets=selected_assets, stories=tuple(stories),
                source_count=len(selected_publishers), provider_count=provider_count, cluster_count=len(groups),
                omitted_story_count=len(groups) - len(stories), flags=tuple(sorted(flags)))
            if len(result.model_dump_json()) <= self._limits.max_context_chars:
                return result
            flags.add("CONTEXT_TRUNCATED")
            if not stories:
                return NewsContext(as_of=decision_time, enabled=True, assets=selected_assets, provider_count=provider_count,
                    cluster_count=len(groups), omitted_story_count=len(groups),
                    flags=("CONTEXT_TRUNCATED", "PROVIDER_METADATA_TRUNCATED", "EMPTY", "UNTRUSTED_CONTENT"))
            stories.pop()
            story_publishers.pop()
            selected_publishers = {publisher for publishers in story_publishers for publisher in publishers}
