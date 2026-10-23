"""SHADOW crash -> resume through the real runner: recovery first, then the same loop; zero real writes."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from support.recovery_fakes import Crash, FakePublicClient

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.contracts import Environment
from wallex_agent.execution.sim_shadow import ShadowVenue
from wallex_agent.journal.store import JournalStore
from wallex_agent.recovery.coordinator import RecoveryBlocked, RecoveryOutcome
from wallex_agent.recovery.rebuild import ledger_digest
from wallex_agent.runtime.config import RunConfig, ShadowSettings
from wallex_agent.runtime.engine import RecoveryRequired, TradingRuntime
from wallex_agent.runtime.shadow import resume_shadow, run_shadow


def _cfg(make_cfg: Callable[..., RunConfig], **extra: Any) -> RunConfig:
    return make_cfg(**{"run.environment": Environment.SHADOW,
                       "shadow": ShadowSettings(poll_interval_s=60, history_bars=200).model_dump(), **extra})


def test_shadow_crash_then_resume_continues_the_same_run(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    cfg = _cfg(make_cfg)
    clock = {"t": datetime(2026, 10, 3, 6, 0, tzinfo=UTC)}
    client = FakePublicClient(lambda: clock["t"])
    seen: dict[str, Any] = {}
    ticks = {"n": 0}

    def crashing_sleep(s: float) -> None:
        ticks["n"] += 1
        if ticks["n"] == 300:
            seen["digest"] = ledger_digest(seen["a"].ledger)
            raise Crash("power loss")
        clock["t"] += timedelta(seconds=s)

    def keep(rt: TradingRuntime) -> None:
        seen["a"] = rt

    store = JournalStore(tmp_path / "s.sqlite")
    with pytest.raises(Crash):
        run_shadow(cfg, store, BaselineSelector(), run_id="sh", duration_s=12 * 3600, client=client,  # type: ignore[arg-type]
                   clock=lambda: clock["t"], sleep=crashing_sleep, on_runtime=keep)
    assert store.run_ended_at("sh") is None and seen["a"].venue.submitted, "fixture should have traded before the crash"

    store = JournalStore(tmp_path / "s.sqlite")  # new process
    with pytest.raises(RecoveryRequired):  # a fresh session never silently starts over an unfinished one
        run_shadow(cfg, store, BaselineSelector(), run_id="sh-new", duration_s=60, client=client,  # type: ignore[arg-type]
                   clock=lambda: clock["t"], sleep=lambda s: None)

    def resumed(rt: TradingRuntime) -> None:
        seen["after_recovery"] = ledger_digest(rt.ledger)

    def sleep(s: float) -> None:
        clock["t"] += timedelta(seconds=s)

    rt = resume_shadow(cfg, store, BaselineSelector(), run_id="sh", duration_s=2 * 3600, client=client,  # type: ignore[arg-type]
                       clock=lambda: clock["t"], sleep=sleep, on_runtime=resumed)
    assert rt.recovery is not None and rt.recovery.outcome is RecoveryOutcome.READY, rt.recovery
    assert seen["after_recovery"] == seen["digest"]
    assert store.run_ended_at("sh") is not None
    assert isinstance(rt.venue, ShadowVenue) and rt.venue.environment is Environment.SHADOW
    assert all(c.startswith("GET ") for c in client.calls)
    assert "RECOVERY_READY" in [e["kind"] for e in store.risk_events("sh")]
    assert len({d.decision_id for d in store.decisions("sh")}) == len(store.decisions("sh"))


def test_blocked_recovery_never_starts_the_loop(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    cfg = _cfg(make_cfg)
    clock = {"t": datetime(2026, 10, 3, 6, 0, tzinfo=UTC)}
    client = FakePublicClient(lambda: clock["t"])
    store = JournalStore(tmp_path / "s.sqlite")

    def crash(s: float) -> None:
        raise Crash("stop")

    with pytest.raises(Crash):
        run_shadow(cfg, store, BaselineSelector(), run_id="sh", duration_s=3600, client=client,  # type: ignore[arg-type]
                   clock=lambda: clock["t"], sleep=crash)
    before = len(store.decisions("sh"))
    with pytest.raises(RecoveryBlocked) as err:
        resume_shadow(_cfg(make_cfg, **{"limits.version": "changed"}), store, BaselineSelector(), run_id="sh",
                      duration_s=3600, client=client, clock=lambda: clock["t"], sleep=lambda s: None)  # type: ignore[arg-type]
    assert err.value.report.outcome is RecoveryOutcome.BLOCKED
    assert len(store.decisions("sh")) == before and store.run_ended_at("sh") is None
