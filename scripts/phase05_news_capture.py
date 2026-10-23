"""Phase 05 — bounded forward news capture for the SHADOW A/B/C comparison (public sources only, no key, no model).

  uv run python scripts/phase05_news_capture.py --until 2026-10-23T08:10:00+00:00 --every 900

Captures every collection-allowed registered source into an ignored, immutable archive (var/news-forward) at a fixed
cadence, outside the decision cycle. Each capture keeps its own fetched-UTC time, so a decision at T only ever sees
captures fetched at or before T (CachedNewsProvider's point-in-time rule). Stops at --until or the archive bound.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wallex_agent.news.normalize import parse_timestamp  # noqa: E402
from wallex_agent.news.providers import SOURCES, NewsHttpClient  # noqa: E402
from wallex_agent.news.store import NewsArchive  # noqa: E402

MAX_ROUNDS = 40


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, default=REPO / "var" / "news-forward")
    p.add_argument("--until", required=True)
    p.add_argument("--every", type=int, default=900)
    a = p.parse_args()
    until = parse_timestamp(a.until)
    if until is None:
        p.error("--until needs a timezone-aware timestamp")
    archive = NewsArchive(a.archive)
    client = NewsHttpClient()
    sources = [s.id for s in SOURCES.values() if s.collection_allowed]
    for n in range(MAX_ROUNDS):
        stamp = datetime.now(UTC)
        rows: list[dict[str, Any]] = []
        for sid in sources:
            r = client.fetch(sid)
            archive.record(r.capture, r.raw)
            rows.append({"provider": sid, "status": r.capture.status, "items": len(r.capture.items), "error": r.capture.error_code})
        print(json.dumps({"round": n, "at": stamp.isoformat(), "captures": rows}), flush=True)
        if datetime.now(UTC) + timedelta(seconds=a.every) > until:
            break
        time.sleep(a.every)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
