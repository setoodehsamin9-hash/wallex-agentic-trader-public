# Phase 03 backtest evidence — PARTIAL

Date: 2026-10-22. No strategy parameters were retuned during takeover. The locked TEST dataset was not rerun for selection.

## Preserved Claude study

`backtest/STUDY.json` and `backtest/TRIALS.jsonl` contain 14 rows: six pre-registered C1 settings on TRAIN/VALIDATION,
then T2 on TEST plus a cost-stress run. They contain **no LLM ablation row**; the prior handoff's implied completed
AI ablation is not supported by these files.

Data: 2026-09-07 through 2026-10-21 UTC, 1m BTCTMN/ETHTMN, time-ordered 60/20/20 split. Dataset file hashes are
verified by `load_dataset`. Coverage is 64,704/64,800 minutes for BTC (96 missing) and 64,675/64,800 for ETH
(125 missing); missing minutes are not filled. Metadata is a historical snapshot and does not prove current account eligibility.

| Preserved result | Trades | Realized TMN P&L | Return | Fees TMN | Max drawdown |
|---|---:|---:|---:|---:|---:|
| T2 VALIDATION, selected by least negative validation return | 9 | -141470 | -2.8294% | 67065 | 2.8294% |
| T2 TEST baseline | 20 | -145716 | -2.9143% | 133452 | 2.9143% |
| T2 TEST, fees and slippage ×2 | 5 | -35257 | -0.7051% | 56529 | 0.9790% |

The stress run trades less because the cost filter changes candidate availability. Its smaller loss does not show
that higher costs improve the same trades. None of these results proves positive expectancy.

## Fresh takeover verification

Code `bb76e58e29ba2706d7428353ac4b25339130355a`, clean manifest, unchanged shipped T1 config, VALIDATION only:
2026-10-04T00:00Z–2026-10-13T00:00Z. 1,726 decisions, 9 trades, realized **-165497 TMN (-3.3099%)**, fees 64505 TMN,
maximum drawdown 3.3099%, final cash/equity 4834503 TMN. No open positions, dust or UNKNOWN orders. This matches
the preserved T1 validation result exactly. Export: `runs/published/codex-takeover-validation/`.

Replay of the recorded B0 decisions reproduced all 1,726 decision outcomes and trade P&Ls, with zero missing
recordings and no model inference. A separate automated test blocks sockets during a complete baseline replay.
Machine-readable results: `CODEX_VERIFICATION.json`.

## Assumptions and remaining gates

Assumed fee 0.25% per side; slippage 5 bps per side; entries fill at next minute-bar open subject to limit/TTL;
stop/TP in one candle takes the stop; gaps use the worse stop fill. No historical depth, queue or market-impact model.
Risk limits are simulation placeholders, not owner-approved. No profitability or LIVE-readiness claim is made.

Still missing: real-model benchmark, AI-vs-B0 ablation and end-to-end AI replay evidence through NineRouter,
latency/cost stress beyond the present simulator, and a fresh forward period before further strategy selection.

Reproduce the verification without rerunning the parameter study:

```bash
WALLEX_AGENT_STATE_DIR=var/codex-takeover uv run wallex-agent backtest --config config/backtest.toml --split VALIDATION --decider BASELINE --run-id <new-run-id> --export
WALLEX_AGENT_STATE_DIR=var/codex-takeover uv run wallex-agent replay <new-run-id>
```
