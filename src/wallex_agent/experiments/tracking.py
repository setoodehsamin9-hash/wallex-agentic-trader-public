"""Optional manual MLflow mirror. Exact Decimal values stay in JSON; MLflow metrics are display floats.

Explicit local SQLite/artifact URIs ignore remote environment defaults. Nothing is imported by the trading runtime
or by default experiments. No autolog, model calls, provider tracing or registry is needed.
"""

import os
from importlib import import_module
from pathlib import Path
from typing import Any

from .runner import TrialResult


class LocalMlflowTracker:
    def __init__(self, directory: Path, experiment_id: str, manifest_hash: str) -> None:
        os.environ["MLFLOW_DISABLE_TELEMETRY"] = "true"
        try:
            mlflow: Any = import_module("mlflow")
        except ImportError as exc:
            raise RuntimeError("optional tracking requires uv sync --group evaluation") from exc
        directory = directory.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.client: Any = mlflow.MlflowClient(tracking_uri=f"sqlite:///{directory / 'tracking.sqlite'}")
        # Project + experiment + protocol hash avoids deployment-wide experiment-name collisions (Domino guidance).
        name = f"wallex-phase04-{experiment_id}-{manifest_hash[:16]}"
        found = self.client.get_experiment_by_name(name)
        self.experiment_id: str = (found.experiment_id if found is not None else self.client.create_experiment(
            name, artifact_location=(directory / "artifacts" / name).as_uri()))

    def log_trial(self, trial: TrialResult, experiment_id: str) -> str:
        run = self.client.create_run(self.experiment_id, tags={"mlflow.runName": trial.trial_id, "experiment_id": experiment_id,
                                                             "git_commit": trial.code.git_commit, "status": trial.status})
        run_id: str = run.info.run_id
        params = {"trial_id": trial.trial_id, "strategy_id": trial.strategy_id, "strategy_version": trial.strategy_version,
                  "parameter_hash": trial.parameter_hash, "config_hash": trial.config_hash, "dataset_hash": trial.dataset_hash,
                  "split_hash": trial.split_hash, "source_hash": trial.source_hash, "seed": str(trial.seed),
                  "split": trial.split.name, "split_role": trial.split.role, "cost.name": trial.cost.name,
                  "cost.fee_rate": str(trial.cost.fee_rate), "cost.slippage_bps": str(trial.cost.slippage_bps),
                  "timestamp": trial.recorded_at.isoformat(), **{f"parameter.{k}": str(v) for k, v in trial.parameters.items()}}
        try:
            for key, value in params.items():
                self.client.log_param(run_id, key, value)
            if trial.metrics is not None:
                for key, value in trial.metrics.model_dump(mode="python").items():
                    if isinstance(value, (int, float)) or hasattr(value, "as_tuple"):
                        self.client.log_metric(run_id, key, float(value))
            self.client.log_dict(run_id, trial.model_dump(mode="json"), "TRIAL.json")
        except Exception:
            self.client.set_terminated(run_id, status="FAILED")
            raise
        self.client.set_terminated(run_id, status="FINISHED" if trial.status == "PASS" else "FAILED")
        return run_id
