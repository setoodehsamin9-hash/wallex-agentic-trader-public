import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from wallex_agent.news.context import CachedNewsProvider, cluster_items
from wallex_agent.news.providers import SOURCES, NewsHttpClient, parse_capture
from wallex_agent.news.store import NewsArchive, NewsIntegrityError

NOW = datetime(2026, 10, 22, 18, tzinfo=UTC)


def body(title: str = "Bitcoin protocol team publishes an important technical update", *,
         publication: str | None = "2026-10-22T17:30:00Z", url: str = "https://publisher.example/story") -> bytes:
    return json.dumps({"articles": [{"title": title, "link": url, "source": "Publisher", "pubDate": publication}]}).encode()


def save(archive: NewsArchive, raw: bytes, fetched: datetime) -> None:
    archive.record(parse_capture(SOURCES["cryptocurrency_cv"], raw, fetched_at=fetched), raw)


@pytest.mark.parametrize("correction", ["old_date", "asset", "missing_date", "future_date"])
def test_latest_known_correction_supersedes_old_eligible_version(tmp_path: Path, correction: str) -> None:
    archive = NewsArchive(tmp_path)
    original = body()
    save(archive, original, NOW - timedelta(minutes=10))
    provider = CachedNewsProvider(archive, enabled=True)
    before = provider.context(NOW - timedelta(minutes=8), assets=("BTC",))
    assert len(before.stories) == 1
    if correction == "old_date":
        revised = body(publication="2026-10-12T17:30:00Z")
    elif correction == "asset":
        revised = body("Ethereum developers publish a revised technical update")
    elif correction == "missing_date":
        revised = body(publication=None)
    else:
        revised = body(publication="2026-10-23T17:30:00Z")
    save(archive, revised, NOW - timedelta(minutes=5))
    assert provider.context(NOW, assets=("BTC",)).stories == ()
    assert provider.context(NOW - timedelta(minutes=8), assets=("BTC",)) == before


def test_truncated_display_headlines_do_not_manufacture_equivalence() -> None:
    prefix = "Bitcoin protocol teams discuss the implementation and public policy. " * 5
    raw = json.dumps({"articles": [
        {"title": prefix + "Approved", "description": "Approved proposal", "link": "https://publisher.example/yes", "pubDate": "2026-10-22T17:00:00Z"},
        {"title": prefix + "Rejected", "description": "Rejected proposal", "link": "https://publisher.example/no", "pubDate": "2026-10-22T17:00:00Z"}]}).encode()
    items = parse_capture(SOURCES["cryptocurrency_cv"], raw, fetched_at=NOW).items
    assert items[0].headline == items[1].headline
    assert len(cluster_items(items)) == 2


def test_truncated_summaries_do_not_manufacture_equivalence() -> None:
    prefix = "Public protocol implementation details and background information. " * 6
    raw = json.dumps({"articles": [
        {"title": "Protocol update", "description": prefix + "Approved", "link": "https://publisher.example/yes"},
        {"title": "Protocol update", "description": prefix + "Rejected", "link": "https://publisher.example/no"}]}).encode()
    items = parse_capture(SOURCES["cryptocurrency_cv"], raw, fetched_at=NOW).items
    assert items[0].summary == items[1].summary
    assert len(cluster_items(items)) == 2


def test_conflicting_simultaneous_revisions_are_explicitly_excluded(tmp_path: Path) -> None:
    value = json.loads(body())
    value["articles"].extend(json.loads(body("Ethereum developers publish a conflicting technical update"))["articles"])
    archive = NewsArchive(tmp_path)
    save(archive, json.dumps(value).encode(), NOW)
    context = CachedNewsProvider(archive, enabled=True).context(NOW)
    assert context.stories == () and "CONFLICTING_VERSIONS_EXCLUDED" in context.flags


def test_predictable_temporary_symlink_cannot_overwrite_unrelated_file(tmp_path: Path) -> None:
    raw = body()
    archive = NewsArchive(tmp_path / "archive")
    raw_dir = archive.directory / "raw"
    raw_dir.mkdir(parents=True)
    victim = tmp_path / "unrelated.txt"
    victim.write_bytes(b'original unrelated data')
    (raw_dir / (hashlib.sha256(raw).hexdigest() + ".tmp")).symlink_to(victim)
    save(archive, raw, NOW)
    assert victim.read_bytes() == b'original unrelated data'
    assert len(archive.captures()) == 1


def test_archive_directory_indirection_is_rejected_before_write(tmp_path: Path) -> None:
    archive = NewsArchive(tmp_path / "archive")
    archive.directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (archive.directory / "raw").symlink_to(outside, target_is_directory=True)
    with pytest.raises(NewsIntegrityError, match="symlink"):
        save(archive, body(), NOW)
    assert not list(outside.iterdir())


def test_archive_directory_indirection_is_rejected_on_replay(tmp_path: Path) -> None:
    archive = NewsArchive(tmp_path / "archive")
    save(archive, body(), NOW)
    raw_dir = archive.directory / "raw"
    outside = tmp_path / "outside"
    raw_dir.rename(outside)
    raw_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(NewsIntegrityError, match="symlink"):
        archive.captures()


def test_provider_advertised_limits_remain_visible_in_capture_and_context(tmp_path: Path) -> None:
    value = json.loads(body())
    value.update(limited=True, totalCount=100, pagination={"hasMore": True})
    raw = json.dumps(value).encode()
    capture = parse_capture(SOURCES["cryptocurrency_cv"], raw, fetched_at=NOW)
    assert {"PROVIDER_LIMITED", "PROVIDER_PAGINATION"} <= set(capture.flags)
    assert capture.provider_total_rows == 100 and capture.provider_has_more is True
    archive = NewsArchive(tmp_path)
    archive.record(capture, raw)
    assert "PARTIAL_PROVIDER:cryptocurrency_cv" in CachedNewsProvider(archive, enabled=True).context(NOW).flags


@pytest.mark.parametrize("raw", [
    b'{"fetchedAt":"9999-12-31T23:59:59-01:00","articles":[]}',
    b'{"articles":[],"number":1e999999999999999999999999}',
    b'{"articles":' + b'[' * 1500 + b']' * 1500 + b'}',
], ids=["utc-overflow", "decimal-overflow", "json-depth"])
def test_malformed_provider_boundary_is_contained_as_failure(raw: bytes) -> None:
    result = NewsHttpClient(get=lambda url, timeout: raw, clock=lambda: NOW, sleep=lambda _: None).fetch("cryptocurrency_cv")
    assert result.capture.status == "ERROR" and result.capture.error_code == "MALFORMED_PAYLOAD"


def test_enabled_missing_archive_is_not_a_valid_empty_replay(tmp_path: Path) -> None:
    context = CachedNewsProvider(NewsArchive(tmp_path / "missing"), enabled=True).context(NOW)
    assert context.stories == () and "ARCHIVE_UNAVAILABLE" in context.flags


def test_explicitly_initialized_empty_archive_exports_as_empty(tmp_path: Path) -> None:
    archive = NewsArchive(tmp_path / "empty")
    (archive.directory / "snapshots").mkdir(parents=True)
    archive.export(tmp_path / "export")
    context = CachedNewsProvider(NewsArchive(tmp_path / "export"), enabled=True).context(NOW)
    assert "EMPTY" in context.flags and "ARCHIVE_UNAVAILABLE" not in context.flags
