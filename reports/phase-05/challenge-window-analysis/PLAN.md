# Frozen F2 challenge-window evidence

Specification: the owner's Phase 05 analysis-only request in this chat. Base: `751942e`.
Scope: scripts, focused tests, analysis evidence and a decision-log entry only. No production edits.

## Task 1: Produce reproducible 72-hour evidence

1. Test first: window boundaries/TEST guard, Decimal distribution and winner-removal accounting,
   terminal-flat validation, cost-aware terminal-exit attribution, causal regime classification,
   pure feature-cache parity, and deterministic compact output.
   Expected: focused tests fail for missing analysis functions, then pass after implementation.
2. Reuse hash-verified non-BLOCKED Phase 04 hourly data and untouched F2 config. Every 4h start in
   VALIDATION [2025-03-01, 2026-10-13), 72h per reset, 5,000,000 TMN; BTC, ETH and shared-capital pooled
   kernel runs, base/adverse costs. Keep invalid/non-flat results visible and out of valid-return statistics.
3. Reuse research-only F2/off and random-entry exit mechanics; compare equal-weight, fully invested
   research controls separately. Record actual exposure/trade differences. No fitted strategy parameters.
4. Record overlap dependence, regime at start and during each window, UTC/Tehran start buckets,
   winner concentration, holding durations, terminal liquidation P&L/cost and full-period reference trades.
5. Write compact gzip CSVs, canonical SUMMARY.json, provenance and readable Persian owner report.
6. One fresh whole-branch reviewer; one final gate: deterministic full rerun/hash comparison,
   relevant tests, strict Pyright, secret scan. Expected: byte-identical analysis products, tests and typing green,
   no secret findings. Per explicit owner instruction, no full project suite for analysis-only files.
7. Commit and push `phase-05/challenge-window-evidence`; no merge, live action or model call.

Review focus: causal warm-up, no TEST reads, fees twice, shared-capital pooling, invalid/non-flat windows,
overlapping observations, random-control comparability, terminal-exit attribution and winner-removal denominators.
