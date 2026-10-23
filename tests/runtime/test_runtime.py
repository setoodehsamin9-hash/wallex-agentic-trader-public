from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
import socket

import pytest

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.agent.context import DecisionContext
from wallex_agent.agent.replay import ReplayDecisionProvider
from wallex_agent.contracts import (
    AutonomyMode,
    CycleStatus,
    DecisionProvenance,
    ValidatedDecision,
)
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.dataset import Dataset
from wallex_agent.runtime.backtest import run_backtest
from wallex_agent.runtime.config import RunConfig, TradingWindowConfig, WindowSpec


class SpyDecider:
    source_name = "BASELINE"

    def __init__(self) -> None:
        self.calls = 0
        self.inner = BaselineSelector()

    def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
        self.calls += 1
        assert ctx.candidates, "decider must never be called without candidates"
        return self.inner.decide(ctx)


def _run(cfg: RunConfig, ds: Dataset, tmp: Path, decider: object | None = None, name: str = "r"):  # type: ignore[no-untyped-def]
    store = JournalStore(tmp / f"{name}.sqlite")
    return run_backtest(cfg, ds, store, decider or SpyDecider(), run_id=name), store  # type: ignore[arg-type]


def test_autonomous_backtest_trades_and_finishes_tmn_flat(synthetic_dataset: Dataset, make_cfg: Callable[..., RunConfig],
                                                          tmp_path: Path) -> None:
    res, store = _run(make_cfg(), synthetic_dataset, tmp_path)
    s = res.summary
    assert s.trades >= 1 and s.fees_tmn > 0
    assert res.runtime.ledger.positions == {}  # liquidated before the end -> realized TMN
    assert s.final_equity_tmn == s.cash_tmn
    assert s.realized_pnl_tmn == s.net_pnl_tmn
    cycles = len(store.decisions("r"))
    expected = 2 * int((res.period[1] - res.period[0]).total_seconds() // 900)
    assert cycles == expected  # every cycle journaled
    assert any(t["exit_reason"] in ("EXIT_STOP", "EXIT_TAKE_PROFIT", "EXIT_TIME", "LIQUIDATION") for t in store.trades("r"))


def test_decider_only_called_when_candidates_exist(synthetic_dataset: Dataset, make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    spy = SpyDecider()
    res, store = _run(make_cfg(), synthetic_dataset, tmp_path, spy)
    with_cands = [d for d in store.decisions("r") if d.candidates and d.status is not CycleStatus.BLOCKED]
    assert spy.calls == len(with_cands) > 0
    assert any(d.status is CycleStatus.NO_CANDIDATES for d in store.decisions("r"))
    assert res.summary.decisions_total == len(store.decisions("r"))


def test_advisor_never_submits(synthetic_dataset: Dataset, make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    res, store = _run(make_cfg(**{"run.autonomy": AutonomyMode.ADVISOR}), synthetic_dataset, tmp_path)
    assert res.runtime.venue.submitted == []  # type: ignore[attr-defined]
    assert any(d.status is CycleStatus.PROPOSED for d in store.decisions("r"))
    assert res.summary.trades == 0 and res.runtime.ledger.reservations == {}


def test_approval_reject_all_never_submits_and_releases(synthetic_dataset: Dataset, make_cfg: Callable[..., RunConfig],
                                                         tmp_path: Path) -> None:
    cfg = make_cfg(**{"run.autonomy": AutonomyMode.APPROVAL_REQUIRED, "backtest.approval_policy": "REJECT_ALL"})
    res, store = _run(cfg, synthetic_dataset, tmp_path)
    assert res.runtime.venue.submitted == []  # type: ignore[attr-defined]
    assert any(d.status is CycleStatus.AWAITING_APPROVAL for d in store.decisions("r"))
    assert res.runtime.ledger.reservations == {}


def test_approval_approve_all_executes_after_recheck(synthetic_dataset: Dataset, make_cfg: Callable[..., RunConfig],
                                                      tmp_path: Path) -> None:
    cfg = make_cfg(**{"run.autonomy": AutonomyMode.APPROVAL_REQUIRED, "backtest.approval_policy": "APPROVE_ALL"})
    res, _ = _run(cfg, synthetic_dataset, tmp_path)
    assert res.summary.trades >= 1 and res.runtime.counters.get("APPROVED_ENTRY_SUBMITTED", 0) >= 1


def test_trading_window_closure_blocks_entries_without_ai_call(synthetic_dataset: Dataset, make_cfg: Callable[..., RunConfig],
                                                               tmp_path: Path) -> None:
    tw = TradingWindowConfig(version="tw-test", timezone="UTC", no_new_entry_before_close_s=1800, flatten_before_close_s=900,
                             windows=(WindowSpec(days=("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"), start="08:00", end="20:00"),))
    spy = SpyDecider()
    res, store = _run(make_cfg(trading_window=tw.model_dump()), synthetic_dataset, tmp_path, spy)
    blocked = [d for d in store.decisions("r") if d.status is CycleStatus.BLOCKED]
    assert blocked and all(d.block_reason for d in blocked)
    night = [d for d in blocked if d.block_reason == "OUTSIDE_TRADING_WINDOW"]
    assert night and all(d.next_entry_at is not None and d.next_entry_at.hour == 8 for d in night)
    for t in store.trades("r"):  # flattened before close: nothing held overnight
        assert t["closed_at"][11:16] <= "20:00" or t["closed_at"][11:13] >= "08"
    assert res.runtime.ledger.positions == {}


def test_kill_switch_blocks_entries(synthetic_dataset: Dataset, make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "k.sqlite")
    store.kill_switch().latch("drill", at=synthetic_dataset.bars["BTCTMN"][0].ts_open)
    res = run_backtest(make_cfg(), synthetic_dataset, store, SpyDecider(), run_id="k")
    assert res.runtime.venue.submitted == []  # type: ignore[attr-defined]
    assert any(d.status is CycleStatus.BLOCKED or d.status is CycleStatus.RISK_REJECTED for d in store.decisions("k"))


def test_backtest_is_deterministic(synthetic_dataset: Dataset, make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    a, sa = _run(make_cfg(), synthetic_dataset, tmp_path, name="a")
    b, sb = _run(make_cfg(), synthetic_dataset, tmp_path, name="b")
    assert a.summary.realized_pnl_tmn == b.summary.realized_pnl_tmn and a.summary.trades == b.summary.trades
    assert [d.status for d in sa.decisions("a")] == [d.status for d in sb.decisions("b")]
    assert [t["net_pnl_tmn"] for t in sa.trades("a")] == [t["net_pnl_tmn"] for t in sb.trades("b")]


def test_baseline_backtest_replays_recorded_decisions_without_network(synthetic_dataset: Dataset,
                                                                     make_cfg: Callable[..., RunConfig], tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_cfg()
    store = JournalStore(tmp_path / "replay.sqlite")
    original = run_backtest(cfg, synthetic_dataset, store, BaselineSelector(), run_id="source")
    assert original.summary.trades > 0

    def no_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("replay attempted network access")

    monkeypatch.setattr(socket, "socket", no_network)
    provider = ReplayDecisionProvider(store, "source", exit_enabled=False)
    replayed = run_backtest(cfg, synthetic_dataset, store, provider, run_id="replay")
    assert replayed.summary.realized_pnl_tmn == original.summary.realized_pnl_tmn
    assert replayed.summary.trades == original.summary.trades
    assert provider.missing == 0
    assert [d.status for d in store.decisions("source")] == [d.status for d in store.decisions("replay")]
    assert store.get_ref("source", "run_config") == cfg.model_dump(mode="json")


def test_cutoff_liquidates_open_positions(synthetic_dataset: Dataset, make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    cfg = make_cfg(**{"strategy.c1.max_holding_s": 10 * 86400, "strategy.c1.tp_atr": "50", "strategy.c1.stop_atr": "40",
                      "limits.risk.per_trade_budget_fraction": "0.05"})
    res, store = _run(cfg, synthetic_dataset, tmp_path)
    assert res.runtime.ledger.positions == {}
    assert any(t["exit_reason"] == "LIQUIDATION" for t in store.trades("r"))
    last = max(t["closed_at"] for t in store.trades("r"))
    assert last <= (res.period[1] - timedelta(minutes=0)).isoformat()
