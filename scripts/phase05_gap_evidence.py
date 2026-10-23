"""Phase 05 — evidence for the decision-window data-quality rule (data quality only; no strategy is evaluated).

  uv run python scripts/phase05_gap_evidence.py   ->  reports/phase-05/GAP_EVIDENCE.json

For every 4h decision time (F2 contiguity window = 49 buckets) on the hash-checked Codex pack, BTCTMN and ETHTMN:
  A strict   : previous BACKTEST rule (last bar at as_of + every spacing in the last 49 buckets exact) on 4h buckets
               aggregated from 1m bars only (a single missing minute drops its whole bucket)
  B quality  : window-quality-1 on the same minute-only buckets
  C combined : window-quality-1 on buckets built from complete minute hours + native 1h candles where minutes are
               missing (never interpolated)
plus strict vs window-quality-1 on the native 1h series alone over its whole history.
"""

import json
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wallex_agent.market.bars import Bar, aggregate  # noqa: E402
from wallex_agent.market.dataset import load_dataset  # noqa: E402
from wallex_agent.market.quality import assess_window  # noqa: E402
from wallex_agent.market.warmup import combine_hours  # noqa: E402

PACK = REPO / "data" / "raw" / "historical-pack" / "export" / "datasets"
IV, NEEDED = 14400, 49
OUT = REPO / "reports" / "phase-05" / "GAP_EVIDENCE.json"


def strict(hist: list[Bar], as_of_ts: int) -> bool:
    """True = blocked under the previous rule (runtime/backtest.py `_flags`)."""
    if not hist or int(hist[-1].ts_open.timestamp()) + IV != as_of_ts:
        return True
    tail = hist[-NEEDED:]
    return any(int((b.ts_open - a.ts_open).total_seconds()) != IV for a, b in zip(tail, tail[1:]))


def evaluate(buckets: list[Bar], first_ts: int, last_ts: int) -> dict[str, Any]:
    stat: Counter[str] = Counter()
    blocked_strict = 0
    n = 0
    t = first_ts + (NEEDED + 2) * IV
    idx = 0
    while t <= last_ts:
        while idx < len(buckets) and int(buckets[idx].ts_open.timestamp()) + IV <= t:
            idx += 1
        hist = buckets[max(0, idx - 400):idx]
        as_of = buckets[0].ts_open + timedelta(seconds=t - int(buckets[0].ts_open.timestamp()))
        q = assess_window(hist, as_of, IV, needed=NEEDED)
        stat[q.status] += 1
        blocked_strict += strict(hist, t)
        n += 1
        t += IV
    blocked_q = stat["COVERAGE_FAILURE"] + stat["STALE"] + stat["MALFORMED"]
    return {"decisions": n, "strict_blocked_share": round(blocked_strict / n, 4),
            "quality_blocked_share": round(blocked_q / n, 4), "quality_status": dict(sorted(stat.items()))}


def main() -> None:
    m1 = load_dataset(PACK / "1m")
    h1 = load_dataset(PACK / "1h", expected_resolution_seconds=3600)
    out: dict[str, Any] = {"rule": "window-quality-1", "needed_buckets": NEEDED, "interval_s": IV, "symbols": {}}
    for sym in ("BTCTMN", "ETHTMN"):
        minutes = m1.bars[sym]
        lo, hi = minutes[0].ts_open, minutes[-1].ts_open
        native = [b for b in h1.bars[sym] if lo <= b.ts_open <= hi]
        first, last = int(lo.timestamp()) // IV * IV, (int(hi.timestamp()) + 60) // IV * IV
        gaps = [(b.ts_open - a.ts_open).total_seconds() / 60 - 1 for a, b in zip(minutes, minutes[1:]) if b.ts_open - a.ts_open != timedelta(minutes=1)]
        minute_buckets = aggregate(minutes, IV)
        combined = aggregate(combine_hours(minutes, native), IV, 3600)
        res: dict[str, Any] = {"minute_period": [lo.isoformat(), hi.isoformat()], "minute_gaps": len(gaps),
               "missing_minutes": int(sum(gaps)), "longest_minute_gap": int(max(gaps, default=0)),
               "four_h_buckets_dropped_minute_only": (last - first) // IV - len(minute_buckets),
               "four_h_buckets_dropped_combined": (last - first) // IV - len(combined)}
        res["A_strict_minute_only"] = evaluate(minute_buckets, first, last)
        res["B_quality_minute_only"] = evaluate(minute_buckets, first, last)
        res["C_quality_combined"] = evaluate(combined, first, last)
        hb = h1.bars[sym]
        hourly_buckets = aggregate(hb, IV, 3600)
        f2, l2 = int(hb[0].ts_open.timestamp()) // IV * IV, (int(hb[-1].ts_open.timestamp()) + 3600) // IV * IV
        res["native_1h_4y"] = {"period": [hb[0].ts_open.isoformat(), hb[-1].ts_open.isoformat()],
                               **evaluate(hourly_buckets, f2, l2)}
        out["symbols"][sym] = res
        print(sym, json.dumps(res, indent=None)[:900])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
