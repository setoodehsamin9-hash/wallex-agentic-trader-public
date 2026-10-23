import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 10, 22, 18, tzinfo=UTC)


def cv_body(articles: list[dict[str, Any]]) -> bytes:
    return json.dumps({"articles": articles, "lang": "en"}).encode()


def article(title: str = "Bitcoin protocol update published by the development team", **changes: Any) -> dict[str, Any]:
    return {"title": title, "link": "https://publisher.example/story?utm_source=feed&id=1#top",
            "pubDate": (NOW - timedelta(minutes=30)).isoformat(), "source": "Publisher",
            "description": "<p>A supplied short summary.</p>", **changes}


def capture(articles: list[dict[str, Any]], fetched_at: datetime = NOW) -> Any:
    from wallex_agent.news.providers import SOURCES, parse_capture

    return parse_capture(SOURCES["cryptocurrency_cv"], cv_body(articles), fetched_at=fetched_at)


def test_normalization_preserves_attribution_and_explicit_utc() -> None:
    result = capture([article(pubDate="2026-10-22T21:00:00+03:30")])
    item = result.items[0]
    assert item.published_at == datetime(2026, 10, 22, 17, 30, tzinfo=UTC)
    assert item.fetched_at == NOW
    assert item.publisher == "Publisher"
    assert item.url == "https://publisher.example/story?id=1"
    assert item.summary == "A supplied short summary."
    assert item.assets == ("BTC",)
    assert item.sentiment is None and item.importance is None
    assert item.provenance.payload_sha256 == result.payload_sha256
    assert item.provenance.provider_id == "cryptocurrency_cv"
    assert item.provenance.item_index == 0
    with pytest.raises(ValidationError):
        item.headline = "changed"


@pytest.mark.parametrize(("headline", "assets", "unresolved"), [
    ("Bitcoin and Ethereum update", ("BTC", "ETH"), ()),
    ("BTC ETH USDT BNB XAUT", ("BTC", "ETH", "USDT", "BNB", "XAUT"), ()),
    ("Tether Gold reserve report", ("XAUT",), ()),
    ("Tether Gold and USDT audit", ("USDT", "XAUT"), ()),
    ("Bitcoin Cash and Ethereum Classic roadmap", (), ("Bitcoin Cash", "Ethereum Classic")),
    ("Airbnb revenue beats forecasts", (), ()),
    ("Federal Reserve monetary policy statement", (), ()),
])
def test_deterministic_aliases_do_not_guess_ambiguous_tickers(
    headline: str, assets: tuple[str, ...], unresolved: tuple[str, ...],
) -> None:
    from wallex_agent.news.normalize import map_assets

    assert map_assets(headline) == (assets, unresolved)


@pytest.mark.parametrize("date", [None, "2026-10-22T17:30:00", "not-a-date"])
def test_missing_or_naive_source_publication_is_not_invented(date: str | None) -> None:
    result = capture([article(pubDate=date)])
    assert result.items[0].published_at is None
    assert "MISSING_PUBLICATION_TIME" in result.items[0].flags


def test_aggregator_without_original_publisher_does_not_invent_attribution() -> None:
    item = capture([article(source=None)]).items[0]
    assert item.publisher is None
    assert item.provider_id == "cryptocurrency_cv"
    assert "MISSING_PUBLISHER" in item.flags


def test_rss_and_atom_dates_are_parsed_without_fetching_links() -> None:
    from wallex_agent.news.providers import SOURCES, parse_capture

    rss = b'<rss version="2.0"><channel><language>en-US</language><item><title>Federal Reserve statement</title><link>https://www.federalreserve.gov/newsevents/pressreleases/monetary.htm</link><pubDate>Thu, 22 Oct 2026 17:00:00 GMT</pubDate><description>Policy update.</description></item></channel></rss>'
    fed = parse_capture(SOURCES["federal_reserve"], rss, fetched_at=NOW).items[0]
    assert fed.category == "MACRO" and fed.assets == ()
    assert fed.published_at == NOW - timedelta(hours=1)
    atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Bitcoin Core release</title><link href="https://bitcoincore.org/en/releases/test/"/><published>2026-10-22T17:00:00Z</published><summary>Release details</summary></entry></feed>'
    core = parse_capture(SOURCES["bitcoin_core"], atom, fetched_at=NOW).items[0]
    assert core.assets == ("BTC",) and core.publisher == "Bitcoin Core"


def test_gdelt_seen_time_is_not_claimed_as_publication_time() -> None:
    from wallex_agent.news.providers import SOURCES, parse_capture

    raw = json.dumps({"articles": [{"url": "https://publisher.example/macro", "title": "Inflation report",
                                   "seendate": "20261022T170000Z", "domain": "publisher.example", "language": "English"}]}).encode()
    item = parse_capture(SOURCES["gdelt"], raw, fetched_at=NOW).items[0]
    assert item.published_at is None
    assert item.provenance.provider_observed_at == NOW - timedelta(hours=1)
    assert "SEEN_TIME_IS_NOT_PUBLICATION" in item.flags


@pytest.mark.parametrize("raw", [b'not json', b'{"success":false,"articles":[]}', b'{"articles":{}}',
    b'<!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss><channel>&xxe;</channel></rss>'])
def test_malformed_and_entity_payloads_fail_without_content_recovery(raw: bytes) -> None:
    from wallex_agent.news.providers import NewsPayloadError, SOURCES, parse_capture

    source = SOURCES["federal_reserve"] if raw.startswith(b'<!') else SOURCES["cryptocurrency_cv"]
    with pytest.raises(NewsPayloadError):
        parse_capture(source, raw, fetched_at=NOW)


def test_bad_rows_are_reported_without_losing_valid_rows() -> None:
    result = capture([article(link="file:///secrets/key"), article(), {"title": "missing URL"}])
    assert len(result.items) == 1 and result.rejected_rows == 2
    assert "REJECTED_ROWS" in result.flags


def test_context_does_not_hide_partial_provider_payload(tmp_path: Path) -> None:
    from wallex_agent.news.context import CachedNewsProvider
    from wallex_agent.news.store import NewsArchive

    rows = [article(link="file:///unusable")]
    archive = NewsArchive(tmp_path)
    archive.record(capture(rows), cv_body(rows))
    context = CachedNewsProvider(archive, enabled=True).context(NOW)
    assert context.stories == () and "PARTIAL_PROVIDER:cryptocurrency_cv" in context.flags


def test_dedupe_clusters_syndication_without_losing_observations() -> None:
    from wallex_agent.news.context import cluster_items

    first = capture([article()]).items[0]
    second = capture([article(link="https://syndicator.example/report", source="Syndicator")]).items[0]
    third = capture([article(link="https://publisher.example/story?id=1&utm_medium=rss")], NOW + timedelta(minutes=1)).items[0]
    groups = cluster_items([second, third, first])
    assert len(groups) == 1 and len(groups[0]) == 3
    assert {item.publisher for item in groups[0]} == {"Publisher", "Syndicator"}
    assert cluster_items([first, second, third]) == groups


def test_generic_short_headlines_do_not_collapse_distinct_articles() -> None:
    from wallex_agent.news.context import cluster_items

    items = capture([article(title="Market update", link="https://publisher.example/a", description=None),
                     article(title="Market update", link="https://publisher.example/b", description=None)]).items
    assert len(cluster_items(items)) == 2


def test_publisher_homepage_is_not_an_article_identity() -> None:
    from wallex_agent.news.context import cluster_items

    items = capture([article(title="Bitcoin protocol developers announce a distinct software update", link="https://publisher.example/"),
                     article(title="Ethereum protocol developers announce another software update", link="https://publisher.example/")]).items
    assert len(cluster_items(items)) == 2


def test_snapshot_roundtrip_tamper_detection_and_export(tmp_path: Path) -> None:
    from wallex_agent.news.store import NewsArchive, NewsIntegrityError

    snapshot = capture([article()])
    raw = cv_body([article()])
    archive = NewsArchive(tmp_path / "archive")
    identity = archive.record(snapshot, raw)
    assert archive.record(snapshot, raw) == identity
    assert archive.captures() == (snapshot,)
    archive.export(tmp_path / "export")
    assert NewsArchive(tmp_path / "export").captures() == (snapshot,)
    (tmp_path / "archive" / "snapshots" / (identity + ".json")).write_bytes(b'{}')
    with pytest.raises(NewsIntegrityError):
        archive.captures()


def test_context_filters_publication_and_observation_before_clustering(tmp_path: Path) -> None:
    from wallex_agent.news.context import CachedNewsProvider
    from wallex_agent.news.store import NewsArchive

    archive = NewsArchive(tmp_path)
    available = article()
    future_publication = article(title="Future Ethereum announcement", pubDate=(NOW + timedelta(hours=1)).isoformat(), link="https://publisher.example/future")
    archive.record(capture([available, future_publication]), cv_body([available, future_publication]))
    revised = article(title="Bitcoin revised with information learned tomorrow", link=available["link"])
    archive.record(capture([revised], NOW + timedelta(days=1)), cv_body([revised]))
    provider = CachedNewsProvider(archive, enabled=True)
    context = provider.context(NOW, assets=("BTC",))
    assert len(context.stories) == 1
    assert context.stories[0].item.headline == available["title"]
    assert context.stories[0].age_seconds == 1800
    assert context.source_count == 1
    assert "FUTURE_OBSERVATIONS_EXCLUDED" not in context.flags
    assert "FUTURE_PUBLICATIONS_EXCLUDED" in context.flags
    assert context == provider.context(NOW, assets=("BTC",))
    archive_without_future = NewsArchive(tmp_path / "past-only")
    archive_without_future.record(capture([available, future_publication]), cv_body([available, future_publication]))
    assert CachedNewsProvider(archive_without_future, enabled=True).context(NOW, assets=("BTC",)) == context


def test_global_context_and_bounds_are_explicit(tmp_path: Path) -> None:
    from wallex_agent.news.context import CachedNewsProvider, ContextLimits
    from wallex_agent.news.store import NewsArchive

    entries = [article(title=f"Bitcoin network announcement number {index} with distinct information", link=f"https://publisher.example/{index}") for index in range(10)]
    entries.append(article(title="Federal Reserve monetary policy statement", category="macro", link="https://publisher.example/fed"))
    archive = NewsArchive(tmp_path)
    archive.record(capture(entries), cv_body(entries))
    provider = CachedNewsProvider(archive, enabled=True, limits=ContextLimits(max_stories=2, max_context_chars=6000))
    context = provider.context(NOW, assets=("ETH",))
    assert len(context.stories) == 1 and context.stories[0].item.category == "MACRO"
    bounded = provider.context(NOW)
    assert len(bounded.stories) <= 2 and len(bounded.model_dump_json()) <= 6000
    assert "CONTEXT_TRUNCATED" in bounded.flags


def test_asset_specific_regulatory_story_is_not_reclassified_as_global(tmp_path: Path) -> None:
    from wallex_agent.news.context import CachedNewsProvider
    from wallex_agent.news.store import NewsArchive

    entries = [article(title="Bitcoin regulatory filing about one specific asset", category="regulatory")]
    archive = NewsArchive(tmp_path)
    archive.record(capture(entries), cv_body(entries))
    provider = CachedNewsProvider(archive, enabled=True)
    assert provider.context(NOW, assets=("ETH",)).stories == ()
    assert len(provider.context(NOW, assets=("BTC",)).stories) == 1


def test_undated_stale_news_and_unsupported_assets_are_not_silently_used(tmp_path: Path) -> None:
    from wallex_agent.news.context import CachedNewsProvider
    from wallex_agent.news.store import NewsArchive

    entries = [article(pubDate=None), article(pubDate=(NOW - timedelta(days=5)).isoformat(), link="https://publisher.example/old")]
    archive = NewsArchive(tmp_path)
    archive.record(capture(entries), cv_body(entries))
    provider = CachedNewsProvider(archive, enabled=True)
    result = provider.context(NOW)
    assert result.stories == ()
    assert {"UNDATED_STORIES_EXCLUDED", "STALE_STORIES_EXCLUDED"} <= set(result.flags)
    with pytest.raises(ValueError):
        provider.context(NOW, assets=("UNKNOWN",))


def test_default_disabled_port_does_not_read_or_change_existing_prompt() -> None:
    from support.agent_context import decision_context

    from wallex_agent.agent.decision_agent import render_user_prompt
    from wallex_agent.news.context import CachedNewsProvider

    class ForbiddenReader:
        def captures(self) -> tuple[Any, ...]:
            raise AssertionError("disabled news read storage")

    before = render_user_prompt(decision_context())
    result = CachedNewsProvider(ForbiddenReader()).context(NOW)
    assert not result.enabled and result.stories == () and "DISABLED" in result.flags
    assert render_user_prompt(decision_context()) == before
