from collections.abc import Callable
from pathlib import Path

import pytest

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.interface import cli
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.dataset import Dataset
from wallex_agent.runtime.backtest import run_backtest
from wallex_agent.runtime.config import RunConfig


def test_replay_cli_uses_source_config_and_supports_repeated_replays(make_cfg: Callable[..., RunConfig],
                                                                  synthetic_dataset: Dataset, tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    cfg = make_cfg(**{"backtest.split": "VALIDATION"})
    state = tmp_path / "state"
    store = JournalStore(state / "backtest.sqlite")
    run_backtest(cfg, synthetic_dataset, store, BaselineSelector(), run_id="source")
    store.close()
    folder = tmp_path / "data" / "datasets"
    folder.mkdir(parents=True)
    (folder / synthetic_dataset.name).symlink_to(synthetic_dataset.path, target_is_directory=True)
    monkeypatch.setattr(cli, "STATE", state)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["replay", "source"]) == 0
    assert cli.main(["replay", "source"]) == 0
    assert '"identical_trades": true' in capsys.readouterr().out
