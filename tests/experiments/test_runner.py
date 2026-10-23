import json
import socket
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from wallex_agent.experiments.runner import run_experiment
from wallex_agent.experiments.spec import ExperimentSpec, parameter_configs
from wallex_agent.market.dataset import Dataset, load_dataset, write_dataset
from wallex_agent.runtime.config import RunConfig


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (path / "TRIALS.jsonl").read_text().splitlines()]


def test_kernel_trials_reproduce_and_stress_costs_without_network_or_mutation(spec_data: dict[str, Any], base: RunConfig,
        dataset: Dataset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("experiment attempted network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    before = base.model_dump_json()
    spec = ExperimentSpec.model_validate(spec_data)
    a = run_experiment(spec, base, dataset, tmp_path / "a")
    b = run_experiment(spec, base, dataset, tmp_path / "b")
    ra, rb = rows(a), rows(b)
    assert len(ra) == 8 and all(r["status"] == "PASS" for r in ra)
    assert all(r["split"]["role"] != "TEST" for r in ra)
    assert [r["result_hash"] for r in ra] == [r["result_hash"] for r in rb]
    assert [r["trial_id"] for r in ra] == [r["trial_id"] for r in rb]
    assert base.model_dump_json() == before
    assert sum(r["metrics"]["trades"] for r in ra) > 0
    assert any(Decimal(x["metrics"]["fees_tmn"]) != Decimal(y["metrics"]["fees_tmn"]) for x, y in zip(ra[::2], ra[1::2]))
    assert any(x["metrics"]["realized_pnl_tmn"] != y["metrics"]["realized_pnl_tmn"] for x, y in zip(ra[::2], ra[1::2]))
    assert all(r["config_hash"] and r["dataset_hash"] and r["split_hash"] and r["parameters"] for r in ra)
    with pytest.raises(FileExistsError):
        run_experiment(spec, base, dataset, tmp_path / "a")


def test_test_split_requires_intent_and_single_parameter_hash(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset, tmp_path: Path) -> None:
    spec = ExperimentSpec.model_validate(spec_data)
    with pytest.raises(ValueError, match="parameter"):
        run_experiment(spec, base, dataset, tmp_path, evaluate_test=True)
    chosen = parameter_configs(spec, base)[0].parameter_hash
    out = run_experiment(spec, base, dataset, tmp_path, evaluate_test=True, parameter_hash=chosen)
    assert len(rows(out)) == 2
    assert {r["split"]["role"] for r in rows(out)} == {"TEST"}
    assert {r["parameter_hash"] for r in rows(out)} == {chosen}


@pytest.mark.parametrize("change", ["limit", "live", "llm", "symbol", "alignment", "coverage", "unknown-strategy"])
def test_invalid_run_is_refused_before_artifacts(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset, tmp_path: Path, change: str) -> None:
    data = base.model_dump(mode="python")
    if change == "limit":
        spec_data["max_trials"] = 1
    elif change == "live":
        data["run"]["environment"] = "LIVE"
    elif change == "llm":
        data["agent"]["decision_provider"] = "LLM"
    elif change == "symbol":
        data["run"]["symbols"] = ["MISSINGTMN"]
    elif change == "alignment":
        spec_data["splits"][0]["start_utc"] = "2026-10-01T00:01:00Z"
    elif change == "coverage":
        spec_data["splits"][0]["start_utc"] = "2026-09-30T00:00:00Z"
    else:
        spec_data["strategy_id"] = "invented"
    with pytest.raises(ValueError):
        run_experiment(ExperimentSpec.model_validate(spec_data), RunConfig.model_validate(data), dataset, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_future_bars_do_not_affect_training_features_or_results(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset, tmp_path: Path) -> None:
    spec_data["parameters"] = {}
    spec_data["splits"] = spec_data["splits"][:1]
    spec_data["costs"] = spec_data["costs"][:1]
    spec = ExperimentSpec.model_validate(spec_data)
    a = rows(run_experiment(spec, base, dataset, tmp_path / "before"))[0]
    cutoff = spec.splits[0].end_utc
    altered = {sym: [replace(b, open=b.open * 2, high=b.high * 2, low=b.low * 2, close=b.close * 2)
                      if b.ts_open >= cutoff else b for b in bars] for sym, bars in dataset.bars.items()}
    write_dataset(dataset.path, dataset.name, altered, extra={"note": "future perturbation"})
    b = rows(run_experiment(spec, base, load_dataset(dataset.path), tmp_path / "after"))[0]
    assert a["metrics"]["realized_pnl_tmn"] == b["metrics"]["realized_pnl_tmn"]
    assert a["metrics"]["trades"] == b["metrics"]["trades"]
    assert a["split_hash"] == b["split_hash"]
    assert a["dataset_hash"] != b["dataset_hash"]


def test_failed_backtest_is_recorded_without_fake_metrics(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset,
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import wallex_agent.experiments.runner as runner
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("must not serialize arbitrary exception payload")
    monkeypatch.setattr(runner, "run_backtest", broken)
    spec_data["parameters"] = {}
    out = run_experiment(ExperimentSpec.model_validate(spec_data), base, dataset, tmp_path)
    assert len(rows(out)) == 4
    assert all(r["status"] == "FAIL" and r["metrics"] is None and r["error_type"] == "RuntimeError" for r in rows(out))
    assert "must not serialize" not in (out / "TRIALS.jsonl").read_text()


def test_fee_and_slippage_are_independent_kernel_inputs(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset, tmp_path: Path) -> None:
    spec_data["parameters"] = {}
    spec_data["splits"] = spec_data["splits"][:1]
    spec_data["costs"] = [{"name": "base", "fee_rate": "0.0025", "slippage_bps": "5"},
                          {"name": "fee", "fee_rate": "0.003", "slippage_bps": "5"},
                          {"name": "slip", "fee_rate": "0.0025", "slippage_bps": "10"}]
    result = rows(run_experiment(ExperimentSpec.model_validate(spec_data), base, dataset, tmp_path))
    assert all(r["status"] == "PASS" and r["metrics"]["trades"] > 0 for r in result)
    assert len({r["metrics"]["realized_pnl_tmn"] for r in result}) == 3


@pytest.mark.parametrize("change", ["duplicate", "reverse", "invalid-bar", "unpinned"])
def test_bad_datasets_fail_closed(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset, tmp_path: Path, change: str) -> None:
    bars = dataset.bars["BTCTMN"]
    if change == "duplicate":
        bars.insert(1, bars[0])
    elif change == "reverse":
        bars.reverse()
    elif change == "invalid-bar":
        bars[0] = replace(bars[0], low=Decimal(-1))
    else:
        bars[0] = replace(bars[0], volume=Decimal(123))
    with pytest.raises(ValueError):
        run_experiment(ExperimentSpec.model_validate(spec_data), base, dataset, tmp_path / "out")


def test_metadata_changes_dataset_identity(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset, tmp_path: Path) -> None:
    spec_data["parameters"] = {}
    spec_data["splits"] = spec_data["splits"][:1]
    spec_data["costs"] = spec_data["costs"][:1]
    spec = ExperimentSpec.model_validate(spec_data)
    a = rows(run_experiment(spec, base, dataset, tmp_path / "a"))[0]
    metadata = dataset.path / "metadata/v1_markets.json"
    metadata.write_text(metadata.read_text() + "\n")
    b = rows(run_experiment(spec, base, dataset, tmp_path / "b"))[0]
    assert a["dataset_hash"] != b["dataset_hash"]


def test_journal_configuration_identifies_actual_dataset_and_period(spec_data: dict[str, Any], base: RunConfig,
        dataset: Dataset, tmp_path: Path) -> None:
    from wallex_agent.journal.store import JournalStore
    spec_data["parameters"] = {}
    spec_data["costs"] = spec_data["costs"][:1]
    spec_data["splits"] = spec_data["splits"][:1]
    spec = ExperimentSpec.model_validate(spec_data)
    out = run_experiment(spec, base, dataset, tmp_path)
    trial = rows(out)[0]
    journal = JournalStore(out / "journals" / f"{trial['trial_id']}.sqlite")
    try:
        config = journal.get_ref(trial["trial_id"], "run_config")
        assert config is not None
        effective = RunConfig.model_validate(config)
        assert effective.backtest is not None
        assert effective.backtest.dataset == dataset.name
        assert effective.backtest.start_utc == spec.splits[0].start_utc
        assert effective.backtest.end_utc == spec.splits[0].end_utc
    finally:
        journal.close()


def test_interior_gaps_remain_unfilled_and_are_counted(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset, tmp_path: Path) -> None:
    spec_data["parameters"] = {}
    spec_data["splits"] = spec_data["splits"][:1]
    spec_data["costs"] = spec_data["costs"][:1]
    del dataset.bars["BTCTMN"][100]
    write_dataset(dataset.path, dataset.name, dataset.bars, extra={"note": "one unfilled minute"})
    out = run_experiment(ExperimentSpec.model_validate(spec_data), base, load_dataset(dataset.path), tmp_path)
    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert manifest["splits"]["train"]["counts"]["BTCTMN"] == {"bars": 1439, "missing_minutes": 1}
    assert manifest["splits"]["train"]["counts"]["ETHTMN"] == {"bars": 1440, "missing_minutes": 0}
