# Phase 04 Experiment Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make owner/Claude-supplied hypotheses reproducible and inexpensive to test on the existing deterministic kernel.

**Architecture:** Versioned TOML defines explicit UTC periods, a bounded Cartesian grid, and explicit cost scenarios. A BACKTEST-only wrapper validates immutable variants, calls the existing driver with B0, and writes append-only trial evidence plus derived cross-period reports. The canonical ledger-based performance path is extended; optional local MLflow mirrors ordinary files.

**Tech Stack:** Existing Python 3.12, Pydantic, Decimal, SQLite journal, optional MLflow 3.8.1; stdlib itertools/csv/tomllib/hashlib only.

**Spec:** Owner task in this chat; preserved scope and acceptance contract below. `AGENTS.md` remains binding.

## Global Constraints

- Base `671f661`; branch `phase-04/experiment-harness-prep`; isolated worktree; no integration or merge.
- No entry/exit, strategy, feature, risk, recovery, agent, private Wallex or LIVE changes.
- Explicit chronological half-open UTC splits; no ratios chosen here. TEST runs only with intentional flag plus one exact parameter hash.
- Maximum trial count applies to grid × splits × costs before any backtest; canonical config, source, dataset (including metadata), split and result hashes.
- No network or real model. Cost scenarios are assumptions, never final account fees. No automatic strategy selection.
- Track every attempted trial including failure. Decimal JSON values are strings; undefined metrics are null.
- Focused tests during development; one final full pytest/Pyright/secret-scan/smoke gate, then one commit and push. No merge.
- Import local Muse category files verbatim as evidence only.

## Review Focus

- Mutated metadata changes provenance; missing symbols, out-of-order/duplicate bars and misaligned boundaries fail before trials. Interior minute gaps remain unfilled, counted explicitly, under the unchanged kernel's data-quality gates.
- Equivalent decimal grid values and duplicate scenarios: reject semantic duplicates; no duplicate submissions.
- Test opt-in accidentally selecting a whole grid: require exactly one parameter hash; no automatic selection from test.
- Partial output / failed trial / tracking failure: retain explicit failures and never silently overwrite evidence or represent them as zero return.
- Multiple positions with overlapping holding times: time-in-market uses a union, bounded by the evaluation period.

---

### Task 1: Canonical performance metrics

**Files:** Modify `src/wallex_agent/reporting/performance.py`; create `tests/reporting/test_performance.py`.

**Interfaces:** Consumes `Ledger`, `TradeRecord`, existing `compute_summary(...) -> PerformanceSummary`. Produces additive gross-profit/loss, profit-factor, fee-burden, breakeven, holding-time and time-in-market fields through the same function; monetary amounts retain Decimal precision.

- [x] Write failing tests: zero trades => win rate/average/profit factor/holding null; all wins => profit factor null; all losses => factor 0; mixed +10/-4/0 => gross 10/4, factor 2.5, one win/loss/breakeven; fees 0.25 retained; overlapping intervals => union rather than sum; invalid capital/period fails.
- [x] Run `uv run pytest tests/reporting/test_performance.py` (Expected: missing metrics/incorrect loss count).
- [x] Extend canonical summary without touching ledger or trade economics; validate metric inputs; document denominator and after-fee profit-factor definition.
- [x] Run same focused tests (Expected: PASS).

### Task 2: Experiment specs, grid and kernel runner

**Files:** Create `src/wallex_agent/experiments/{__init__,spec,runner}.py`; `tests/experiments/{conftest,test_spec,test_runner}.py`.

**Interfaces:** Consumes Task 1 summary, `RunConfig`, `Dataset`, `run_backtest(...,period=...)`, `JournalStore`, `BaselineSelector`. Produces `ExperimentSpec`, `TimeSplit`, `CostScenario`, `parameter_configs(spec,base)`, `run_experiment(spec,base,dataset,output_root,*,evaluate_test=False,parameter_hash=None,mlflow=False) -> Path` and immutable `TrialResult` records.

- [x] Write failing tests for ordered non-overlap, unknown keys, stable hashes/order, max count, normalized duplicate values, unknown strategy/parameters, BACKTEST/B0 guards, future-bar prefix invariance, held-out TEST gating, metadata hashes and coverage, fee/slippage sensitivity, unchanged input configuration and source, failure recording and exclusive output creation.
- [x] Run `uv run pytest tests/experiments/test_spec.py tests/experiments/test_runner.py` (Expected: feature absent).
- [x] Implement explicit time splits and bounded sorted Cartesian products. Validate every variant using existing contracts; bind only strategies the runtime supports. Trim future bars before kernel invocation; let the unchanged driver read past-only warmup.
- [x] Produce hash-identified per-trial journals, manifest, JSONL and exact cost/config metadata, failure type (no exception payload secrets), timestamps and reproducible result hashes. Reject existing experiment directories.
- [x] Run same focused tests (Expected: PASS).

### Task 3: Reports, CLI, optional tracking and handoff

**Files:** Create `experiments/{report,tracking,cli}.py`; modify `interface/cli.py`, `.gitignore`, `docs/{DECISION_LOG,OPEN_SOURCE_DECISIONS}.md`; create example TOML, tests and Phase 04 handoffs.

**Interfaces:** Consumes Task 2 immutable records. Produces `summarize_experiment(path)`, SUMMARY.json/CSV, concise REPORT_FA.md; CLI `experiment run CONFIG` / `experiment summarize ID` with configurable output root; optional local SQLite MLflow mirror.

- [x] Write failing tests: period/cost matrix retained; sign reversals, low-count and cost-fragility observations; failed trials remain null; no best selector; CSV round trip; CLI paths relative to spec; tamper detection on summarize; optional MLflow native local storage and no default import/network; tracker failure preserves ordinary artifacts.
- [x] Run focused tests (Expected: missing report/CLI/tracking).
- [x] Implement derived reports and CLI following argparse patterns, safe identifier/path validation and versioned example (infrastructure QA only). Use manual native MLflow logging; no autolog or remote URI.
- [x] Run focused tests (Expected: PASS).
- [x] One fresh-context whole-branch reviewer against working diff and this plan; fix important findings with failing-first tests. No per-task reviewer or repeated broad review.
- [x] Final gate: `uv run pytest`, `uv run pyright`, secret scan of candidate tracked files plus experiment smoke artifacts, deterministic synthetic smoke twice for hash/metric equality. Expected: all PASS (optional network/model tests deselected).
- [x] Finish concise Persian/English handoffs including exact branch and self-identifying commit command; commit once after gate, push branch; verify clean worktree and remote hash. No merge or PR integration.

## Execution record

Implementation is inline as requested. The owner explicitly authorized execution and push, so no plan-confirmation pause is added. Detailed task evidence lives in this plan's scratch ledger until the final handoff.
