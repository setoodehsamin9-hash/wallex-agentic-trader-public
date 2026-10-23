"""Bounded offline trials over the unchanged deterministic BACKTEST kernel.

The output directory is exclusively created, trial rows append once, and result hashes exclude wall-clock timestamps.
Future bars are removed before each invocation; only past bars can be used for feature warmup. No model/provider or
private venue factory is called. Parameter choice is always external to this module.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.contracts import CodeVersion, Contract
from wallex_agent.contracts.hashing import content_id, sha256_hex
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.dataset import Dataset
from wallex_agent.market.snapshot import bars_sha256
from wallex_agent.reporting.performance import PerformanceSummary
from wallex_agent.runtime.backtest import run_backtest
from wallex_agent.runtime.config import RunConfig
from wallex_agent.runtime.provenance import REPO, current_code_version, lockfile_sha256

from .spec import CostScenario, ExperimentSpec, ParameterConfig, TimeSplit, fingerprint, parameter_configs


class TrialResult(Contract):
    trial_id: str
    parameter_hash: str
    parameters: dict[str, Any]
    config_hash: str
    dataset_hash: str
    split_hash: str
    split: TimeSplit
    cost: CostScenario
    strategy_id: str
    strategy_version: str
    seed: int
    code: CodeVersion
    source_hash: str
    dependency_hash: str
    recorded_at: datetime
    status: Literal["PASS", "FAIL"]
    metrics: PerformanceSummary | None
    error_type: str | None
    result_hash: str


def write_json(path: Path, value: Any) -> None:
    with path.open("x") as f:
        json.dump(value, f, ensure_ascii=False, indent=1, default=str, allow_nan=False)
        f.write("\n")


def _dataset_identity(dataset: Dataset, symbols: tuple[str, ...]) -> str:
    for symbol in symbols:
        bars = dataset.bars.get(symbol)
        if not bars:
            raise ValueError(f"missing dataset symbol: {symbol}")
        previous: datetime | None = None
        for bar in bars:
            if (bar.ts_open.utcoffset() != timedelta(0) or bar.ts_open.second or bar.ts_open.microsecond
                    or (previous is not None and bar.ts_open <= previous)):
                raise ValueError("bars must be unique, increasing UTC minute opens")
            values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
            if (not all(v.is_finite() for v in values) or min(values[:4]) <= 0 or bar.volume < 0
                    or bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close)):
                raise ValueError("invalid OHLCV bar")
            previous = bar.ts_open
        info = dataset.manifest["symbols"][symbol]
        if bars_sha256(bars) != info["bars_sha256"] or sha256_hex((dataset.path / info["file"]).read_bytes()) != info["sha256"]:
            raise ValueError("dataset content differs from pinned manifest")
    metadata = {name: sha256_hex((dataset.path / "metadata" / name).read_bytes())
                for name in ("v1_markets.json", "hector_markets.json", "package_183.json")}
    return content_id({"manifest": dataset.manifest, "metadata": metadata})


def _split_metadata(split: TimeSplit, dataset: Dataset, symbols: tuple[str, ...], interval: int) -> dict[str, Any]:
    if any(int(t.timestamp()) % interval or t.microsecond for t in (split.start_utc, split.end_utc)):
        raise ValueError("split boundaries must align with strategy bars")
    counts: dict[str, Any] = {}
    prefix: dict[str, str] = {}
    base_s = int(dataset.manifest.get("resolution_seconds", 60))  # 60 for minute data (unchanged); 3600 for 1h histories
    for symbol in symbols:
        bars = dataset.bars[symbol]
        if split.start_utc < bars[0].ts_open or split.end_utc > bars[-1].ts_open + timedelta(seconds=base_s):
            raise ValueError("split outside common dataset coverage")
        count = sum(split.start_utc <= b.ts_open < split.end_utc for b in bars)
        if count == 0:
            raise ValueError("empty split")
        span_s = int((split.end_utc - split.start_utc).total_seconds())
        counts[symbol] = {"bars": count, "missing_minutes": (span_s - count * base_s) // 60}
        # Includes earlier warmup inputs but never future bars, independently of parameter-specific lookbacks.
        prefix[symbol] = bars_sha256([b for b in bars if b.ts_open < split.end_utc])
    payload = {"split": split.model_dump(mode="json"), "counts": counts, "past_bars_hashes": prefix}
    return {**payload, "split_hash": content_id(payload)}


def trial_config(variant: ParameterConfig, split: TimeSplit, cost: CostScenario, dataset_name: str) -> RunConfig:
    raw = variant.config.model_dump(mode="python")
    raw["costs"] = {"fee_rate": cost.fee_rate, "fee_source": cost.fee_source, "backtest_slippage_bps": cost.slippage_bps}
    # Explicit experiment periods replace legacy percentage splits / optional competition cutoffs in simulation only.
    raw["backtest"].update(dataset=dataset_name, split="ALL", start_utc=split.start_utc, end_utc=split.end_utc)
    raw["competition"] = {"start_utc": split.start_utc, "end_utc": split.end_utc}
    return RunConfig.model_validate(raw)


def trial_identity(config: RunConfig, dataset_hash: str, split_hash: str, source_hash: str, dependency_hash: str) -> dict[str, str]:
    return {"config_hash": fingerprint(config), "dataset_hash": dataset_hash, "split_hash": split_hash,
            "source_hash": source_hash, "dependency_hash": dependency_hash}


def run_experiment(spec: ExperimentSpec, base: RunConfig, dataset: Dataset, output_root: Path, *,
                   evaluate_test: bool = False, parameter_hash: str | None = None, mlflow: bool = False) -> Path:
    variants = parameter_configs(spec, base)
    splits = tuple(s for s in spec.splits if (s.role == "TEST") == evaluate_test)
    if evaluate_test:
        if parameter_hash is None or not splits:
            raise ValueError("TEST requires explicit parameter_hash and configured TEST period")
        variants = tuple(v for v in variants if v.parameter_hash == parameter_hash)
        if len(variants) != 1:
            raise ValueError("parameter_hash must identify one existing configuration")
    elif parameter_hash is not None:
        raise ValueError("parameter_hash is only used for intentional TEST evaluation")
    if len(variants) * len(splits) * len(spec.costs) > spec.max_trials:
        raise ValueError("grid × periods × costs exceeds max_trials")
    symbols = base.run.symbols
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate configured symbols")
    dataset_hash = _dataset_identity(dataset, symbols)
    split_meta = {s.name: _split_metadata(s, dataset, symbols, base.run.bar_interval_seconds) for s in spec.splits}
    code = current_code_version()
    source_hash = content_id({str(p.relative_to(REPO)): sha256_hex(p.read_bytes()) for p in sorted((REPO / "src").rglob("*.py"))})
    dependency_hash = lockfile_sha256()
    manifest: dict[str, Any] = {"schema_version": "1", "spec": spec.model_dump(mode="json"), "spec_hash": fingerprint(spec),
        "base_config": base.model_dump(mode="json"), "base_config_hash": fingerprint(base), "dataset_hash": dataset_hash,
        "dataset_name": dataset.name, "splits": split_meta, "code": code.model_dump(mode="json"),
        "source_hash": source_hash, "dependency_hash": dependency_hash, "evaluate_test": evaluate_test,
        "selected_parameter_hash": parameter_hash, "trial_count": len(variants) * len(splits) * len(spec.costs),
        "fill_assumptions": ["existing BacktestVenue; next-bar entry fills; worse stop when both barriers touch",
                             "past-only feature warmup; fresh capital/ledger per period; no carried positions",
                             "no historical depth, queue or latency model; gaps not filled; explicit assumed fee per side and slippage bps"],
        "selection_policy": "external human/Claude decision; no automatic best configuration"}
    manifest["manifest_hash"] = content_id(manifest)
    out = output_root / spec.experiment_id
    tracker = None
    if out.exists():
        raise FileExistsError(out)
    if mlflow:
        from .tracking import LocalMlflowTracker
        tracker = LocalMlflowTracker(output_root / ".mlflow", spec.experiment_id, manifest["manifest_hash"])
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "MANIFEST.json", manifest)
    seen: set[str] = set()
    with (out / "TRIALS.jsonl").open("x") as log:
        for variant in variants:
            for split in splits:
                # Hard boundary: even a mistakenly implemented downstream feature cannot read a future bar.
                view = Dataset(dataset.name, dataset.path, {s: [b for b in dataset.bars[s] if b.ts_open < split.end_utc]
                                                           for s in symbols}, dataset.refs, dataset.manifest)
                for cost in spec.costs:
                    cfg = trial_config(variant, split, cost, dataset.name)
                    identity = trial_identity(cfg, dataset_hash, split_meta[split.name]["split_hash"], source_hash, dependency_hash)
                    trial_id = content_id(identity)
                    if trial_id in seen:
                        raise ValueError("duplicate trial identity")
                    seen.add(trial_id)
                    journal = JournalStore(out / "journals" / f"{trial_id}.sqlite")
                    metrics, error_type = None, None
                    try:
                        result = run_backtest(cfg, view, journal, BaselineSelector(), run_id=trial_id, code=code,
                                              period=(split.start_utc, split.end_utc))
                        if (result.period != (split.start_utc, split.end_utc) or result.summary.unresolved_orders
                                or result.summary.execution_errors or result.summary.open_positions):
                            raise ValueError("incomplete backtest result")
                        metrics = result.summary
                    except Exception as exc:
                        error_type = type(exc).__name__  # Exception messages may contain arbitrary tool/credential payloads.
                    finally:
                        journal.close()
                    payload = {"trial_id": trial_id, "parameter_hash": variant.parameter_hash, "parameters": variant.parameters,
                        **identity, "split": split, "cost": cost, "strategy_id": spec.strategy_id,
                        "strategy_version": spec.strategy_version, "seed": spec.seed, "code": code,
                        "recorded_at": datetime.now(UTC), "status": "FAIL" if error_type else "PASS", "metrics": metrics,
                        "error_type": error_type, "result_hash": content_id({"identity": identity, "metrics": metrics.model_dump(mode="json")
                                                                               if metrics else None, "error_type": error_type})}
                    trial = TrialResult.model_validate(payload)
                    log.write(trial.model_dump_json() + "\n")
                    log.flush()
                    if tracker is not None:
                        try:
                            tracker.log_trial(trial, spec.experiment_id)
                        except Exception as exc:
                            with (out / "TRACKING_ERRORS.jsonl").open("a") as errors:
                                errors.write(json.dumps({"trial_id": trial_id, "error_type": type(exc).__name__}) + "\n")
    write_json(out / "COMPLETE.json", {"trials_sha256": sha256_hex((out / "TRIALS.jsonl").read_bytes()), "count": len(seen)})
    from .report import summarize_experiment

    summarize_experiment(out)
    return out
