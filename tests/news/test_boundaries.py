import io
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from wallex_agent.contracts.hashing import content_id
from wallex_agent.news.context import CachedNewsProvider, ContextLimits
from wallex_agent.news.contracts import NewsCapture, NewsItem
from wallex_agent.news.providers import MAX_BODY_BYTES, SOURCES, NewsHttpClient, NewsPayloadError, public_get
from wallex_agent.news.store import NewsArchive, NewsIntegrityError

NOW = datetime(2026, 10, 22, 18, tzinfo=UTC)
BODY = b'{"articles":[{"title":"Bitcoin network release announcement with details","link":"https://publisher.example/release","pubDate":"2026-10-22T17:00:00Z","source":"Publisher"}]}'


def test_read_retry_is_bounded_and_capture_uses_host_clock() -> None:
    calls: list[str] = []
    waits: list[float] = []

    def get(url: str, timeout: float) -> bytes:
        calls.append(url)
        if len(calls) == 1:
            raise TimeoutError("temporary read failure")
        return BODY

    result = NewsHttpClient(get=get, clock=lambda: NOW, sleep=waits.append).fetch("cryptocurrency_cv")
    assert result.capture.status == "OK" and result.capture.fetched_at == NOW
    assert len(calls) == 2 and sum(waits) >= 3
    assert result.raw == BODY


def test_rate_limit_stops_read_without_retrying_or_hiding_failure(tmp_path: Path) -> None:
    calls: list[str] = []

    def get(url: str, timeout: float) -> bytes:
        calls.append(url)
        raise urllib.error.HTTPError(url, 429, "rate limited", Message(), None)

    result = NewsHttpClient(get=get, clock=lambda: NOW, sleep=lambda _: None).fetch("gdelt")
    assert len(calls) == 1 and result.capture.status == "ERROR"
    assert result.capture.error_code == "HTTP_429"
    archive = NewsArchive(tmp_path)
    archive.record(result.capture, result.raw)
    assert "PROVIDER_FAILURE:gdelt" in CachedNewsProvider(archive, enabled=True).context(NOW).flags


def test_permission_restricted_or_unknown_provider_never_calls_http() -> None:
    def forbidden_get(url: str, timeout: float) -> bytes:
        raise AssertionError("unapproved provider request")

    client = NewsHttpClient(get=forbidden_get, clock=lambda: NOW, sleep=lambda _: None)
    assert client.fetch("coindesk").capture.error_code == "USAGE_RESTRICTED"
    with pytest.raises(ValueError):
        client.fetch("unknown")


@pytest.mark.parametrize("url", ["https://api.wallex.ir/v1/account/balances", "https://api.wallex.ir/v1/orders",
    "http://cryptocurrency.cv/api/news?limit=50", "https://127.0.0.1/private", "https://cryptocurrency.cv/api/ai"])
def test_unregistered_or_wallex_endpoints_are_denied_before_transport(url: str) -> None:
    with pytest.raises(NewsPayloadError):
        public_get(url, 1)


def test_transport_only_builds_get_without_auth_and_enforces_body_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[urllib.request.Request] = []

    class Response(io.BytesIO):
        headers = Message()

    class Opener:
        def open(self, request: urllib.request.Request, timeout: float) -> Response:
            requests.append(request)
            return Response(b'x' * (MAX_BODY_BYTES + 1))

    def build(*handlers: urllib.request.BaseHandler) -> Opener:
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    with pytest.raises(NewsPayloadError, match="large"):
        public_get(SOURCES["cryptocurrency_cv"].url, 1)
    assert requests[0].get_method() == "GET"
    assert not ({"authorization", "x-api-key"} & {key.lower() for key in requests[0].headers})


def test_redirect_handler_denies_second_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def build(*handlers: Any) -> Any:
        class Opener:
            def open(self, request: urllib.request.Request, timeout: float) -> Any:
                return handlers[0].redirect_request(request, None, 302, "redirect", Message(), "https://api.wallex.ir/private")
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    with pytest.raises(NewsPayloadError, match="redirect"):
        public_get(SOURCES["federal_reserve"].url, 1)


def test_oversized_custom_get_does_not_poison_archive(tmp_path: Path) -> None:
    result = NewsHttpClient(get=lambda url, timeout: b'x' * (MAX_BODY_BYTES + 1),
                            clock=lambda: NOW, sleep=lambda _: None).fetch("cryptocurrency_cv")
    assert result.capture.status == "ERROR" and result.raw is None
    archive = NewsArchive(tmp_path)
    archive.record(result.capture, result.raw)
    assert archive.captures() == (result.capture,)


def test_read_contract_rejects_forged_unsafe_url_and_mismatched_provider() -> None:
    item = NewsHttpClient(get=lambda url, timeout: BODY, clock=lambda: NOW, sleep=lambda _: None).fetch("cryptocurrency_cv").capture.items[0]
    for defect in ("url", "provenance"):
        data = item.model_dump(mode="python", exclude={"id"})
        if defect == "url":
            data["url"] = "javascript:alert(1)"
        else:
            data["provenance"]["provider_id"] = "other"
        with pytest.raises(ValueError):
            NewsItem(id=content_id(data), **data)


def test_context_metadata_alone_cannot_exceed_budget() -> None:
    class Reader:
        def captures(self) -> tuple[NewsCapture, ...]:
            return tuple(NewsCapture(provider_id="provider-" + str(index) + "-" + "x" * 40,
                feed_url="https://provider.example/feed", fetched_at=NOW, status="ERROR", error_code="UNAVAILABLE")
                for index in range(80))

    context = CachedNewsProvider(Reader(), enabled=True, limits=ContextLimits(max_context_chars=2048)).context(NOW)
    assert len(context.model_dump_json()) <= 2048
    assert "CONTEXT_TRUNCATED" in context.flags


def test_snapshot_name_and_raw_symlink_fail_closed(tmp_path: Path) -> None:
    result = NewsHttpClient(get=lambda url, timeout: BODY, clock=lambda: NOW, sleep=lambda _: None).fetch("cryptocurrency_cv")
    archive = NewsArchive(tmp_path)
    archive.record(result.capture, result.raw)
    payload = tmp_path / "raw" / (str(result.capture.payload_sha256) + ".bin")
    payload.unlink()
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("unrelated local data")
    payload.symlink_to(unrelated)
    with pytest.raises(NewsIntegrityError):
        archive.captures()


def test_cache_replay_cannot_touch_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = NewsHttpClient(get=lambda url, timeout: BODY, clock=lambda: NOW, sleep=lambda _: None).fetch("cryptocurrency_cv")
    archive = NewsArchive(tmp_path)
    archive.record(result.capture, result.raw)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("offline replay tried HTTP")

    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    assert len(CachedNewsProvider(archive, enabled=True).context(NOW).stories) == 1


def test_provenance_truncation_flag_cannot_push_context_over_budget(tmp_path: Path) -> None:
    rows = [{"title": "Bitcoin network release announcement with long technical details", "link": "https://publisher.example/release",
             "pubDate": "2026-10-22T17:00:00Z", "source": "Publisher " + str(index),
             "description": "A supplied summary of the release. " * 6} for index in range(8)]
    body = json.dumps({"articles": rows}).encode()
    result = NewsHttpClient(get=lambda url, timeout: body, clock=lambda: NOW, sleep=lambda _: None).fetch("cryptocurrency_cv")
    archive = NewsArchive(tmp_path)
    archive.record(result.capture, result.raw)
    full = CachedNewsProvider(archive, enabled=True).context(NOW)
    budget = len(full.model_dump_json()) - 1
    assert budget >= 2048
    bounded = CachedNewsProvider(archive, enabled=True, limits=ContextLimits(max_context_chars=budget)).context(NOW)
    assert len(bounded.model_dump_json()) <= budget


def test_repeated_query_parameters_preserve_their_semantic_order() -> None:
    from wallex_agent.news.normalize import canonical_url

    assert canonical_url("https://publisher.example/news?id=1&id=2") != canonical_url("https://publisher.example/news?id=2&id=1")


def test_freshness_expiry_is_exclusive_without_rounding_away_elapsed_time(tmp_path: Path) -> None:
    from wallex_agent.news.providers import parse_capture

    raw = b'{"articles":[{"title":"Bitcoin protocol news","link":"https://publisher.example/news","pubDate":"2026-10-21T17:59:59.500000Z"}]}'
    archive = NewsArchive(tmp_path)
    archive.record(parse_capture(SOURCES["cryptocurrency_cv"], raw, fetched_at=NOW), raw)
    assert CachedNewsProvider(archive, enabled=True).context(NOW).stories == ()


def test_archive_stops_listing_when_the_capture_bound_is_exceeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "snapshots").mkdir()

    def bounded_glob(path: Path, pattern: str) -> Any:
        yield tmp_path / ("a" * 64 + ".json")
        yield tmp_path / ("b" * 64 + ".json")
        raise AssertionError("archive enumerated beyond its bound")

    monkeypatch.setattr(Path, "glob", bounded_glob)
    with pytest.raises(NewsIntegrityError, match="bound"):
        NewsArchive(tmp_path, max_snapshots=1).captures()
