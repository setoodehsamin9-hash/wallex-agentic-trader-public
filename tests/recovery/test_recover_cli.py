"""`wallex-agent recover RUN_ID`: read-only inspection an operator runs before resuming. It never writes the journal."""

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.contracts import CodeVersion, Environment
from wallex_agent.execution.sim_shadow import ShadowVenue
from wallex_agent.interface import cli
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import aggregate
from wallex_agent.market.book import OrderBook
from wallex_agent.market.dataset import Dataset
from wallex_agent.runtime.backtest import universe_from_metadata
from wallex_agent.runtime.config import RunConfig
from wallex_agent.runtime.engine import TradingRuntime

T = datetime(2026, 10, 3, 6, 15, tzinfo=UTC)


def _crashed_run(cfg: RunConfig, ds: Dataset, path: Path) -> TradingRuntime:
    metas, eligible, universe = universe_from_metadata(ds.path / "metadata")
    books: dict[str, OrderBook] = {}
    rt = TradingRuntime(cfg, store=JournalStore(path), venue=ShadowVenue(fee_rate=cfg.costs.fee_rate, book=books.get),
                        decider=BaselineSelector(), metas=metas, eligible=eligible, universe_version=universe, run_id="sh-1",
                        code=CodeVersion(git_commit="a" * 40, dirty=False), dataset_id=ds.name, data_source="FIXTURE")
    rt.start(T - timedelta(minutes=1), dependency_lock_sha256="a" * 64)
    price = [b for b in ds.bars["BTCTMN"] if b.ts_open + timedelta(minutes=1) <= T][-1].close
    books["BTCTMN"] = OrderBook(bids=((price * Decimal("0.9997"), Decimal(1)),), asks=((price * Decimal("1.0003"), Decimal(1)),),
                                received_at=T)
    rt.update_market("BTCTMN", mark=price, book=books["BTCTMN"])
    bars = [b for b in aggregate(ds.bars["BTCTMN"], 900) if b.ts_open + timedelta(seconds=900) <= T]
    rt.cycle("BTCTMN", T, bars[-rt.history_window:], flags=frozenset())
    return rt


def test_recover_inspects_without_writing(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    state = tmp_path / "var"
    rt = _crashed_run(make_cfg(**{"run.environment": Environment.SHADOW}), synthetic_dataset, state / "state.sqlite")
    assert rt.ledger.positions, "fixture should hold a position"
    db = sqlite3.connect(state / "state.sqlite")
    rows_before = db.execute("SELECT COUNT(*) FROM state_log").fetchone()[0] + db.execute("SELECT COUNT(*) FROM risk_events").fetchone()[0]
    monkeypatch.setattr(cli, "STATE", state)

    assert cli.main(["recover", "sh-1"]) == 0
    out = capsys.readouterr().out
    data = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert data["chain_ok"] and data["sealed"] is False
    assert list(data["positions"]) == ["BTCTMN"] and data["positions"]["BTCTMN"]["stop_price"]
    assert "shadow --resume sh-1" in out
    rows_after = db.execute("SELECT COUNT(*) FROM state_log").fetchone()[0] + db.execute("SELECT COUNT(*) FROM risk_events").fetchone()[0]
    assert rows_after == rows_before


def test_recover_reports_corruption(make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset, tmp_path: Path,
                                    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    state = tmp_path / "var"
    _crashed_run(make_cfg(**{"run.environment": Environment.SHADOW}), synthetic_dataset, state / "state.sqlite")
    db = sqlite3.connect(state / "state.sqlite")
    db.execute("DELETE FROM state_log WHERE seq = 1")
    db.commit()
    monkeypatch.setattr(cli, "STATE", state)
    assert cli.main(["recover", "sh-1"]) == 3
    assert "BLOCKED" in capsys.readouterr().out


def test_abandon_seals_an_unfinished_shadow_run_with_an_audited_reason(
        make_cfg: Callable[..., RunConfig], synthetic_dataset: Dataset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "var"
    _crashed_run(make_cfg(**{"run.environment": Environment.SHADOW}), synthetic_dataset, state / "state.sqlite")
    monkeypatch.setattr(cli, "STATE", state)
    assert cli.main(["abandon", "sh-1", "--reason", "code changed", "--operator", "owner"]) == 0
    store = JournalStore(state / "state.sqlite")
    assert store.run_ended_at("sh-1") is not None and store.unfinished_runs() == []
    ev = [e for e in store.risk_events("sh-1") if e["kind"] == "RUN_ABANDONED"]
    assert ev and ev[0]["operator"] == "owner" and ev[0]["reason"] == "code changed" and ev[0]["open_positions"] == ["BTCTMN"]


def test_recover_on_missing_db_does_not_create_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(cli, "STATE", tmp_path / "nothing")
    assert cli.main(["recover", "x"]) == 3
    assert not (tmp_path / "nothing").exists() and "BLOCKED" in capsys.readouterr().out
