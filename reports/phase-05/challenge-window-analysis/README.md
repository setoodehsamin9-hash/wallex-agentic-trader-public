# Frozen F2: 72-hour evidence

This directory is analysis evidence, not trading configuration or permission to start LIVE.
The frozen checkpoint is `751942e7d4e36c158247366651d1cdf7c47dd196`.

Run from the isolated `phase-05/challenge-window-evidence` worktree:

```bash
uv sync --frozen --python 3.12
uv run python scripts/challenge_windows.py run --workers 4
uv run python scripts/challenge_windows.py verify --workers 4
```

Requires the existing validated Phase 04 pack at `data/raw/historical-pack/export/`.
The pack is deliberately ignored by Git. Obtain the existing owner-approved local archive;
do not silently fetch replacement data with different hashes. The loader checks the series
against committed `data/historical/MANIFEST.json`; BLOCKED data are refused.
Evaluation and every control end before the protected TEST at 2026-10-13 00:00 UTC.

Files:

- `SUMMARY.json`: provenance, exact cost/config/source/data hashes, six kernel distributions,
  winner concentration, start and regime buckets, 18 non-overlapping offsets, matched research controls.
- `WINDOWS.csv.gz`: one row per (4-hour rolling start, independent BTC/ETH/shared-capital portfolio, cost).
  Every account starts at 5,000,000 TMN. `valid=False` rows remain inspectable but are excluded from statistics.
- `TRADES.csv.gz`: every closed kernel trade from those runs. The same historical entry can occur in many
  overlapping accounts; these are not unique independent trades. `window_id` joins to `WINDOWS.csv.gz`.
- `REFERENCE_TRADES.csv.gz`: pooled kernel trades from the seven original, disjoint VALIDATION slices,
  each starting flat. This measures the untruncated multi-day strategy's winner concentration and holding time.
- `BOUNDARY_DIAGNOSTICS.json`: production BACKTEST replays of five observed non-flat endpoints,
  including a confirmed expired, unfilled terminal liquidation in the hourly simulator. These remain invalid;
  no synthetic fill or execution fix was applied.
- `CONTROLS.csv.gz`: normalized research F2, the already available filter-off twin, buy-and-hold,
  and 20 deterministic random-entry paths, for the same starts. Return units are percent, not TMN.
  Only the same valid kernel starts enter control comparisons. Retained invalid control rows are flagged.
- `HASHES.json`: SHA-256 of all five analytical products, including canonical `SUMMARY.json`.
- `DATA_INTEGRITY.json`: measured equality of the hourly dataset manifest and all four public metadata
  files against the committed Phase 04 pack manifest.
- `VERIFICATION.json`: final rerun, test, type-check, scope and secret-scan evidence.
- `PROVENANCE_REFRESH.json`: transparent resolution of the first strict JSON hash mismatch. The full
  final-source rerun reproduced all four CSVs byte-for-byte and every non-provenance JSON field. Only four
  provenance keys differed because their wording changed while the first long run was active. The final
  summary comes from the complete final-source rerun; no analytical number was edited.
- `TRIALS.jsonl`: analysis attempts, including an interrupted development run; no strategy search.

Important definitions:

- Kernel results use the untouched production `TradingRuntime`, permission policy, Risk Engine,
  ledger and hourly BACKTEST venue. Pooled means one 5,000,000-TMN shared account with both symbols;
  it is not the sum or average of two independently funded accounts. Limits remain simulation placeholders.
- Every normal kernel run invokes the existing end buffers: stop entries 4h before the endpoint and begin
  liquidation 2h before it. Orders can fill only at subsequent observed base bars. Pending orders, residual
  positions/dust or missing first/final bars make a window invalid. No artificial terminal fill is inserted.
- The original F2 history/GAP rule remains in effect. No missing candle is filled or repaired.
- `regime_start` is computed only from bars closed at the window start, using F2's existing causal rank layer.
  `regime_mix` counts the same rank labels at closed decision times during the run. It is distinct from the older
  SMA/ATR regime label also present in production features. Pooled buckets identify both symbols' start labels.
- An exceptional trade is retrospectively at/above p90 of positive trade TMN P&L for that book/cost.
  A dependent profitable window becomes non-positive after subtracting its largest positive trade.
  This is attribution with all other fills/sizing held fixed, not a resimulation or a trading rule.
- Top 1/5/10% counts use all observations, rounded up, with only positive winners contributing. Both share
  of gross winning P&L and share of signed net P&L are given. A share of net above 100% is possible;
  a negative denominator is explicitly flagged. Window sums are hypothetical reset-account sums.
- Forced-flatten P&L is the full net trade P&L of `LIQUIDATION` trades. The separate forced impact is actual
  net sale proceeds minus the same quantity valued at the final observed gross close. It includes timing,
  sell fee and slippage. It does not estimate the unknowable profit from continuing into the future.
- Research random controls use TRAIN entry-signal density in the same regime-eligible decision pool;
  no VALIDATION outcome is used to fit the rate. ATR stop/trail, EMA exit, next-open entry cap and 168h cap
  come from the existing simulator. Signal-density matching does not ensure equal realized trades/exposure;
  those distributions are exported. Research F2 and controls are fully invested normalized sub-accounts,
  equal weight when pooled; research pooling and GAP handling differ from the kernel.
- Adjacent rolling windows share 68 of 72 hours. Percent positive is a historical descriptive frequency,
  not a calibrated future probability. The 18 offsets report sensitivity of non-overlapping subsets;
  they do not remove broader serial dependence or constitute confidence intervals.
- No nightly trading schedule or official competition hours are invented. A nightly forced-flat rule would
  require separate evidence from the frozen strategy and may truncate it further.
- No AI selection, private Wallex data, model calls, liquidity queue, historical spread or market impact
  is represented. This establishes evidence about frozen deterministic F2/B0, not LIVE readiness.

Example read (standard library only):

```python
import csv, gzip
with gzip.open("reports/phase-05/challenge-window-analysis/WINDOWS.csv.gz", "rt") as handle:
    for row in csv.DictReader(handle):
        if row["book"] == "POOLED" and row["cost"] == "base" and row["valid"] == "True":
            print(row["start"], row["return_pct"], row["trades"])
```
