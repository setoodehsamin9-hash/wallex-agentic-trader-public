# Experiment harness → Claude

Scope: infrastructure preparation, **not Phase 04 strategy acceptance or LIVE readiness**.
Base `671f661`; branch **`phase-04/experiment-harness-prep`**. Integrate only after Claude's Phase 03 integration.
No merge of recovery/main was performed here. Integration unit is the single commit containing this handoff:
`git log -1 --format='%H %s' origin/phase-04/experiment-harness-prep` (final chat response also gives its exact SHA).

## Define / run

Copy `config/experiments/synthetic-smoke.toml`; give each independent experiment a new id. Input paths are relative
to that TOML; output root is relative to the shell. Use an existing BACKTEST config with BASELINE, original limits,
features, symbols and strategy version. Every variant is revalidated; files/config objects are never edited.

```toml
# Under the example's top-level fields; illustrative search axes only, not a proposed strategy.
[parameters]
lookback_bars = [20, 40]
stop_atr = ["1.5", "2.5"]
```

Only existing strategy parameter fields are grid axes; `version` and unknown fields are rejected. Monetary/decimal
values must be quoted strings; integer fields use integers. No random search; keys sorted, axis value order retained.
`max_trials` bounds parameters × executed periods × costs BEFORE execution. Semantically duplicate combinations
(e.g. `"1.5"` vs `"1.50"`) and duplicate cost scenarios are errors, not silent skips. Seed is explicit.
Current runtime supports only `C1_breakout` → `strategy.c1`; the wrapper contains no entry/exit business rules.
Change feature/regime/symbol inputs in a separately versioned BACKTEST config when YOU decide to test them.

Use named `[[splits]]` with role TRAIN / VALIDATION / TEST and explicit UTC `start_utc` / exclusive `end_utc`.
They must be chronological, disjoint, within all configured symbols' coverage, aligned to strategy bars. Multiple
named non-overlapping periods per role are allowed; no automatic walk-forward/overlapping-fold generator.
Future bars are removed before kernel invocation; the unchanged driver uses past-only warmup. Each period resets
capital/ledger and flattens independently. Full dataset hashing is integrity work, not TEST evaluation. TEST is not
run during a development grid; the already-observed Phase 03 historical TEST is not a new holdout.

Each `[[costs]]` supplies name, per-side fee fraction, slippage in basis points and ASSUMED/ACCOUNT label. The label
is owner-supplied provenance, not a credential/account lookup. Define fee-only, slippage-only and combined stress as
needed. Base-cost id anchors observations. No fee/risk threshold or final split ratio is chosen by this harness.

```bash
uv run wallex-agent experiment run path/to/experiment.toml
uv run wallex-agent experiment summarize EXPERIMENT_ID
# Intentionally evaluate ONE configuration; use a separate root to preserve development evidence:
uv run wallex-agent experiment run path/to/experiment.toml --evaluate-test --parameter-hash FULL_HASH --output-root experiments-test
# Join compatible immutable development/evaluation runs into a separate comparisons/<protocol-hash>/ report:
uv run wallex-agent experiment summarize EXPERIMENT_ID --include-evaluation experiments-test/EXPERIMENT_ID
# Optional local native MLflow mirror; never inherits remote MLFLOW_TRACKING_URI:
uv sync --frozen --group evaluation
uv run wallex-agent experiment run path/to/experiment.toml --mlflow
```

Artifacts: `experiments/<id>/MANIFEST.json`, append-once `TRIALS.jsonl`, `COMPLETE.json`, `SUMMARY.json`, `SUMMARY.csv`,
`REPORT_FA.md`, and `journals/<trial_id>.sqlite`. Re-running the same id/root refuses overwrite. Keep journals for
replay. Source/dependency/config/dataset (metadata included)/split/result hashes accompany every trial. Timestamps
are excluded from reproducible result hashes. Split manifest includes bar counts, missing minutes and past-input
hashes. Missing/invalid result ⇒ FAIL + null metrics + safe exception type; failed trials remain in the matrix.
Interrupted runs retain partial rows; no resume is implemented. A fresh derived report can label them PARTIAL.
Completed runs are checked against trial-file and result hashes before summarization. Hashes detect corruption,
not malicious rewriting of every artifact; retain source control/backups for provenance.

Metrics use the one `reporting.performance.compute_summary` path: capital, ending equity, realized/net P&L/return,
drawdown, closed trade/win/loss/breakeven counts, win rate, mean trade P&L, fees, gross positive/negative CLOSED
trade **net after-fee P&L**, profit factor, fee burden (fees / initial capital ×100), mean holding seconds and union
of holding intervals / period duration. Monetary JSON preserves Decimal; percent metrics keep existing 4dp display
precision. Profit factor is null when gross loss is zero, including all wins. Empty-trade win rate/mean/holding are
null. CSV nulls are empty. Ending exposure also remains in the canonical summary.

Cross-period observations: return sign changes, fewer than the configured observation trade count, positive base
return lost under higher costs, failed trials. Cost changes may alter candidates/sizing as in the real kernel; these
are full reruns, not post-hoc fee subtraction or identical-fill comparisons. ALL configurations are listed for
Claude review by hash, with no automatic best/KEEP/MODIFY/REJECT/LIVE choice.

Optional MLflow stays in the existing evaluation group and imports only for `--mlflow`. It uses local
`<output-root>/.mlflow/tracking.sqlite` + file artifacts, manual params/metrics/exact JSON, telemetry off. Native
MLflow display metrics use floats; ordinary files remain exact and authoritative. Per-trial tracking failures are
recorded separately and do not erase backtests. Missing optional dependency only blocks `--mlflow`, not experiments.

## Reproduce synthetic smoke (no network/private credentials)

```bash
uv run python - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, 'tests')
from support.synthetic import metadata_dir, synthetic_bars
from wallex_agent.market.dataset import write_dataset
p = Path('var/experiment-smoke-data')
write_dataset(p, 'synthetic-harness-qa', {'BTCTMN': synthetic_bars(3, 1e8, 0.0), 'ETHTMN': synthetic_bars(3, 5e6, 1.3)},
              extra={'note': 'synthetic infrastructure QA, not market evidence'})
metadata_dir(p / 'metadata')
PY
uv run wallex-agent experiment run config/experiments/synthetic-smoke.toml --output-root var/smoke-a
uv run wallex-agent experiment run config/experiments/synthetic-smoke.toml --output-root var/smoke-b
```

Compare trial ids/result hashes and economic metrics; wall-clock timestamps differ. Final counts/evidence are in
`PRE_STRATEGY_EXPERIMENT_HARNESS_FA.md`. No full market study or profitability claim is made.

## Integration touchpoints / limits

Only existing-file code changes: argparse registration/dispatch in `interface/cli.py` and additive canonical reporting
in `reporting/performance.py` (also preserves sub-TMN precision and classifies breakevens separately). New package
`experiments/`, tests and docs are otherwise independent. Resolve potential append-only DECISION_LOG/OSS/.gitignore
conflicts manually; retain Claude's Phase 03 changes and rerun integration checks. Do not replace Claude's versions
of kernel/recovery files. No strategy/features, risk/portfolio/execution/policy, NineRouter, runtime driver, recovery
or LIVE composition was modified. All Muse category documents were copied verbatim from owner's local files as
research evidence; their proposals do not govern strategy or risk. No new library or lockfile change.

## Review and execution rulings

One fresh-context reviewer found two Important provenance issues; failing-first tests reproduced both (3 failures),
then passed after fixes: bundle rows are reconstructed/bound to the manifest's expected configurations, dataset,
source, dependency, split/role and cost set; journaled BACKTEST config now names actual data and explicit start/end.
No Critical or deferred Minor findings. No repeated broad review; final gate is executor-owned.

Rulings retained for the owner (including reviewer boundaries): one final commit/gate under the requested time
policy (cost: coarser history); only current C1 kernel supported, other knobs via separate base configs (cost: new
strategies need kernel work); interior minute gaps retained/counts explicit, never filled (cost: sparse evidence
needs interpretation); no profitability/LIVE/parameter judgment (cost: viability remains unproven); external-path
native replay lookup not extended (cost: existing replay CLI still expects its own dataset location); opt-in MLflow
initialization may refuse before trials (cost: rerun without the optional mirror); Muse/OSS evidence not independently
endorsed as trading/legal conclusions (cost: verify before adopting); reviewer left final gate to executor (cost:
completion depends on that gate). These do not relax AGENTS safety constraints.
