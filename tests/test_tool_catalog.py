import json
from pathlib import Path

import pytest

from wallex_agent.contracts import Environment
from wallex_agent.tools import (
    CATALOG,
    LLM_CONSUMERS,
    Consumer,
    ToolRisk,
    UnknownToolError,
    exposed_tools,
    invariant_violations,
    spec_for,
)

FIXTURE = Path(__file__).parent / "fixtures" / "wallex_mcp_tool_names.json"


def test_every_advertised_tool_is_classified() -> None:
    advertised = set(json.loads(FIXTURE.read_text())["tool_names"])
    assert len(advertised) == 52
    assert advertised == set(CATALOG), "new or removed Wallex tool: review docs/WALLEX_TOOL_MAP.md"


def test_catalog_invariants_hold() -> None:
    assert invariant_violations() == []


def test_unknown_tool_is_denied() -> None:
    with pytest.raises(UnknownToolError):
        spec_for("wallex_spot_post_account_orders_v2")


@pytest.mark.parametrize("env", list(Environment))
@pytest.mark.parametrize("consumer", sorted(LLM_CONSUMERS))
def test_llm_consumers_only_ever_see_safe_reads(consumer: Consumer, env: Environment) -> None:
    for name in exposed_tools(consumer, env):
        assert CATALOG[name].risk is ToolRisk.SAFE_READ
        assert not CATALOG[name].requires_key


@pytest.mark.parametrize("consumer", sorted(LLM_CONSUMERS))
def test_backtest_gives_llm_no_live_wallex_tools(consumer: Consumer) -> None:
    assert exposed_tools(consumer, Environment.BACKTEST) == frozenset()


@pytest.mark.parametrize("env", [Environment.BACKTEST, Environment.SHADOW])
def test_no_write_tools_outside_live(env: Environment) -> None:
    for consumer in Consumer:
        for name in exposed_tools(consumer, env):
            assert CATALOG[name].risk not in {ToolRisk.WRITE_LOW, ToolRisk.TRADE_EXECUTION}


def test_dangerous_and_irrelevant_tools_reach_nobody() -> None:
    reachable = {name for consumer in Consumer for env in Environment for name in exposed_tools(consumer, env)}
    for name, spec in CATALOG.items():
        if spec.risk in {ToolRisk.DANGEROUS, ToolRisk.IRRELEVANT}:
            assert name not in reachable


def test_decision_agent_live_allowlist_is_exactly_three_reads() -> None:
    assert exposed_tools(Consumer.DECISION_AGENT, Environment.LIVE) == {
        "wallex_basic_candle_history",
        "wallex_basic_get_market_depth",
        "wallex_basic_get_latest_trades",
    }
