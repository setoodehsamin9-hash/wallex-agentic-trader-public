"""Fetch public Wallex 1m candles in 1-day chunks into a hash-pinned dataset (read-only, no key).

Usage:
  uv run python scripts/fetch_history.py --symbols BTCTMN ETHTMN --days 45 --end 2026-10-22T00:00:00Z \
      --name wallex-1m-btctmn-ethtmn --out data/datasets/wallex-1m-btctmn-ethtmn

Wallex caps the range per request (a 3-day request returned 1 bar), so each call covers one UTC day.
Coverage and gaps are written into the manifest; missing minutes are reported, never filled.
"""

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wallex_agent.market.bars import Bar, parse_udf_history  # noqa: E402
from wallex_agent.market.dataset import write_dataset  # noqa: E402
from wallex_agent.market.wallex_public import WallexPublicClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--days", type=int, required=True)
    ap.add_argument("--end", required=True, help="UTC ISO end (exclusive), aligned to a day")
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    end = datetime.fromisoformat(args.end.replace("Z", "+00:00")).astimezone(UTC)
    start = end - timedelta(days=args.days)
    client = WallexPublicClient(min_interval_s=0.6)
    fetched_at = datetime.now(UTC)

    bars_by: dict[str, list[Bar]] = {}
    coverage: dict[str, dict[str, object]] = {}
    for sym in args.symbols:
        seen: dict[datetime, Bar] = {}
        flags: set[str] = set()
        day = start
        while day < end:
            frm, to = int(day.timestamp()), int((day + timedelta(days=1)).timestamp()) - 1
            payload = client.candles(sym, "1", frm, to)
            bars, fl = parse_udf_history(payload, 60, now=fetched_at)
            flags |= {f.value for f in fl}
            for b in bars:
                if start <= b.ts_open < end:
                    seen[b.ts_open] = b
            day += timedelta(days=1)
            print(f"{sym} {day.date()} total={len(seen)}", file=sys.stderr)
        ordered = [seen[k] for k in sorted(seen)]
        expected = int((end - start).total_seconds() // 60)
        missing = expected - len(ordered)
        bars_by[sym] = ordered
        coverage[sym] = {"expected_bars": expected, "bars": len(ordered), "missing_bars": missing,
                         "coverage": round(len(ordered) / expected, 6), "chunk_flags": sorted(flags)}

    meta_dir = args.out / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    for name, fn in (("v1_markets.json", client.markets_v1), ("hector_markets.json", client.markets_hector)):
        (meta_dir / name).write_text(json.dumps(fn(), default=str, ensure_ascii=False))
    (meta_dir / "package_183.json").write_text(json.dumps(client.package(183), default=str, ensure_ascii=False))

    manifest = write_dataset(args.out, args.name, bars_by, extra={
        "source": "https://api.wallex.ir/v1/udf/history resolution=1 (public, no key)",
        "range_utc": [start.isoformat(), end.isoformat()],
        "fetched_at_utc": fetched_at.isoformat(),
        "coverage": coverage,
        "metadata_snapshot": "metadata/ (markets + package 183 at fetch time; universe version for this dataset)",
    })
    print(json.dumps({"symbols": manifest["symbols"], "coverage": coverage}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
