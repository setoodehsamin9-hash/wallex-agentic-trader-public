"""Read-only inspection of the PUBLIC Wallex MCP server: initialize + tools/list, nothing else.

Usage:  uv run python scripts/inspect_wallex_mcp.py [--out PATH]

- Never sends X-API-Key (it refuses to run if WALLEX_API_KEY is set, to avoid accidental use).
- Never issues tools/call. Use it to detect drift between the live tool list and
  src/wallex_agent/tools/catalog.py; a new or removed tool means docs/WALLEX_TOOL_MAP.md needs review.
"""

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, cast

ENDPOINT = "https://api.wallex.ir/mcp"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def _rpc(method: str, params: dict[str, Any], rpc_id: int) -> dict[str, Any]:
    body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}).encode()
    request = urllib.request.Request(ENDPOINT, data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        return cast(dict[str, Any], json.loads(response.read()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    if os.environ.get("WALLEX_API_KEY"):
        print("refusing: WALLEX_API_KEY is set; this script is public/unauthenticated only", file=sys.stderr)
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from wallex_agent.tools import CATALOG

    init = _rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                               "clientInfo": {"name": "wallex-agent-inspect", "version": "0.1"}}, 1)
    listing = _rpc("tools/list", {}, 2)
    tools = cast(list[dict[str, Any]], listing["result"]["tools"])
    live = {cast(str, t["name"]) for t in tools}
    print("server:", init["result"]["serverInfo"], "protocol:", init["result"]["protocolVersion"])
    print("live tools:", len(live), "catalogued:", len(CATALOG))
    print("new (UNCLASSIFIED, denied):", sorted(live - set(CATALOG)))
    print("missing from server:", sorted(set(CATALOG) - live))
    if args.out:
        args.out.write_text(json.dumps(listing, ensure_ascii=False, indent=1))
    return 0 if live == set(CATALOG) else 1


if __name__ == "__main__":
    raise SystemExit(main())
