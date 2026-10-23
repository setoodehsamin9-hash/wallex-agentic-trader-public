"""The two independent run dimensions. They are host-owned configuration, fixed for a run,
and never something the model can change through a tool argument or prompt text."""

from enum import StrEnum


class AutonomyMode(StrEnum):
    """How far an AI-originated decision may travel without a human."""

    ADVISOR = "ADVISOR"
    """Analyse and propose only. Nothing is ever executed, not even in simulation."""

    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    """A risk-approved intent waits for an explicit, single-use human approval."""

    AUTONOMOUS = "AUTONOMOUS"
    """Risk-approved intents execute automatically, inside deterministic limits."""


class Environment(StrEnum):
    """Where executions land. Selected by which adapters the composition root wires in."""

    BACKTEST = "BACKTEST"
    """Historical data, virtual clock, simulated fills. No network writes; no live market reads."""

    SHADOW = "SHADOW"
    """Live market data, real clock, simulated fills. The live write adapter is never constructed."""

    LIVE = "LIVE"
    """Live market data and real Wallex orders on the challenge account."""
