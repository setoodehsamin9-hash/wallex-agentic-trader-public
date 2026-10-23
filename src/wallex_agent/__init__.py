"""Wallex agentic trader.

Layout (see docs/ARCHITECTURE.md):
    contracts/   versioned data contracts shared by every component (no I/O)
    policy/      deterministic permission policy: autonomy mode x environment
    tools/       Wallex MCP tool catalog and default-deny exposure rules
    ports.py     interfaces that environment adapters (backtest/shadow/live) implement

Everything that can move capital lives behind the deterministic path
AgentDecision -> validation -> permission policy -> risk engine -> execution adapter.
"""

__version__ = "0.1.0"
