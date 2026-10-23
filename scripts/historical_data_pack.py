"""Collect/resume public candles, or deterministically regenerate the pack with --offline."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from wallex_agent.market.historical_pack import PublicArchive, build_pack, prefetch


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=Path("data/historical/collection_plan.json"))
    parser.add_argument("--archive", type=Path, default=Path("data/raw/historical-pack/http"))
    parser.add_argument("--out", type=Path, default=Path("data/raw/historical-pack/export"))
    parser.add_argument("--manifest", type=Path, default=Path("data/historical/MANIFEST.json"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    plan: dict[str, Any] = json.loads(args.plan.read_text())
    archive = PublicArchive(args.archive, offline=args.offline)
    def progress(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    attempts = prefetch(plan, archive, workers=args.workers, progress=progress)
    manifest = build_pack(plan, archive, args.out, progress=progress)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_bytes((args.out / "MANIFEST.json").read_bytes())
    print(json.dumps({"series": len(manifest["series"]), "public_http_attempts": attempts + archive.http_attempts,
                      "manifest": str(args.manifest), "offline": args.offline}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
