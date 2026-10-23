"""Deterministic permission policy: autonomy mode x action kind -> what may happen.

Environment does not loosen or tighten the outcome, with one exception: LIVE + AUTONOMOUS entries
need an explicit operator gate (`live_autonomous_enabled`), because Wallex's MCP access rules ask
for explicit user confirmation before state-changing actions and the challenge brief asks for
automatic execution; that tension (U04) must be resolved with the organiser before it is switched on.
Environment decides WHERE an EXECUTE lands (simulator, shadow simulator, or exchange), which is
wired by the composition root, not by this module.

This module never sees model output directly. It is called by the orchestrator after the
decision validator, and before the risk engine. The risk engine still runs on every EXECUTE and
on every approval, and can reject or reduce what this policy allows.
"""

from dataclasses import dataclass
from enum import StrEnum

from wallex_agent.contracts import AutonomyMode, Environment, PolicyOutcome

POLICY_VERSION = "permissions-1.0.0"


class ActionKind(StrEnum):
    AI_ENTRY = "AI_ENTRY"
    """New exposure chosen by the agent (AgentAction.ENTER)."""
    AI_EXIT = "AI_EXIT"
    """Early close requested by the agent (AgentAction.EXIT_POSITION)."""
    PROTECTIVE_EXIT = "PROTECTIVE_EXIT"
    """Stop / take-profit / time exit fixed in the candidate the entry was opened from."""
    LIQUIDATION = "LIQUIDATION"
    """End-of-challenge conversion to TMN, or kill-switch flattening."""


class RunConfigurationError(ValueError):
    """The requested run is not allowed to start."""


@dataclass(frozen=True, slots=True)
class PolicyContext:
    autonomy: AutonomyMode
    environment: Environment
    kill_switch_latched: bool
    live_autonomous_enabled: bool


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    outcome: PolicyOutcome
    reason: str
    policy_version: str = POLICY_VERSION


def check_run_allowed(autonomy: AutonomyMode, environment: Environment, *, live_autonomous_enabled: bool) -> None:
    """Called once at startup. LIVE + AUTONOMOUS must be switched on explicitly in config."""
    if environment is Environment.LIVE and autonomy is AutonomyMode.AUTONOMOUS and not live_autonomous_enabled:
        raise RunConfigurationError(
            "LIVE + AUTONOMOUS requires live_autonomous_enabled=true (pending organiser answer on U04)"
        )


def decide(action: ActionKind, ctx: PolicyContext) -> PolicyDecision:
    if ctx.autonomy is AutonomyMode.ADVISOR:
        return PolicyDecision(PolicyOutcome.PROPOSE_ONLY, "advisor mode never executes")

    if action is ActionKind.AI_ENTRY and ctx.kill_switch_latched:
        return PolicyDecision(PolicyOutcome.BLOCKED, "kill switch latched: no new exposure")

    if action in (ActionKind.PROTECTIVE_EXIT, ActionKind.LIQUIDATION):
        # Risk-reducing and pre-declared: the stop/TP/time plan was part of the approved entry,
        # and liquidation is part of the run's declared policy. Never waits for a human or the AI.
        return PolicyDecision(PolicyOutcome.EXECUTE, "risk-reducing exit fixed before entry")

    if ctx.autonomy is AutonomyMode.APPROVAL_REQUIRED:
        return PolicyDecision(PolicyOutcome.REQUIRE_APPROVAL, "approval mode: explicit single-use approval required")

    # AUTONOMOUS
    if (
        action is ActionKind.AI_ENTRY
        and ctx.environment is Environment.LIVE
        and not ctx.live_autonomous_enabled
    ):
        return PolicyDecision(PolicyOutcome.BLOCKED, "live autonomous entries are not enabled")
    return PolicyDecision(PolicyOutcome.EXECUTE, "autonomous mode inside risk limits")
