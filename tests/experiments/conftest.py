from pathlib import Path
from typing import Any

import pytest
from support.synthetic import REPO, metadata_dir, synthetic_bars

from wallex_agent.market.dataset import Dataset, load_dataset, write_dataset
from wallex_agent.runtime.config import RunConfig, load_run_config


@pytest.fixture
def dataset(tmp_path: Path) -> Dataset:
    path = tmp_path / "data"
    write_dataset(path, "synthetic-harness-qa", {"BTCTMN": synthetic_bars(3, 1e8, 0.0), "ETHTMN": synthetic_bars(3, 5e6, 1.3)},
                  extra={"note": "synthetic fixture only; not market evidence"})
    metadata_dir(path / "metadata")
    return load_dataset(path)


@pytest.fixture
def base() -> RunConfig:
    return load_run_config(REPO / "config/backtest.toml")


@pytest.fixture
def spec_data() -> dict[str, Any]:
    return {"schema_version": "1", "experiment_id": "qa-grid", "base_config": "base.toml", "dataset_dir": "data",
            "strategy_id": "C1_breakout", "strategy_version": "c1-0.1.0", "seed": 7, "max_trials": 12,
            "parameters": {"lookback_bars": [16, 24]}, "base_cost_id": "base", "min_trades_observation": 5,
            "splits": [{"name": "train", "role": "TRAIN", "start_utc": "2026-10-01T00:00:00Z", "end_utc": "2026-10-02T00:00:00Z"},
                       {"name": "validation", "role": "VALIDATION", "start_utc": "2026-10-02T00:00:00Z", "end_utc": "2026-10-03T00:00:00Z"},
                       {"name": "test", "role": "TEST", "start_utc": "2026-10-03T00:00:00Z", "end_utc": "2026-10-04T00:00:00Z"}],
            "costs": [{"name": "base", "fee_rate": "0.0025", "slippage_bps": "5"},
                      {"name": "stress", "fee_rate": "0.005", "slippage_bps": "10"}]}
