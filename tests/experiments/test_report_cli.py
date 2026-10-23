import json
from pathlib import Path
from typing import Any

import pytest

from wallex_agent.experiments.report import analyze_trials, summarize_experiment
from wallex_agent.experiments.runner import run_experiment
from wallex_agent.experiments.spec import ExperimentSpec, parameter_configs
from wallex_agent.interface.cli import main
from wallex_agent.market.dataset import Dataset
from wallex_agent.runtime.config import RunConfig


def test_observations_are_metric_facts_without_automatic_selection() -> None:
    rows: list[dict[str, Any]] = [
        {"parameter_hash": "p", "split": {"name": "train", "role": "TRAIN"}, "cost": {"name": "base", "fee_rate": "0.001", "slippage_bps": "1"},
         "status": "PASS", "metrics": {"return_pct": "1", "trades": 2}},
        {"parameter_hash": "p", "split": {"name": "validation", "role": "VALIDATION"}, "cost": {"name": "base", "fee_rate": "0.001", "slippage_bps": "1"},
         "status": "PASS", "metrics": {"return_pct": "-2", "trades": 4}},
        {"parameter_hash": "p", "split": {"name": "train", "role": "TRAIN"}, "cost": {"name": "stress", "fee_rate": "0.0015", "slippage_bps": "1"},
         "status": "PASS", "metrics": {"return_pct": "-0.1", "trades": 2}},
        {"parameter_hash": "q", "split": {"name": "train", "role": "TRAIN"}, "cost": {"name": "base"}, "status": "FAIL", "metrics": None},
    ]
    result = analyze_trials(rows, base_cost_id="base", min_trades=3)
    codes = {o["code"] for o in result}
    assert codes == {"LOW_TRADE_COUNT", "RETURN_SIGN_CHANGE", "COST_ERASED_POSITIVE_RETURN", "FAILED_TRIAL"}
    assert any(o["parameter_hash"] == "q" and o["code"] == "FAILED_TRIAL" for o in result)


def test_reports_keep_matrix_and_detect_tampering(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset, tmp_path: Path) -> None:
    spec_data["parameters"] = {}
    spec = ExperimentSpec.model_validate(spec_data)
    out = run_experiment(spec, base, dataset, tmp_path / "train")
    summary = summarize_experiment(out)
    assert summary["complete"] and len(summary["trials"]) == 4
    assert summary["test_evaluated"] is False
    assert len(summary["configurations_for_claude_review"]) == 1
    assert {t["split"]["name"] for t in summary["trials"]} == {"train", "validation"}
    for filename in ("SUMMARY.csv", "SUMMARY.json", "REPORT_FA.md"):
        assert (out / filename).is_file()
    chosen = parameter_configs(spec, base)[0].parameter_hash
    test_out = run_experiment(spec, base, dataset, tmp_path / "test", evaluate_test=True, parameter_hash=chosen)
    combined = summarize_experiment(out, evaluation=test_out)
    assert combined["test_evaluated"] and len(combined["trials"]) == 6
    text = (out / "TRIALS.jsonl").read_text()
    (out / "TRIALS.jsonl").write_text(text.replace('"trades":', '"trades":9,"ignored":'))
    with pytest.raises(ValueError, match="integrity"):
        summarize_experiment(out)


def test_cli_uses_spec_relative_paths_and_cleanly_refuses_duplicates(spec_data: dict[str, Any], dataset: Dataset, tmp_path: Path,
        capsys: pytest.CaptureFixture[str]) -> None:
    from support.synthetic import REPO
    import shutil
    shutil.copyfile(REPO / "config/backtest.toml", tmp_path / "base.toml")
    spec_path = tmp_path / "experiment.toml"
    spec_path.write_text('''schema_version = "1"
experiment_id = "cli-qa"
base_config = "base.toml"
dataset_dir = "data"
strategy_id = "C1_breakout"
strategy_version = "c1-0.1.0"
seed = 7
max_trials = 1
base_cost_id = "base"
min_trades_observation = 5
[[splits]]
name = "train"
role = "TRAIN"
start_utc = 2026-10-01T00:00:00Z
end_utc = 2026-10-02T00:00:00Z
[[costs]]
name = "base"
fee_rate = "0.0025"
slippage_bps = "5"
''')
    output = tmp_path / "experiments"
    args = ["experiment", "run", str(spec_path), "--output-root", str(output)]
    assert main(args) == 0
    assert main(["experiment", "summarize", "cli-qa", "--output-root", str(output)]) == 0
    summary = json.loads((output / "cli-qa/SUMMARY.json").read_text())
    assert len(summary["trials"]) == 1
    assert main(args) == 1
    assert "FileExistsError" in capsys.readouterr().err


def test_partial_results_are_marked_incomplete(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset, tmp_path: Path) -> None:
    spec_data["parameters"] = {}
    out = run_experiment(ExperimentSpec.model_validate(spec_data), base, dataset, tmp_path)
    (out / "COMPLETE.json").unlink()
    # The read-only summarizer can describe an interrupted run; it must not overwrite the original completed report.
    with pytest.raises(ValueError, match="derived artifacts"):
        summarize_experiment(out)


@pytest.mark.parametrize("swap", ["dataset", "role"])
def test_trial_bundle_must_belong_to_its_manifest(spec_data: dict[str, Any], base: RunConfig, dataset: Dataset,
        tmp_path: Path, swap: str) -> None:
    import shutil
    from dataclasses import replace
    from wallex_agent.market.dataset import load_dataset, write_dataset
    spec_data["parameters"] = {}
    spec_data["costs"] = spec_data["costs"][:1]
    spec_data["splits"] = [spec_data["splits"][0], spec_data["splits"][2]]
    spec = ExperimentSpec.model_validate(spec_data)
    original = run_experiment(spec, base, dataset, tmp_path / "a")
    if swap == "dataset":
        changed = {s: [replace(b, volume=b.volume + 1) for b in bars] for s, bars in dataset.bars.items()}
        write_dataset(dataset.path, dataset.name, changed, extra={"note": "other pinned dataset"})
        other = run_experiment(spec, base, load_dataset(dataset.path), tmp_path / "b")
    else:
        chosen = parameter_configs(spec, base)[0].parameter_hash
        other = run_experiment(spec, base, dataset, tmp_path / "b", evaluate_test=True, parameter_hash=chosen)
    for filename in ("SUMMARY.json", "SUMMARY.csv", "REPORT_FA.md"):
        (original / filename).unlink()
    for filename in ("TRIALS.jsonl", "COMPLETE.json"):
        shutil.copyfile(other / filename, original / filename)
    with pytest.raises(ValueError, match="provenance"):
        summarize_experiment(original)
