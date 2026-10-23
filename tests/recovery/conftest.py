from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from support.synthetic import REPO, metadata_dir, synthetic_bars

from wallex_agent.market.dataset import Dataset, load_dataset, write_dataset
from wallex_agent.runtime.config import RunConfig, load_run_config


@pytest.fixture
def synthetic_dataset(tmp_path: Path) -> Dataset:
    d = tmp_path / "ds"
    write_dataset(d, "synthetic-test", {"BTCTMN": synthetic_bars(3, 1e8, 0.0), "ETHTMN": synthetic_bars(3, 5e6, 1.3)},
                  extra={"note": "synthetic"})
    metadata_dir(d / "metadata")
    return load_dataset(d)


@pytest.fixture
def make_cfg() -> Callable[..., RunConfig]:
    def build(**overrides: Any) -> RunConfig:
        data = load_run_config(REPO / "config" / "backtest.toml").model_dump(mode="python")
        for dotted, value in overrides.items():
            node = data
            parts = dotted.split(".")
            for p in parts[:-1]:
                node = node[p]
            node[parts[-1]] = value
        data["backtest"]["dataset"] = "synthetic-test"
        return RunConfig.model_validate(data)

    return build
