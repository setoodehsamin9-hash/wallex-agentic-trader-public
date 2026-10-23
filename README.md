# Wallex Agentic Trader

Agentic AI trading engine for the Wallex × Starkooch Agentic AI challenge: a **bounded AI agent** chooses among
deterministic trade candidates (or waits) inside a **deterministic risk and execution kernel** it cannot bypass.
Same code for backtest, shadow and live; every decision journaled and replayable.

> **Status (Phase 05).** The full pipeline runs in BACKTEST, SHADOW and LIVE from one core:
> strategy **F2** (4h time-series momentum, frozen) → **bounded AI veto agent** (GPT-6.1 Sol through NineRouter,
> read-only tools only) → validator → permission policy → risk engine → execution. LIVE has its own runner
> (`runtime/live.py`) with a read-only account preflight, balance reconciliation and crash resume; it starts only from the
> owner's machine with a committed, tagged tree and the owner's key in an environment variable. Results of any run are in
> `runs/published/` and `reports/`; this README makes no profitability claim.
> **Presentation guide (Persian): [docs/PROJECT_GUIDE_FA.md](docs/PROJECT_GUIDE_FA.md).**

## Setup

Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest            # Layer 1+2: deterministic + replay tests (no network, no model)
uv run pyright           # strict type check
```

Real-model tests and evaluation go **only** through NineRouter (Layer 3, opt-in). Keep the key outside the repo:

```bash
export NINEROUTER_API_KEY_FILE=/absolute/path/outside/repo/key.txt   # or NINEROUTER_API_KEY
export NINEROUTER_BASE_URL=http://localhost:20128/v1                   # default
export NINEROUTER_MODEL=av/claude-sonnet-5-5                           # any id from GET /v1/models
uv run pytest -m ninerouter
uv run wallex-agent eval-ninerouter --models av/claude-sonnet-5-5 av/claude-haiku-5-5 --repeats 3 --mcp
```

## Run

```bash
# Backtest on the hash-pinned dataset (data/datasets/wallex-1m-btctmn-ethtmn, 45 days, 1m)
uv run wallex-agent backtest --config config/backtest.toml --split TEST --export            # B0 baseline
uv run wallex-agent backtest --config config/backtest.toml --split TEST --decider LLM --export   # AI via NineRouter
uv run wallex-agent replay <RUN_ID> --split TEST       # re-run with recorded model outputs, no network, compare

# The six-trial study has already run: read reports/phase-03/BACKTEST_REPORT.md.
# Do not rerun scripts/backtest_study.py over the existing report/store paths: it opens TRIALS.jsonl for writing.
# The committed study contains B0/cost stress; the AI ablation is still pending.

# Shadow: live public Wallex data, real decision path, simulated fills, zero real orders
uv run wallex-agent shadow --config config/shadow.toml --duration 3600 --decider LLM --autonomy AUTONOMOUS
uv run wallex-agent shadow-drills              # failure-injection drills on a live capture

# While a shadow run is active (another terminal)
uv run wallex-agent status                     # Persian status: mode, entries allowed, P&L, return, drawdown, kill switch
uv run wallex-agent ask "چرا معامله نکردی؟"     # read-only assistant (NineRouter)
uv run wallex-agent approve <TICKET>           # APPROVAL_REQUIRED: approve/reject the algorithm's exact trade
uv run wallex-agent kill --reason "..."        # latch: no new entries; exits continue
uv run wallex-agent reset-kill --reason "..." --operator NAME

# LIVE (owner only): fill config/live-f2.toml, set WALLEX_API_KEY and NINEROUTER_API_KEY_FILE in the shell, tag the commit
uv run wallex-agent preflight --config config/live-f2.toml --account-out var/account.json   # read-only (GET)
uv run wallex-agent readiness --config config/live-f2.toml --format table                   # PASS / BLOCKED list
uv run wallex-agent run --env LIVE --config config/live-f2.toml [--resume RUN_ID]
```

Exports land in `runs/published/<RUN_ID>/`: `SUMMARY.json`, `TRADES.jsonl`, `DECISIONS.jsonl`, `RISK_EVENTS.jsonl`,
`PERFORMANCE_FA.md`, `MANIFEST.json` (secret-scanned). Journals live in `var/` (git-ignored).
Replay requires the source SQLite journal and dataset; exports alone are not yet a complete portable replay bundle.
New runs record their config for replay. Older runs require the exact original config/split. An unfinished SHADOW
session blocks a new session over the same journal because automatic recovery has not been implemented.

## How it works

```
Wallex data ─> snapshot + features ─> F2 candidate ─> AI veto agent (read-only tools, NineRouter) ─> AgentDecision
   ─> validator ─> permission policy ─> risk engine (sizes, may reject) ─> [approval ─> risk again] ─> execution
   ─> ledger / reconciliation ─> automatic exits (stop / take-profit / time / window / cutoff) ─> decision journal
```

- **Autonomy:** `ADVISOR` (propose only) · `APPROVAL_REQUIRED` (operator approves/rejects the algorithm's complete
  trade; no editing) · `AUTONOMOUS` (final competition mode; LIVE activation stays gated).
- **Environments:** `BACKTEST` · `SHADOW` · `LIVE` — same core, different adapters.
- **Trading windows:** required for LIVE (`[trading_window]`, not guessed); outside a window no entries, flatten before close.

## Documentation

| Topic | File |
|---|---|
| Architecture, scoring map, Phase 03 notes | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Decision cycle and failure handling | [docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md) |
| Autonomy modes and permissions | [docs/AUTONOMY_AND_PERMISSIONS.md](docs/AUTONOMY_AND_PERMISSIONS.md) |
| Data contracts | [docs/DATA_CONTRACTS.md](docs/DATA_CONTRACTS.md) |
| Wallex MCP tool inventory | [docs/WALLEX_TOOL_MAP.md](docs/WALLEX_TOOL_MAP.md) |
| Decisions | [docs/adr/](docs/adr/), [docs/DECISION_LOG.md](docs/DECISION_LOG.md) |
| Phase 03 plan (with Amendment A) | [docs/superpowers/plans/2026-10-22-phase-03-trading-kernel.md](docs/superpowers/plans/2026-10-22-phase-03-trading-kernel.md) |
| Rules for contributors and coding agents | [AGENTS.md](AGENTS.md) |
| Owner reports (Persian) and handoffs | [reports/](reports/) |
| Takeover verification, partial gates and Claude handoff | [reports/phase-03/HANDOFF_CODEX_TO_CLAUDE.md](reports/phase-03/HANDOFF_CODEX_TO_CLAUDE.md) |
| LIVE blockers and future activation procedure | [docs/LIVE_RUNBOOK.md](docs/LIVE_RUNBOOK.md) |

## Safety

The AI holds no write tool and cannot name a symbol, price, quantity or stop; every AI failure is a WAIT; the risk
engine's REJECT is final; a lost order response is UNKNOWN and reconciled by client order id, never resubmitted.
Real-model calls go only through NineRouter. No key, token or account identifier is stored in this repository: the
Wallex key is read from `WALLEX_API_KEY` at call time and sent only in the `X-API-Key` header. LIVE requires
owner-approved limits, the competition clock, the U04 answer for AUTONOMOUS, a passing account preflight and a clean
tagged commit.

This public repository is a single clean snapshot (no earlier history). Third-party reference documents with unclear
licenses were left out; open-source ideas are listed in [docs/OPEN_SOURCE_DECISIONS.md](docs/OPEN_SOURCE_DECISIONS.md)
(no upstream code was copied). No license is granted by default: all rights reserved by the author.
