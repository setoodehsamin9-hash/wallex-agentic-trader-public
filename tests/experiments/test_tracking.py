import importlib.util
import os
import socket
from pathlib import Path
from typing import Any

import pytest

from wallex_agent.experiments.tracking import LocalMlflowTracker
from wallex_agent.experiments.runner import run_experiment
from wallex_agent.experiments.spec import ExperimentSpec
from wallex_agent.market.dataset import Dataset
from wallex_agent.runtime.config import RunConfig


@pytest.mark.skipif(importlib.util.find_spec("mlflow") is None, reason="optional evaluation group")
def test_native_mlflow_mirror_is_local_even_if_environment_names_remote_server(spec_data: dict[str, Any], base: RunConfig,
        dataset: Dataset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://unreachable.invalid")
    os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"
    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    spec_data["parameters"] = {}
    spec_data["splits"] = spec_data["splits"][:1]
    spec_data["costs"] = spec_data["costs"][:1]
    out = run_experiment(ExperimentSpec.model_validate(spec_data), base, dataset, tmp_path, mlflow=True)
    assert not (out / "TRACKING_ERRORS.jsonl").exists()
    from importlib import import_module
    mlflow = import_module("mlflow")
    client = mlflow.MlflowClient(tracking_uri=f"sqlite:///{tmp_path / '.mlflow/tracking.sqlite'}")
    experiments = client.search_experiments()
    runs = [r for e in experiments for r in client.search_runs([e.experiment_id])]
    assert len(runs) == 1
    assert runs[0].data.params["strategy_id"] == "C1_breakout"
    assert runs[0].data.params["cost.fee_rate"] == "0.0025"
    assert "return_pct" in runs[0].data.metrics
    assert client.list_artifacts(runs[0].info.run_id)


def test_optional_tracking_failure_keeps_ordinary_results(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset,
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        pass
    def broken(self: Any, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("arbitrary data must not appear in artifacts")
    monkeypatch.setattr(LocalMlflowTracker, "__init__", init)
    monkeypatch.setattr(LocalMlflowTracker, "log_trial", broken)
    spec_data["parameters"] = {}
    spec_data["splits"] = spec_data["splits"][:1]
    spec_data["costs"] = spec_data["costs"][:1]
    out = run_experiment(ExperimentSpec.model_validate(spec_data), base, dataset, tmp_path, mlflow=True)
    assert (out / "TRIALS.jsonl").exists() and (out / "SUMMARY.json").exists()
    assert "RuntimeError" in (out / "TRACKING_ERRORS.jsonl").read_text()
    assert "arbitrary data" not in (out / "TRACKING_ERRORS.jsonl").read_text()
