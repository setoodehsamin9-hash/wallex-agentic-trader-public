# Handoff to Phase 05

Date: 2026-10-22 · Branch **`phase-04/strategy-lab`** (from `phase-03/integrated-kernel` @ `d31e60f` + cherry-picked
experiment harness `a6148e3` → `32b0f21`; not merged to `main`). Phase 04 status: **PARTIAL** — diagnosis complete,
two families and one revision tested under pre-registered rules, **no configuration survived; TEST untouched**.

Reports: `STRATEGY_DESIGN_FA.md`, `EXPERIMENT_RESULTS_FA.md`, `PREREGISTRATION.md` (rules, committed before results),
`diagnosis/DIAGNOSIS.json`, `experiments/ROUND{1,2}_EVALUATION.{md,json}`, `experiments/TRADE_STATS_base.json`.

## 1. What changed in code (strategy layer only; no risk/execution/policy/portfolio change)

- `C1Params.exit_mode` (`TARGET` default = unchanged c1-0.1.x; `TIME` = no take-profit, stop/max-hold exit, gate
  `ATR_bps >= min_atr_cost_ratio x round trip`).
- `strategies/c2_trend_hold.py` (`C2_trend_hold`): close >= e x ATR above SMA(trend_bars) and positive trend_bars
  return; stop/max-hold exit; `trend_gate = false` (explicit, required) is the DRIFT CONTROL.
- `StrategySettings.active` + `strategies.build_strategy` / `strategy_lookback`: runtime, backtest warm-up/GAP window and
  experiment harness (`STRATEGY_SECTIONS`, refuses a base config whose `active` differs) use the active strategy.
  `c1` stays required (Codex's `agent/evaluation.py` still builds C1 directly).
- Shipped `config/backtest.toml` / `config/shadow.toml` are **unchanged**: nothing earned a promotion. The shipped
  C1 c1-0.1.0 (15m, 3-ATR target) is diagnosed as structurally loss-making and must not be used for LIVE.
- Scripts: `phase04_diagnose.py` (dev-window-only diagnosis), `phase04_evaluate.py` (applies the pre-registered
  rules), `phase04_trade_stats.py`. Experiment configs: `config/experiments/phase04/`.

## 2. Findings (evidence, dev window 2026-09-07..10-13)

1. Round trip (60 bps at 0.25%/side + 5 bps) is ~2x the 15m ATR and ~1x the 1h ATR.
2. 15m/1h returns mean-revert (VR(16) 0.69-0.81); 4h is neutral/momentum for BTC (VR 1.0-1.16), weaker for ETH.
3. Per-trade fees ~50 bps; best per-trade price move +53-55 bps (24h holds) → net ≈ 0. All 12h holds lose.
4. Fixed time exits inside persisting trends cause re-entry within 2h in ~half of C2 time exits (wasted round trips);
   48h holds fix the mechanism (+92 bps/trade) but a random-entry control with the same exits also turns positive:
   most of the gain is drift/beta (TMN B&H +16.9% BTC, +11.4% ETH; slice signs track B&H).
5. BTC positive / ETH negative in every 24h C1/C2 configuration (untested observation).

## 3. Blocking inputs (owner)

1. **Trading-window hours** (AGENTS.md 9c): if overnight holding is not allowed, every hold > ~12h tested here is
   undeployable, and all <=12h configurations lost.
2. **Real account fee** (U06): viability flips with fee tier (at 0.20%/side several C2 configs were positive in dev,
   used for information only). Read `GET /v1/account/fee` (owner-run, read-only) before Phase 05 selection.
3. Challenge market eligibility (BTC/ETH/USDT/XAUT/BNB TMN) still unconfirmed.

## 4. Codex historical data pack (`origin/phase-04/historical-data-pack` @ `a1621fd`) — needed: **YES**

It provides 1m data 2026-07-24..10-22 for BTC/USDT/ETH/XAUT/BNB (TMN) and 1h history from 2022 (XAUT from 2024).
This round failed mainly on sample size (R2: 26 trades) and regime dependence (5 weekly slices, drift-dominated);
the pack directly addresses both. Datasets are in an ignored local directory of Codex's worktree
(`data/raw/historical-pack/export/datasets/...`) and must be regenerated/copied on the run host (see its handoff).
Running 1h-bar families on 1h base data needs a check that `run_backtest` handles a non-1m base resolution
(exits would then be evaluated per hour bar — a fill-realism change to document).

## 5. Recommended Phase 05 order (Claude-led strategy work continues)

1. Merge/rebase onto the integrated base; obtain the data pack; get trading-window hours and the real fee.
2. Pre-register a second pass BEFORE looking: time-ordered TRAIN on 2026-07-24..09-30 (1m) and/or multi-year 1h,
   VALIDATION 10-01..10-13, TEST 10-13..10-22 (still untouched). Candidates: C2 v0.2 (frozen as the leading
   hypothesis, unchanged), BTC-only variant (the recorded observation), and the drift control as mandatory benchmark;
   add USDT/TMN or XAUT/TMN only with their own cost/spread assumptions.
3. Rules unchanged (R1-R7 + adverse robustness); require beating the drift control by a margin that survives the
   adverse cost. If overnight holding is forbidden, restrict to holds that fit inside one window.
4. Only survivors touch TEST once. No profitability claim without it.

## 5a. Verification at hand-off

Final gate results are recorded in `reports/phase-04/VERIFICATION_PHASE04.json` (full pytest, Pyright strict,
secret scan, deterministic experiment re-run, backtest replay).
