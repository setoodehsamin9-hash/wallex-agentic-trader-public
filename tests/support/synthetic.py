"""Shared synthetic data for runtime/shadow tests (importable via pytest `pythonpath = ["tests"]`)."""

import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from wallex_agent.market.bars import Bar

REPO = Path(__file__).resolve().parents[2]
EV1 = REPO / "research" / "phase-01" / "evidence"
START = datetime(2026, 10, 1, tzinfo=UTC)


def synthetic_bars(days: int, p0: float, seed_phase: float) -> list[Bar]:
    """Up-trending series with 3-hour swings: produces breakouts, pullbacks, stop-outs and take-profits."""
    out: list[Bar] = []
    for m in range(days * 1440):
        mid = p0 * math.exp(0.00011 * m + 0.012 * math.sin(2 * math.pi * m / 180 + seed_phase))
        o, c = mid * (1 - 0.0004), mid * (1 + 0.0004)
        out.append(Bar(START + timedelta(minutes=m), Decimal(round(o)), Decimal(round(max(o, c) * 1.001)),
                       Decimal(round(min(o, c) * 0.999)), Decimal(round(c)), Decimal("1.5")))
    return out


def metadata_dir(meta_dir: Path) -> None:
    meta_dir.mkdir(parents=True, exist_ok=True)
    for src, dst in (("_v1_markets.json", "v1_markets.json"), ("_hector_web_v1_markets.json", "hector_markets.json"),
                     ("_v1_prop_packages_183.json", "package_183.json")):
        body = json.loads((EV1 / src).read_text())["body"]
        (meta_dir / dst).write_text(body if isinstance(body, str) else json.dumps(body))
