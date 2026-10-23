"""Composition root: LIVE gets a decider (the runner then gates every write), but never a replay of recorded outputs."""

from collections.abc import Callable
from pathlib import Path

import pytest

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.contracts import Environment
from wallex_agent.journal.store import JournalStore
from wallex_agent.runtime.compose import LiveNotEnabled, build_decider
from wallex_agent.runtime.config import RunConfig
from wallex_agent.tools import Consumer, exposed_tools


def test_live_builds_a_decider_but_never_a_replay(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    live = make_cfg(**{"run.environment": Environment.LIVE, "run.live_autonomous_enabled": True})
    decider, _ = build_decider(live, store)
    assert isinstance(decider, BaselineSelector)
    replay = make_cfg(**{"run.environment": Environment.LIVE, "run.live_autonomous_enabled": True,
                         "agent.decision_provider": "REPLAY", "agent.replay_run_id": "old"})
    with pytest.raises(LiveNotEnabled):
        build_decider(replay, store)
    assert all("order" not in t.lower() or "get" in t.lower() for t in exposed_tools(Consumer.DECISION_AGENT, Environment.LIVE))
