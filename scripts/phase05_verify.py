"""Phase 05 — SHADOW zero-write proof (static + journal evidence; no network, no model).

  uv run python scripts/phase05_verify.py zero-write --run sh-f2-p05 [--journal var/state.sqlite]

Static: the public client reaches the network only through `urllib_get` (GET, no credential header); the SHADOW
runtime imports no private/live adapter or transport. Journal: the run is SHADOW and every execution event's fill
source is the SHADOW simulator. `real_wallex_writes` counts exchange-sourced events (must be 0).
"""

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wallex_agent.journal.store import JournalStore  # noqa: E402

SRC = REPO / "src" / "wallex_agent"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
    return out


def zero_write(run: str, journal: Path) -> dict[str, Any]:
    public = (SRC / "market" / "wallex_public.py").read_text()
    static = {
        "public_client_get_only": all(m not in public for m in ('"POST"', '"DELETE"', '"PUT"', "method=")),
        "public_client_no_key_header": "X-API-Key" not in public,
        "shadow_imports_no_live_adapter": not any("wallex_live" in m or "transport" in m.lower()
                                                  for m in _imports(SRC / "runtime" / "shadow.py")),
    }
    store = JournalStore(journal)
    manifest = store.manifest(run)
    events = store.events(run)
    sources: dict[str, int] = {}
    for e in events:
        ev: dict[str, Any] = e.get("event", {})
        src = str(ev.get("source", "?"))
        sources[src] = sources.get(src, 0) + 1
    real = sum(n for s, n in sources.items() if s not in ("SHADOW_SIM",))
    env = manifest.environment.value if manifest is not None else None
    return {"run": run, "environment": env, "static": static,
            "execution_events": len(events), "event_sources": sources, "trades": len(store.trades(run)),
            "decisions": len(store.decisions(run)), "real_wallex_writes": real,
            "pass": env == "SHADOW" and all(static.values()) and real == 0}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    z = sub.add_parser("zero-write")
    z.add_argument("--run", required=True)
    z.add_argument("--journal", type=Path, default=REPO / "var" / "state.sqlite")
    a = p.parse_args()
    res = zero_write(a.run, a.journal)
    print(json.dumps(res, indent=1))
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
