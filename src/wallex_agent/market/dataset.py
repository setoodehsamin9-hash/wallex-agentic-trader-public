"""Hash-pinned datasets: `<dir>/manifest.json` + `<SYMBOL>_<minutes>m.csv.gz` per symbol.

Gzip is written with mtime=0 so the same bars always produce the same bytes and hash; the manifest hash of each
file is verified on load, so a backtest can only run on exactly the data its report names. Consumers default to
minute bars; research consumers must explicitly declare a different expected resolution.
"""

import csv
import gzip
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from wallex_agent.contracts import DataRef

from .bars import Bar
from .snapshot import bars_sha256


class DatasetIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class Dataset:
    name: str
    path: Path
    bars: dict[str, list[Bar]]
    refs: dict[str, DataRef]
    manifest: dict[str, Any]


def _encode(bars: list[Bar]) -> bytes:
    text = io.StringIO()
    w = csv.writer(text, lineterminator="\n")
    w.writerow(["t", "o", "h", "l", "c", "v"])
    for b in bars:
        w.writerow([int(b.ts_open.timestamp()), b.open, b.high, b.low, b.close, b.volume])
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        gz.write(text.getvalue().encode())
    return buf.getvalue()


def write_dataset(directory: Path, name: str, bars_by_symbol: dict[str, list[Bar]], *, extra: dict[str, Any],
                  resolution_seconds: int = 60) -> dict[str, Any]:
    if type(resolution_seconds) is not int or resolution_seconds <= 0 or resolution_seconds % 60:
        raise ValueError("resolution must be a positive whole number of minutes")
    directory.mkdir(parents=True, exist_ok=True)
    symbols: dict[str, Any] = {}
    for sym, bars in sorted(bars_by_symbol.items()):
        bars = sorted(bars, key=lambda b: b.ts_open)
        data = _encode(bars)
        fname = f"{sym}_{resolution_seconds // 60}m.csv.gz"
        (directory / fname).write_bytes(data)
        symbols[sym] = {
            "file": fname,
            "sha256": hashlib.sha256(data).hexdigest(),
            "bars_sha256": bars_sha256(bars),
            "bars": len(bars),
            "first_ts": bars[0].ts_open.isoformat() if bars else None,
            "last_ts": bars[-1].ts_open.isoformat() if bars else None,
        }
    manifest: dict[str, Any] = {**extra, "name": name, "resolution_seconds": resolution_seconds, "symbols": symbols}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False))
    return manifest


def load_dataset(directory: Path, *, expected_resolution_seconds: int = 60) -> Dataset:
    manifest: dict[str, Any] = json.loads((directory / "manifest.json").read_text())
    if manifest.get("resolution_seconds") != expected_resolution_seconds:
        raise DatasetIntegrityError("dataset resolution differs from the consumer's expected resolution")
    bars_by: dict[str, list[Bar]] = {}
    refs: dict[str, DataRef] = {}
    for sym, info in manifest["symbols"].items():
        data = (directory / info["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise DatasetIntegrityError(f"{sym}: file hash differs from manifest")
        rows = list(csv.reader(io.StringIO(gzip.decompress(data).decode())))[1:]
        bars = [Bar(datetime.fromtimestamp(int(r[0]), UTC), Decimal(r[1]), Decimal(r[2]), Decimal(r[3]), Decimal(r[4]),
                    Decimal(r[5])) for r in rows]
        bars_by[sym] = bars
        refs[sym] = DataRef(dataset_id=manifest["name"], content_sha256=info["sha256"], source="WALLEX_REST", symbol=sym,
                            first_ts=bars[0].ts_open, last_ts=bars[-1].ts_open,
                            resolution_seconds=expected_resolution_seconds)
    return Dataset(manifest["name"], directory, bars_by, refs, manifest)
