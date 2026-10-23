import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, current_thread
from typing import Any

import pytest
from support.synthetic import EV1, synthetic_bars

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.agent.context import DecisionContext
from wallex_agent.contracts import AutonomyMode, CycleStatus, DecisionProvenance, Environment, ValidatedDecision
from wallex_agent.journal.store import JournalStore
from wallex_agent.runtime.config import RunConfig, ShadowSettings
from wallex_agent.runtime.shadow import run_shadow


class FakePublicClient:
    """Serves synthetic 1m candles and a book around the last close; records every URL. Has no write methods."""

    def __init__(self, now: Callable[[], datetime], publish_delay_s: int = 0) -> None:
        self.now = now
        self.delay = timedelta(seconds=publish_delay_s)
        self.calls: list[str] = []
        self.series = {"BTCTMN": synthetic_bars(4, 1e8, 0.0), "ETHTMN": synthetic_bars(4, 5e6, 1.3)}

    def _body(self, name: str) -> Any:
        raw = json.loads((EV1 / name).read_text())["body"]
        return json.loads(raw, parse_float=Decimal) if isinstance(raw, str) else raw

    def candles(self, symbol: str, resolution: str, frm: int, to: int) -> dict[str, Any]:
        self.calls.append(f"GET candles {symbol}")
        bars = [b for b in self.series[symbol]
                if frm <= b.ts_open.timestamp() <= to and b.ts_open + timedelta(seconds=60) + self.delay <= self.now()]
        return {"s": "ok", "t": [int(b.ts_open.timestamp()) for b in bars], "o": [str(b.open) for b in bars],
                "h": [str(b.high) for b in bars], "l": [str(b.low) for b in bars], "c": [str(b.close) for b in bars],
                "v": [str(b.volume) for b in bars]}

    def depth(self, symbol: str) -> dict[str, Any]:
        self.calls.append(f"GET depth {symbol}")
        last = [b for b in self.series[symbol] if b.ts_open <= self.now()][-1].close
        return {"result": {"ask": [{"price": last * Decimal("1.0003"), "quantity": Decimal(5)}],
                           "bid": [{"price": last * Decimal("0.9997"), "quantity": Decimal(5)}]}}

    def markets_v1(self) -> Any:
        return self._body("_v1_markets.json")

    def markets_hector(self) -> Any:
        return self._body("_hector_web_v1_markets.json")

    def package(self, package_id: int) -> Any:
        return self._body("_v1_prop_packages_183.json")


@pytest.mark.parametrize("mode", list(AutonomyMode))
def test_shadow_runs_full_path_with_zero_real_writes(make_cfg: Callable[..., RunConfig], tmp_path: Path,
                                                    mode: AutonomyMode) -> None:
    clock = {"t": datetime(2026, 10, 3, 6, 0, tzinfo=UTC)}
    client = FakePublicClient(lambda: clock["t"])
    cfg = make_cfg(**{"run.environment": Environment.SHADOW, "run.autonomy": mode,
                      "shadow": ShadowSettings(poll_interval_s=60, history_bars=200).model_dump()})
    store = JournalStore(tmp_path / "s.sqlite")

    def sleep(s: float) -> None:
        if mode is AutonomyMode.APPROVAL_REQUIRED:
            from wallex_agent.approval.service import ApprovalService
            from wallex_agent.contracts import ApprovalStatus

            for ticket in store.tickets(ApprovalStatus.PENDING, run_id="sh"):
                ApprovalService(store, ttl_s=300).decide(ticket.ticket_id, approve=True, operator="scripted-test", now=clock["t"])
        clock["t"] += timedelta(seconds=s)

    rt = run_shadow(cfg, store, BaselineSelector(), run_id="sh", duration_s=12 * 3600, client=client,  # type: ignore[arg-type]
                    clock=lambda: clock["t"], sleep=sleep)
    decisions = store.decisions("sh")
    assert len(decisions) >= 2 * 47  # one per symbol per 15m bar
    assert all(not c.startswith(("POST", "DELETE", "PUT")) for c in client.calls)
    assert rt.venue.environment is Environment.SHADOW
    if mode is AutonomyMode.ADVISOR:
        assert rt.execution.orders() == []
        assert any(d.status is CycleStatus.PROPOSED for d in decisions)
    elif mode is AutonomyMode.APPROVAL_REQUIRED:
        assert rt.counters.get("APPROVED_ENTRY_SUBMITTED", 0) > 0
    else:
        assert any(d.status in (CycleStatus.FILLED, CycleStatus.PARTIALLY_FILLED) for d in decisions)
    status = store.get_status("sh")
    assert status is not None and status["active"] is False and status["environment"] == "SHADOW"
    assert "return_pct" in status and "next_boundary" in status


def test_shadow_waits_for_late_published_bar_instead_of_marking_stale(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    """Wallex publishes the last 1m bar a few seconds after the boundary; the cycle must wait (settle), not go STALE."""
    clock = {"t": datetime(2026, 10, 3, 6, 0, 5, tzinfo=UTC)}
    client = FakePublicClient(lambda: clock["t"], publish_delay_s=30)
    cfg = make_cfg(**{"run.environment": Environment.SHADOW, "run.autonomy": AutonomyMode.AUTONOMOUS,
                      "shadow": ShadowSettings(poll_interval_s=15, history_bars=200, bar_settle_max_s=120).model_dump()})
    store = JournalStore(tmp_path / "late.sqlite")

    def sleep(s: float) -> None:
        clock["t"] += timedelta(seconds=s)

    run_shadow(cfg, store, BaselineSelector(), run_id="late", duration_s=2 * 3600, client=client,  # type: ignore[arg-type]
               clock=lambda: clock["t"], sleep=sleep)
    decisions = store.decisions("late")
    assert len(decisions) >= 2 * 7
    stale = [d for d in decisions if d.status is CycleStatus.DATA_UNUSABLE]
    assert stale == [], [d.block_reason for d in stale]


def test_shadow_maintains_public_feed_while_decider_is_waiting(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    clock = {"t": datetime(2026, 10, 3, 6, 14, 59, tzinfo=UTC)}
    waiting, serviced = Event(), Event()
    main = current_thread()

    class Client(FakePublicClient):
        def depth(self, symbol: str) -> dict[str, Any]:
            assert current_thread() is main
            if waiting.is_set():
                serviced.set()
            return super().depth(symbol)

    class Decider:
        source_name = "BASELINE"

        def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
            waiting.set()
            assert serviced.wait(3), "public feed and exits stalled while waiting for model"
            return BaselineSelector().decide(ctx)

    cfg = make_cfg(**{"run.environment": Environment.SHADOW, "run.symbols": ("BTCTMN",),
                      "shadow": ShadowSettings(poll_interval_s=1, history_bars=200).model_dump()})

    def sleep(seconds: float) -> None:
        clock["t"] += timedelta(seconds=seconds)

    rt = run_shadow(cfg, JournalStore(tmp_path / "wait.sqlite"), Decider(), run_id="responsive", duration_s=3,
                    client=Client(lambda: clock["t"]), clock=lambda: clock["t"], sleep=sleep)  # type: ignore[arg-type]
    assert serviced.is_set() and rt.last_decision is not None
    assert rt.last_decision.status is CycleStatus.FILLED
    status = rt.store.get_status("responsive")
    assert status is not None and "ninerouter_latency_ms" in status and "ai_errors" in status


def test_shadow_model_write_attempt_cannot_reach_live_adapter(make_cfg: Callable[..., RunConfig], tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel
    from wallex_agent.agent.decision_agent import LlmDecisionProvider
    from wallex_agent.runtime.config import AgentSettings

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("LIVE adapter reached in SHADOW")

    monkeypatch.setattr("wallex_agent.execution.wallex_live.WallexLiveVenue.__init__", forbidden)
    monkeypatch.setattr("wallex_agent.execution.wallex_live.UrllibPrivateTransport.__init__", forbidden)
    offered: list[str] = []

    def tempt(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        offered.extend(t.name for t in info.function_tools)
        return ModelResponse(parts=[ToolCallPart("wallex_spot_post_account_orders", {"symbol": "BTCTMN"})])

    store = JournalStore(tmp_path / "write-attempt.sqlite")
    provider = LlmDecisionProvider(FunctionModel(tempt), model_label="fake", settings=AgentSettings(output_retries=0),
                                   store=store, stream=False, environment=Environment.SHADOW)
    cfg = make_cfg(**{"run.environment": Environment.SHADOW, "run.symbols": ("BTCTMN",),
                      "shadow": ShadowSettings(poll_interval_s=15, history_bars=200).model_dump()})
    clock = {"t": datetime(2026, 10, 3, 6, 14, 59, tzinfo=UTC)}

    def advance(seconds: float) -> None:
        clock["t"] += timedelta(seconds=seconds)

    rt = run_shadow(cfg, store, provider, run_id="write-attempt", duration_s=60,
                    client=FakePublicClient(lambda: clock["t"]), clock=lambda: clock["t"], sleep=advance)  # type: ignore[arg-type]
    rows = store.decisions("write-attempt")
    assert offered and "wallex_spot_post_account_orders" not in offered
    assert any(d.provenance and any(t.tool_name == "wallex_spot_post_account_orders" and t.is_error
                                   for t in d.provenance.tool_calls) for d in rows)
    assert rt.execution.orders() == [] and all(d.effective_action.value == "WAIT" for d in rows)


def test_unfinished_shadow_session_refuses_new_session_before_io(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    clock = lambda: datetime(2026, 10, 3, 6, 0, tzinfo=UTC)

    class CrashingClient(FakePublicClient):
        def depth(self, symbol: str) -> dict[str, Any]:
            raise RuntimeError("simulated process interruption")

    cfg = make_cfg(**{"run.environment": Environment.SHADOW, "shadow": ShadowSettings(history_bars=200).model_dump()})
    client = CrashingClient(clock)
    store = JournalStore(tmp_path / "crashed.sqlite")
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        run_shadow(cfg, store, BaselineSelector(), run_id="crashed", duration_s=1, client=client, clock=clock)  # type: ignore[arg-type]
    calls_before = list(client.calls)
    with pytest.raises(RuntimeError, match="recovery"):
        run_shadow(cfg, store, BaselineSelector(), run_id="different-id", duration_s=1, client=client, clock=clock)  # type: ignore[arg-type]
    assert client.calls == calls_before
    assert [m.run_id for m in store.unfinished_runs()] == ["crashed"]


@pytest.mark.parametrize("environment", [Environment.BACKTEST, Environment.LIVE])
def test_shadow_rejects_other_environments_before_any_io(make_cfg: Callable[..., RunConfig], tmp_path: Path,
                                                       environment: Environment, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_cfg(**{"run.environment": environment, "shadow": ShadowSettings().model_dump()})
    client = FakePublicClient(lambda: datetime(2026, 10, 3, 6, 0, tzinfo=UTC))
    def forbidden_read() -> dict[str, Any]:
        raise AssertionError("environment guard ran after market I/O")
    monkeypatch.setattr(client, "markets_v1", forbidden_read)
    with pytest.raises(ValueError, match="SHADOW"):
        run_shadow(cfg, JournalStore(tmp_path / "wrong.sqlite"), BaselineSelector(), run_id="wrong", duration_s=0,
                   client=client)  # type: ignore[arg-type]
    assert client.calls == []
