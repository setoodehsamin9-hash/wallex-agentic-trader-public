# Phase 03 SHADOW evidence — PARTIAL

Date: 2026-10-22. Every run below uses simulated execution. No private Wallex call or real order was made.

## Fresh public-data smoke runs

Public Wallex REST data, B0 selector, 1-minute smoke cadence, nominal duration 130 seconds each. Configuration:
`shadow/PUBLIC_SMOKE_CONFIG.toml`; owner-approved limits remain false. This is adapter verification, not a strategy tuning trial.

| Run | Autonomy | UTC interval | Decisions | Candidates / simulated orders | Execution errors / UNKNOWN |
|---|---|---|---:|---:|---:|
| codex-public-advisor | ADVISOR | 13:08:03–13:10:13 | 4 | 0 / 0 | 0 / 0 |
| codex-public-approval | APPROVAL_REQUIRED | 13:12:05–13:14:32 | 4 | 0 / 0 | 0 / 0 |
| codex-public-autonomous | AUTONOMOUS | 13:15:27–13:17:54 | 4 | 0 / 0 | 0 / 0 |

Actual duration includes bounded I/O. All twelve cycles recorded `NO_CANDIDATES`; no trade was manufactured to
exercise activity. All runs ended with zero open positions and zero model calls. Their exports and exact source
commits are under `runs/published/<run>/` and `CODEX_VERIFICATION.json`. These short quiet samples do not validate
real-model decisions or market fills.

## Deterministic acceptance evidence

The fake public-feed test runs twelve virtual hours for each autonomy mode, using the real kernel:
ADVISOR never submits; APPROVAL_REQUIRED consumes scripted operator approvals; AUTONOMOUS submits simulated
entries and arms exits. A blocking-provider integration test proves public-feed maintenance continues during
inference. Another test executes a stop before releasing the provider; deadline expiry becomes WAIT.

Dedicated tests reproduce lost response → UNKNOWN → lookup without duplicate submit, permanently unresolved
UNKNOWN retaining fail-closed state, partial fills, late fills after cancel, multi-level exits, same-tick reservations,
cutoff/window closure, persisted CLI kill, run-scoped approvals and host time after model latency.
`tests/runtime/test_drills.py` verifies the nine inherited drill scenarios on a synthetic capture. The earlier public
capture and `shadow/SHADOW_DRILLS.json` were preserved, including their limited real-NineRouter timeout marker.

## Remaining gates

Durable restart recovery is missing; current startup guard refuses unsafe continuation. Shadow fills are IOC on
visible depth, with no queue model. Real-model shadow/benchmark, broader outage/recovery evidence and a longer
forward run remain required. No LIVE-ready or profitability conclusion is supported.

Reproduce a fresh public smoke run with a new state directory/run ID:

```bash
WALLEX_AGENT_STATE_DIR=var/public-smoke-new uv run wallex-agent shadow --config reports/phase-03/shadow/PUBLIC_SMOKE_CONFIG.toml --duration 130 --decider BASELINE --autonomy ADVISOR --run-id <new-run-id>
```

Repeat with APPROVAL_REQUIRED and AUTONOMOUS using separate new journals. Do not reuse an unfinished journal to bypass recovery.
