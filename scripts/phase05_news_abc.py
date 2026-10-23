"""Phase 05 — forward News A/B/C comparison on journaled SHADOW decisions (offline replay; no model, no orders).

  uv run python scripts/phase05_news_abc.py --run sh-f2-p05   ->  reports/phase-05/NEWS_ABC.json

A = F2 technical only (regime filter off), re-derived from each decision's journaled features with the same entry
    rule (close >= EMA48 + 0.5 ATR and close > close 48 bars ago);
B = what the SHADOW actually ran (F2 + regime-r2): the journaled candidates;
C = B + news-overlay-0.1.0 veto, with the NewsContext read point-in-time (captures fetched <= the decision time).
News can only remove an entry (veto); it never creates one. Small samples are reported INCONCLUSIVE.
"""

import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wallex_agent.journal.store import JournalStore  # noqa: E402
from wallex_agent.news.context import CachedNewsProvider  # noqa: E402
from wallex_agent.news.contracts import Asset  # noqa: E402
from wallex_agent.news.overlay import news_gate  # noqa: E402
from wallex_agent.news.store import NewsArchive  # noqa: E402

ASSET: dict[str, Asset] = {"BTCTMN": "BTC", "ETHTMN": "ETH"}
MIN_SAMPLE = 30


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", required=True)
    p.add_argument("--journal", type=Path, default=REPO / "var" / "state.sqlite")
    p.add_argument("--archive", type=Path, default=REPO / "var" / "news-forward")
    p.add_argument("--out", type=Path, default=REPO / "reports" / "phase-05" / "NEWS_ABC.json")
    a = p.parse_args()
    store = JournalStore(a.journal)
    news = CachedNewsProvider(NewsArchive(a.archive), enabled=True)
    rows: list[dict[str, Any]] = []
    for d in store.decisions(a.run):
        if d.features_ref is None or d.symbol not in ASSET:
            continue
        raw = store.get_payload(d.features_ref)
        if raw is None:
            continue
        ctx: dict[str, Any] = json.loads(raw)
        qflags: list[str] = list(ctx["snapshot"]["quality_flags"])
        f = {x["feature_id"]: (Decimal(x["value"]) if x["value"] is not None else None) for x in ctx["features"]["features"]}
        s = d.symbol
        close, atr, ema, lag = f.get(f"{s}.close.1"), f.get(f"{s}.atr.14"), f.get(f"{s}.ema.48"), f.get(f"{s}.close_lag.48")
        a_entry = (close is not None and atr is not None and ema is not None and lag is not None
                   and close >= ema + Decimal("0.5") * atr and close > lag)
        b_entry = bool(ctx["candidates"])
        as_of = datetime.fromisoformat(ctx["snapshot"]["as_of"])
        gate = news_gate(news.context(as_of, assets=(ASSET[s],)), ASSET[s])
        c_entry = b_entry and gate.action != "BLOCK_NEW_ENTRIES"
        rows.append({"symbol": s, "as_of": as_of.isoformat(), "status": d.status.value, "quality_flags": qflags,
                     "A_entry_signal": bool(a_entry), "B_candidate": b_entry, "C_after_news": c_entry,
                     "news_action": gate.action, "news_reason": gate.reason, "news_context_id": gate.context_id,
                     "news_story_ids": list(gate.story_ids), "close": str(close), "ema48": str(ema),
                     "regime": {"er_rank": str(f.get(f"{s}.er_rank.42")), "vol_rank": str(f.get(f"{s}.vol_rank.14"))}})
    n = len(rows)
    summary = {"decisions": n, "A_entries": sum(r["A_entry_signal"] for r in rows), "B_candidates": sum(r["B_candidate"] for r in rows),
               "C_entries": sum(r["C_after_news"] for r in rows), "news_vetoes": sum(r["B_candidate"] and not r["C_after_news"] for r in rows),
               "news_block_states": sum(r["news_action"] == "BLOCK_NEW_ENTRIES" for r in rows),
               "news_unavailable": sum(r["news_reason"] == "NEWS_UNAVAILABLE" for r in rows),
               "verdict": "INCONCLUSIVE" if n < MIN_SAMPLE or sum(r["B_candidate"] for r in rows) == 0 else "MEASURED",
               "note": "news is veto-only; subsequent-price and P&L comparison needs entries and forward data (none yet)"}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"run": a.run, "summary": summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
