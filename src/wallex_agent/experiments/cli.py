"""Argparse adapter; config-relative input paths and explicit held-out evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def add_parser(sub: Any) -> None:
    p = sub.add_parser("experiment", help="offline deterministic experiment harness")
    commands = p.add_subparsers(dest="experiment_command", required=True)
    run = commands.add_parser("run")
    run.add_argument("config", type=Path)
    run.add_argument("--output-root", type=Path, default=Path("experiments"))
    run.add_argument("--evaluate-test", action="store_true", help="run only TEST for one explicit parameter hash")
    run.add_argument("--parameter-hash")
    run.add_argument("--mlflow", action="store_true", help="optional local MLflow mirror (evaluation dependency group)")
    report = commands.add_parser("summarize")
    report.add_argument("experiment_id")
    report.add_argument("--output-root", type=Path, default=Path("experiments"))
    report.add_argument("--include-evaluation", type=Path, help="path to a compatible intentional TEST experiment")


def command(args: argparse.Namespace) -> int:
    from .report import summarize_experiment
    from .runner import run_experiment
    from .spec import load_spec
    from wallex_agent.market.dataset import load_dataset
    from wallex_agent.runtime.config import load_run_config

    try:
        if args.experiment_command == "run":
            config_path = Path(args.config).resolve()
            spec = load_spec(config_path)
            base = load_run_config(config_path.parent / spec.base_config)
            dataset = load_dataset(config_path.parent / spec.dataset_dir,
                                   expected_resolution_seconds=spec.dataset_resolution_seconds)
            out = run_experiment(spec, base, dataset, args.output_root, evaluate_test=args.evaluate_test,
                                 parameter_hash=args.parameter_hash, mlflow=args.mlflow)
            summary = summarize_experiment(out)
        else:
            import re
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.experiment_id):
                raise ValueError("invalid experiment id")
            out = args.output_root / args.experiment_id
            summary = summarize_experiment(out, evaluation=args.include_evaluation)
        print(json.dumps({"directory": str(out), "status": summary["status"], "trials": len(summary["trials"]),
                          "test_evaluated": summary["test_evaluated"]}))
        return 0 if summary["status"] == "PASS" else 1
    except (ValueError, OSError, RuntimeError) as exc:
        # Pydantic/IO exception payloads may echo input values. Never print arbitrary input or credentials.
        print(f"Experiment refused: {type(exc).__name__}. Check schema, offline base, bounds, hashes and unique output id.", file=sys.stderr)
        return 1
