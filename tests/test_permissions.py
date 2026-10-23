import itertools

import pytest

from wallex_agent.contracts import AutonomyMode, Environment, PolicyOutcome
from wallex_agent.policy import ActionKind, PolicyContext, RunConfigurationError, check_run_allowed, decide


def _ctx(mode: AutonomyMode, env: Environment, *, kill: bool = False, live_auto: bool = False) -> PolicyContext:
    return PolicyContext(mode, env, kill_switch_latched=kill, live_autonomous_enabled=live_auto)


@pytest.mark.parametrize(("env", "action"), list(itertools.product(Environment, ActionKind)))
def test_advisor_never_executes(env: Environment, action: ActionKind) -> None:
    assert decide(action, _ctx(AutonomyMode.ADVISOR, env)).outcome is PolicyOutcome.PROPOSE_ONLY


@pytest.mark.parametrize("env", list(Environment))
def test_approval_mode_gates_ai_actions(env: Environment) -> None:
    ctx = _ctx(AutonomyMode.APPROVAL_REQUIRED, env)
    assert decide(ActionKind.AI_ENTRY, ctx).outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decide(ActionKind.AI_EXIT, ctx).outcome is PolicyOutcome.REQUIRE_APPROVAL
    assert decide(ActionKind.PROTECTIVE_EXIT, ctx).outcome is PolicyOutcome.EXECUTE
    assert decide(ActionKind.LIQUIDATION, ctx).outcome is PolicyOutcome.EXECUTE


@pytest.mark.parametrize("env", [Environment.BACKTEST, Environment.SHADOW])
def test_autonomous_executes_in_simulated_envs(env: Environment) -> None:
    assert decide(ActionKind.AI_ENTRY, _ctx(AutonomyMode.AUTONOMOUS, env)).outcome is PolicyOutcome.EXECUTE


def test_live_autonomous_entry_blocked_without_operator_gate() -> None:
    blocked = decide(ActionKind.AI_ENTRY, _ctx(AutonomyMode.AUTONOMOUS, Environment.LIVE))
    assert blocked.outcome is PolicyOutcome.BLOCKED
    allowed = decide(ActionKind.AI_ENTRY, _ctx(AutonomyMode.AUTONOMOUS, Environment.LIVE, live_auto=True))
    assert allowed.outcome is PolicyOutcome.EXECUTE


@pytest.mark.parametrize(
    ("mode", "env"),
    list(itertools.product([AutonomyMode.APPROVAL_REQUIRED, AutonomyMode.AUTONOMOUS], Environment)),
)
def test_kill_switch_blocks_entries_but_not_exits(mode: AutonomyMode, env: Environment) -> None:
    ctx = _ctx(mode, env, kill=True, live_auto=True)
    assert decide(ActionKind.AI_ENTRY, ctx).outcome is PolicyOutcome.BLOCKED
    assert decide(ActionKind.PROTECTIVE_EXIT, ctx).outcome is PolicyOutcome.EXECUTE
    assert decide(ActionKind.LIQUIDATION, ctx).outcome is PolicyOutcome.EXECUTE


def test_live_autonomous_run_refused_at_startup_without_gate() -> None:
    with pytest.raises(RunConfigurationError):
        check_run_allowed(AutonomyMode.AUTONOMOUS, Environment.LIVE, live_autonomous_enabled=False)
    check_run_allowed(AutonomyMode.AUTONOMOUS, Environment.LIVE, live_autonomous_enabled=True)
    check_run_allowed(AutonomyMode.APPROVAL_REQUIRED, Environment.LIVE, live_autonomous_enabled=False)
