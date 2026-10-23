"""news-overlay-0.1.0 is veto-only, deterministic and inert while News is disabled (the shipped default)."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from wallex_agent.news.context import CachedNewsProvider
from wallex_agent.news.overlay import news_gate
from wallex_agent.news.providers import SOURCES, parse_capture
from wallex_agent.news.store import NewsArchive

NOW = datetime(2026, 10, 22, 18, tzinfo=UTC)


def _article(title: str, minutes_ago: int, **changes: Any) -> dict[str, Any]:
    return {"title": title, "link": f"https://publisher.example/{hashlib.sha256(title.encode()).hexdigest()[:12]}",
            "pubDate": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "source": "Publisher",
            "description": "<p>A supplied short summary.</p>", **changes}


def _provider(tmp_path: Path, entries: list[dict[str, Any]], *, enabled: bool = True) -> CachedNewsProvider:
    body = json.dumps({"articles": entries, "lang": "en"}).encode()
    archive = NewsArchive(tmp_path)
    archive.record(parse_capture(SOURCES["cryptocurrency_cv"], body, fetched_at=NOW), body)
    return CachedNewsProvider(archive, enabled=enabled)


def test_disabled_news_has_no_effect_even_with_a_shock_story(tmp_path: Path) -> None:
    p = _provider(tmp_path, [_article("Federal Reserve emergency monetary policy statement", 30, category="macro")], enabled=False)
    gate = news_gate(p.context(NOW, assets=("BTC",)), "BTC")
    assert gate.action == "NO_EFFECT" and gate.reason == "DISABLED"


def test_fresh_global_macro_story_blocks_new_entries_with_provenance(tmp_path: Path) -> None:
    p = _provider(tmp_path, [_article("Federal Reserve emergency monetary policy statement", 30, category="macro")])
    ctx = p.context(NOW, assets=("BTC",))
    gate = news_gate(ctx, "BTC")
    assert gate.action == "BLOCK_NEW_ENTRIES" and gate.reason == "NEWS_SHOCK"
    assert gate.context_id == ctx.context_id and len(gate.story_ids) == 1


def test_old_or_other_asset_or_non_shock_stories_do_not_block(tmp_path: Path) -> None:
    p = _provider(tmp_path, [_article("Federal Reserve monetary policy statement from this morning", 300, category="macro"),
                             _article("Bitcoin regulatory filing about one specific asset", 20, category="regulatory"),
                             _article("Ethereum client release notes for node operators", 10)])
    assert news_gate(p.context(NOW, assets=("ETH",)), "ETH").action == "NO_EFFECT"
    assert news_gate(p.context(NOW, assets=("BTC",)), "BTC").action == "BLOCK_NEW_ENTRIES"


def test_unavailable_archive_is_recorded_not_guessed(tmp_path: Path) -> None:
    ctx = CachedNewsProvider(NewsArchive(tmp_path / "missing"), enabled=True).context(NOW, assets=("BTC",))
    gate = news_gate(ctx, "BTC")
    assert gate.action == "NO_EFFECT" and gate.reason == "NEWS_UNAVAILABLE"
