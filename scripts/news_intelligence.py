"""Explicit public capture, local snapshot export and offline point-in-time NewsContext."""

import argparse
import json
from pathlib import Path

from wallex_agent.news.context import CachedNewsProvider, ContextLimits
from wallex_agent.news.normalize import parse_timestamp
from wallex_agent.news.providers import SOURCES, NewsHttpClient
from wallex_agent.news.store import NewsArchive


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--archive", type=Path, default=Path("var/news-intelligence"))
    fetch.add_argument("--sources", nargs="+", choices=tuple(SOURCES), default=[source.id for source in SOURCES.values() if source.collection_allowed])
    context = commands.add_parser("context")
    context.add_argument("--archive", type=Path, default=Path("var/news-intelligence"))
    context.add_argument("--as-of", required=True, help="Explicit timezone-aware decision time; never inferred from publication")
    context.add_argument("--assets", nargs="*", default=[])
    context.add_argument("--enabled", action="store_true")
    context.add_argument("--max-age-seconds", type=int, default=86400)
    export = commands.add_parser("export")
    export.add_argument("--archive", type=Path, default=Path("var/news-intelligence"))
    export.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    archive = NewsArchive(args.archive)
    if args.command == "fetch":
        client = NewsHttpClient()
        captures: list[dict[str, str | int | None]] = []
        for provider in dict.fromkeys(args.sources):
            result = client.fetch(provider)
            identity = archive.record(result.capture, result.raw)
            captures.append({"provider": provider, "snapshot": identity, "status": result.capture.status,
                             "items": len(result.capture.items), "rejected": result.capture.rejected_rows,
                             "error": result.capture.error_code})
        print(json.dumps({"captures": captures}, sort_keys=True))
        return 0 if any(capture["status"] == "OK" for capture in captures) else 1
    if args.command == "context":
        decision_time = parse_timestamp(args.as_of)
        if decision_time is None:
            parser.error("--as-of requires an explicit timezone-aware timestamp")
        provider = CachedNewsProvider(archive, enabled=args.enabled, limits=ContextLimits(max_age_seconds=args.max_age_seconds))
        print(provider.context(decision_time, assets=tuple(args.assets)).model_dump_json())
        return 0
    archive.export(args.out)
    print(json.dumps({"exported_snapshots": len(archive.captures()), "directory": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
