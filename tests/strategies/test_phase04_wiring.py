"""The active strategy is what the runtime, backtest warm-up/GAP window and experiment harness actually use."""

from pathlib import Path
from typing import Any

import pytest
from support.synthetic import REPO, metadata_dir, synthetic_bars

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.experiments.spec import ExperimentSpec, parameter_configs
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.dataset import load_dataset, write_dataset
from wallex_agent.runtime.backtest import run_backtest
from wallex_agent.runtime.config import RunConfig, load_run_config

C2_SECTION = {"version": "c2-0.1.0", "trend_gate": True, "entry_trend_atr": "0.5", "stop_atr": "3", "max_holding_s": 43200,
              "candidate_ttl_s": 600, "min_atr_cost_ratio": "1.0"}


def _c2_config(**c2: Any) -> RunConfig:
    raw = load_run_config(REPO / "config/backtest.toml").model_dump(mode="python")
    raw["strategy"]["c2"] = {**C2_SECTION, **c2}
    raw["strategy"]["active"] = "C2_trend_hold"
    raw["run"]["bar_interval_seconds"] = 3600
    raw["features"]["trend_bars"] = 24
    raw["backtest"]["dataset"] = "synthetic-test"
    return RunConfig.model_validate(raw)


def test_backtest_trades_with_the_active_c2_strategy(tmp_path: Path) -> None:
    d = tmp_path / "ds"
    write_dataset(d, "synthetic-test", {"BTCTMN": synthetic_bars(3, 1e8, 0.0), "ETHTMN": synthetic_bars(3, 5e6, 1.3)},
                  extra={"note": "synthetic"})
    metadata_dir(d / "metadata")
    res = run_backtest(_c2_config(), load_dataset(d), JournalStore(tmp_path / "j.sqlite"), BaselineSelector(), run_id="c2")
    rt = res.runtime
    assert rt.strategy.strategy_id == "C2_trend_hold" and rt.strategy.version == "c2-0.1.0"
    assert rt.history_window >= 4 * 24
    cands = [c for d in rt.store.decisions("c2") for c in d.candidates]
    assert cands and {c.strategy_id for c in cands} == {"C2_trend_hold"}
    entries = [o for o in rt.execution.orders() if o.intent.purpose.value == "ENTRY"]
    assert entries and all(o.plan is not None and o.plan.take_profit_price is None for o in entries)


def test_harness_binds_c2_section_and_refuses_inactive_strategy() -> None:
    spec: dict[str, Any] = {"schema_version": "1", "experiment_id": "c2-grid", "base_config": "base.toml", "dataset_dir": "data",
                            "strategy_id": "C2_trend_hold", "strategy_version": "c2-0.1.0", "seed": 7, "max_trials": 8,
                            "parameters": {"stop_atr": ["2", "3"], "max_holding_s": [43200, 86400]}, "base_cost_id": "base",
                            "min_trades_observation": 5,
                            "splits": [{"name": "train", "role": "TRAIN", "start_utc": "2026-10-01T00:00:00Z",
                                        "end_utc": "2026-10-02T00:00:00Z"}],
                            "costs": [{"name": "base", "fee_rate": "0.0025", "slippage_bps": "5"}]}
    variants = parameter_configs(ExperimentSpec.model_validate(spec), _c2_config())
    assert len(variants) == 4 and all(v.config.strategy and v.config.strategy.c2 for v in variants)
    assert {str(v.parameters["stop_atr"]) for v in variants} == {"2", "3"}
    with pytest.raises(ValueError):  # a base config whose active strategy is C1 cannot run a C2 grid
        parameter_configs(ExperimentSpec.model_validate(spec), load_run_config(REPO / "config/backtest.toml"))
