"""Write the round-2 G10 Experiment Harness specs (kernel confirmation; VALIDATION slices only, no TEST split).

  uv run python scripts/phase04r2_specs.py   ->  config/experiments/phase04r2/p04r2-*.toml

Pre-registered in reports/phase-04/round2/PREREGISTRATION_R2.md section 8 (G10) and the addendum in section 11.
"""

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "config" / "experiments" / "phase04r2"
PACK = "../../../data/raw/historical-pack/export/datasets"
VAL_A = [("V1", "2025-03-01", "2025-08-01"), ("V2", "2025-08-01", "2026-01-01"), ("V3", "2026-01-01", "2026-06-01"),
         ("V4", "2026-06-01", "2026-07-24")]
VAL_B = [("V5", "2026-07-24", "2026-08-20"), ("V6", "2026-08-20", "2026-09-16"), ("V7", "2026-09-16", "2026-10-13")]
COSTS = [("base", "0.0025", "5"), ("adverse", "0.0035", "10"), ("low", "0.0020", "5")]


def spec(exp_id: str, note: str, base: str, dataset: str, resolution: int, strategy: str, version: str,
         slices: list[tuple[str, str, str]], test: tuple[str, str, str] | None = None) -> str:
    lines = [f"# {note}", 'schema_version = "1"', f'experiment_id = "{exp_id}"', f'base_config = "{base}"',
             f'dataset_dir = "{PACK}/{dataset}"', f"dataset_resolution_seconds = {resolution}",
             f'strategy_id = "{strategy}"', f'strategy_version = "{version}"', "seed = 7",
             f"max_trials = {(len(slices) + (test is not None)) * len(COSTS)}",
             'base_cost_id = "base"', "min_trades_observation = 5", ""]
    for name, a, b in slices:
        lines += ["[[splits]]", f'name = "{name.lower()}"', 'role = "VALIDATION"', f"start_utc = {a}T00:00:00Z",
                  f"end_utc = {b}T00:00:00Z", ""]
    if test is not None:
        lines += ["[[splits]]", f'name = "{test[0].lower()}"', 'role = "TEST"', f"start_utc = {test[1]}T00:00:00Z",
                  f"end_utc = {test[2]}T00:00:00Z", ""]
    for name, fee, slip in COSTS:
        lines += ["[[costs]]", f'name = "{name}"', f'fee_rate = "{fee}"', f'slippage_bps = "{slip}"', 'fee_source = "ASSUMED"', ""]
    return "\n".join(lines)


def main() -> None:
    files = {
        "p04r2-g10-f2.toml": spec("p04r2-g10-f2", "G10: frozen F2 (4h, EMA48, m5, regime-r2 on) in the kernel, 1h base, VAL-A+VAL-B",
                                  "base-4h-f2.toml", "1h", 3600, "F2_tsm", "f2-0.1.0", VAL_A + VAL_B),
        "p04r2-g10-f2-off.toml": spec("p04r2-g10-f2-off", "G10 regime comparison: F2 regime-off twin, kernel, 1h base, VAL",
                                      "base-4h-f2-off.toml", "1h", 3600, "F2_tsm", "f2-0.1.0", VAL_A + VAL_B),
        "p04r2-g10-f2-off-1m.toml": spec("p04r2-g10-f2-off-1m", "Fidelity: F2 regime-off twin, kernel on 1m bars, VAL-B",
                                         "base-4h-f2-off.toml", "1m", 60, "F2_tsm", "f2-0.1.0", VAL_B),
        "p04r2-test-f2.toml": spec("p04r2-test-f2", "TEST (single read, only after G1-G10): frozen F2 in the kernel, 1h base",
                                   "base-4h-f2.toml", "1h", 3600, "F2_tsm", "f2-0.1.0", VAL_A + VAL_B,
                                   test=("X", "2026-10-13", "2026-10-22")),
        "p04r2-cal-c2-1m.toml": spec("p04r2-cal-c2-1m", "Simulator calibration: C2 v0.2 baseline, kernel on 1m bars, VAL-B",
                                     "../phase04/base-1h-c2-v2.toml", "1m", 60, "C2_trend_hold", "c2-0.2.0", VAL_B),
    }
    for name, text in files.items():
        (OUT / name).write_text(text)
        print(OUT / name)


if __name__ == "__main__":
    main()
