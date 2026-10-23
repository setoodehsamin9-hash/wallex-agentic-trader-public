import json
import runpy
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


def test_optional_port_keeps_current_baseline_decision_identical(tmp_path: Path) -> None:
    from support.agent_context import decision_context

    from wallex_agent.agent.baseline import BaselineSelector
    from wallex_agent.news.context import CachedNewsProvider
    from wallex_agent.news.store import NewsArchive
    from wallex_agent.ports import NewsContextPort

    context = decision_context()
    before = BaselineSelector().decide(context)
    port: NewsContextPort = CachedNewsProvider(NewsArchive(tmp_path))
    assert not port.context(context.now, assets=("BTC",)).enabled
    assert BaselineSelector().decide(context) == before


def test_cli_context_default_is_disabled_and_never_fetches(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("offline CLI tried network")

    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/news_intelligence.py"))
    assert namespace["main"](["context", "--archive", str(tmp_path), "--as-of", "2026-10-22T18:00:00Z"]) == 0
    assert json.loads(capsys.readouterr().out)["enabled"] is False


def test_cli_offline_export_context_matches_original(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from wallex_agent.news.providers import SOURCES, parse_capture
    from wallex_agent.news.store import NewsArchive

    raw = b'{"articles":[{"title":"Bitcoin protocol release news","link":"https://publisher.example/release","source":"Publisher","pubDate":"2026-10-22T17:00:00Z"}]}'
    archive = NewsArchive(tmp_path / "source")
    archive.record(parse_capture(SOURCES["cryptocurrency_cv"], raw, fetched_at=datetime(2026, 10, 22, 18, tzinfo=UTC)), raw)
    archive.export(tmp_path / "export")
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/news_intelligence.py"))
    results: list[dict[str, Any]] = []
    for directory in (tmp_path / "source", tmp_path / "export"):
        assert namespace["main"](["context", "--enabled", "--archive", str(directory), "--as-of", "2026-10-22T18:00:00Z"]) == 0
        results.append(json.loads(capsys.readouterr().out))
    assert results[0] == results[1] and len(results[0]["stories"]) == 1
