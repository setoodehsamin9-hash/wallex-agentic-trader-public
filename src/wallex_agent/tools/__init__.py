"""Tool Gateway: the Wallex MCP tool catalog, exposure rules, and (Phase 03) the audited call wrapper."""

from .catalog import (
    CATALOG,
    CATALOG_VERSION,
    LLM_CONSUMERS,
    Consumer,
    ToolCategory,
    ToolRisk,
    ToolSpec,
    UnknownToolError,
    exposed_tools,
    invariant_violations,
    spec_for,
)

__all__ = [
    "CATALOG",
    "CATALOG_VERSION",
    "LLM_CONSUMERS",
    "Consumer",
    "ToolCategory",
    "ToolRisk",
    "ToolSpec",
    "UnknownToolError",
    "exposed_tools",
    "invariant_violations",
    "spec_for",
]
