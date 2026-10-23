"""Phase 05 — natural frequency of frozen F2 candidates and entries (offline; no model; no network; zero writes).

  uv run python scripts/phase05_f2_frequency.py

One BASELINE kernel backtest of frozen F2 (config/experiments/phase04r2/base-4h-f2.toml) over VALIDATION
2025-03-01..2026-10-13 on the hash-checked 1h pack. Per symbol and 4h decision boundary it records whether F2 offered a
candidate and whether the deterministic selector entered. Reports how often a candidate exists, how long candidate
states persist (P(candidate at t+4h | candidate at t)), entries per day, and the chance of >= 1 candidate / entry within
the next k boundaries. Descriptive only: it changes no parameter and is not a forecast of profit.
"""

import json
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wallex_agent.agent.baseline import BaselineSelector  # noqa: E402
from wallex_agent.journal.store import JournalStore  # noqa: E402
from wallex_agent.market.dataset import load_dataset  # noqa: E402
from wallex_agent.runtime.backtest import run_backtest  # noqa: E402
from wallex_agent.runtime.config import load_run_config  # noqa: E402

CFG = REPO / "config/experiments/phase04r2/base-4h-f2.toml"
PERIOD = (datetime(2025, 3, 1, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC))
OUT = REPO / "reports/phase-05/F2_NATURAL_FREQUENCY.json"
STEP = timedelta(hours=4)


def main() -> int:
    cfg = load_run_config(CFG)
    ds = load_dataset(REPO / "data/raw/historical-pack/export/datasets/1h", expected_resolution_seconds=3600)
    store = JournalStore(Path(tempfile.mkdtemp()) / "freq.sqlite")
    rt = run_backtest(cfg, ds, store, BaselineSelector(), run_id="freq", period=PERIOD).runtime
    entries = {t.decision_id for t in rt.ledger.trades}
    cand: dict[str, dict[datetime, bool]] = defaultdict(dict)
    entered: dict[str, dict[datetime, bool]] = defaultdict(dict)
    for d in store.decisions("freq"):
        at = datetime.fromisoformat(d.cycle_id.split("@")[1])
        sym = d.symbol
        assert sym is not None
        cand[sym][at] = bool(d.candidates)
        entered[sym][at] = d.decision_id in entries
    times = sorted({t for v in cand.values() for t in v})
    days = (PERIOD[1] - PERIOD[0]).total_seconds() / 86400
    per_symbol: dict[str, Any] = {}
    for s, m in cand.items():
        ts = sorted(m)
        pairs = [(m[a], m[a + STEP]) for a in ts if a + STEP in m]
        after = [b for a, b in pairs if a]
        per_symbol[s] = {"boundaries": len(ts), "candidate_share": sum(m.values()) / len(ts),
                         "persistence_p_next_given_now": sum(after) / len(after) if after else None,
                         "entries": sum(entered[s].values()), "entries_per_day": sum(entered[s].values()) / days}

    def any_within(source: dict[str, dict[datetime, bool]], k: int) -> float:
        hits = [any(source[s].get(t + i * STEP, False) for s in source for i in range(1, k + 1)) for t in times]
        return sum(hits) / len(hits)

    both_now = [t for t in times if all(cand[s].get(t, False) for s in cand)]
    nxt = [any(cand[s].get(t + STEP, False) for s in cand) for t in both_now]
    result = {
        "period": [p.isoformat() for p in PERIOD], "strategy": "f2-0.1.0 (frozen)", "decision_interval_h": 4,
        "per_symbol": per_symbol,
        "p_any_candidate_within_next_k_boundaries": {k: any_within(cand, k) for k in (1, 2, 3, 6, 18)},
        "p_any_entry_within_next_k_boundaries": {k: any_within(entered, k) for k in (1, 2, 3, 6, 18)},
        "p_next_boundary_candidate_given_both_symbols_now": sum(nxt) / len(nxt) if nxt else None,
        "both_symbols_candidate_boundaries": len(both_now),
        "total_entries": sum(v["entries"] for v in per_symbol.values()),
        "entries_per_day_both_symbols": sum(v["entries"] for v in per_symbol.values()) / days,
        "note": "entries need a flat book: while a position is open new candidates are not entries. Descriptive only.",
    }
    OUT.write_text(json.dumps(result, indent=1, default=str) + "\n")
    print(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
