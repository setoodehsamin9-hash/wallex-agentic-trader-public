# Observability/evidence handoff to Claude

Status: implementation complete; final verification recorded in `OBSERVABILITY_VERIFICATION.json`.
Base: `phase-04/strategy-round2` at `751942e7d4e36c158247366651d1cdf7c47dd196`.
Own branch: `phase-05/observability-evidence`. No main or live-readiness branch merges.
No request to continue the live-readiness task is implied by this handoff.

## Integration surface

- New independent package: `src/wallex_agent/observability/` (`source`, `reconstruct`, `inspector`, `presentation`, `bundle`, `cli`).
- Only shared source edit: `src/wallex_agent/interface/cli.py`: read-only command registration and replacement of
  the old writable-store-backed status function. `status --run-id ID` remains supported; `status ID` is added.
- Tests: `tests/observability/`. No dependencies, trading contracts, database schema or trading settings changed.
- Docs: this handoff, `OBSERVABILITY_EVIDENCE_FA.md`, work log, verification record, and `reports/submission/` checklist/inventory.
- Small engineering choice logged in `docs/DECISION_LOG.md`. Resolve that append and CLI registration locally if
  your branch touched the same lines. Do not copy this worktree's runtime state into a LIVE host.

## Public read-only Python interfaces

```python
from pathlib import Path
from wallex_agent.observability.source import read_run, EvidenceError
from wallex_agent.observability.inspector import inspect_run
from wallex_agent.observability.bundle import export_evidence, verify_bundle

source = read_run(Path('var/state.sqlite'), 'RUN_ID')
summary = inspect_run(source)  # Decimal amounts; None for undefined/missing; exactly one run
export_evidence(source, Path('evidence/RUN_ID'))  # new directory; source never changes
verified = verify_bundle(Path('evidence/RUN_ID'))
assert verified['reconstruction_matches']
```

The observer reads DB+WAL bytes twice and refuses a changing capture, copies them into private temporary scratch,
then opens only the copy with SQLite `mode=ro`, `query_only=ON`, one read transaction. This avoids SQLite creating
source -wal/-shm sidecars and retains committed active WAL data. Capturing a large database costs two reads plus
temporary storage; busy journals may require another inspection attempt. It must NOT be replaced with
JournalStore (its constructor creates/migrates schema and enables WAL). It never constructs a venue, trading runtime,
recovery coordinator, model or private client. The replay mutates only a new in-memory Ledger with no callbacks.

## CLI

```bash
uv run wallex-agent status RUN_ID --db state.sqlite
uv run wallex-agent performance RUN_ID --db backtest.sqlite
uv run wallex-agent explain RUN_ID --last 5 --db state.sqlite
uv run wallex-agent demo-evidence RUN_ID --db state.sqlite
uv run wallex-agent evidence export RUN_ID --db state.sqlite --out evidence/RUN_ID
uv run wallex-agent evidence verify evidence/RUN_ID
```

Relative --db resolves under WALLEX_AGENT_STATE_DIR (default var), absolute paths are accepted. Destinations are never
overwritten. --generated-at accepts a fixed UTC time for byte-identical output. Live status is a recorded snapshot,
not a heartbeat or exchange query. Its last evidence timestamp and optional status timestamp must remain visible.

## Persisted evidence semantics / future gaps

- RunManifest anchors environment, autonomy, source commit/dirty flag, config hash, starting capital, versions and model ids.
  Decisions/intents/config/run-scoped status must agree. Exchange/simulator source mixing refuses the report.
- State-log seq/hash chain, unique FILL deltas, checkpoints and persisted closed trades are cross-checked. Cumulative
  execution quantities/fees must never be summed as new fills. Trade PK is run_id:trade_id; body keeps the ledger id.
- Equity is recorded liquidation value, often rounded to TMN. Exact cash/realized/fees come from reconstructed fills.
  Unrealized is based on a sufficiently fresh recorded valuation. Open gross exposure needs a matching fresh status;
  otherwise it is null. Equity-table valuations must be strictly later than the last observed fill; equal timestamps
  cannot prove whether the valuation preceded the fill. A matching position/cash status can provide separate evidence.
  No fallback to invented marks, zero unrealized, or estimated cost-basis equity.
- Profit factor stays null when total losing-trade P&L is zero. No closed trades => win rate/average also null.
- `real_wallex_write_count=0` needs persisted simulator-tagged execution states matched to the state log and every
  ORDER paired with a SUBMITTING event. Merely BACKTEST/SHADOW, a caller-supplied counter or empty event table is insufficient.
- Legacy LIVE execution events do not persist every cancel operation; **LIVE full write count is UNKNOWN**.
  `observed_exchange_submissions` is explicitly a narrower count, not a total-write substitute. Any future operation
  audit belongs to the execution owner's work; add a separately tested read-only adapter once its real schema exists.
- Counts cover the captured committed journal prefix. Hashes cannot prove there was no unjournaled operation/tail.
- Restart attempts count RECOVERY_STARTED journal rows once; recovery reports/checkpoints are exported as supporting
  evidence, not additional restarts. Global current kill table is never attributed to an older run; use run-scoped status.
- Existing SignalCandidate has no strategy reason code. Explanation exposes that as null; candidate ids/features,
  stored Agent reason_codes, concise brief_rationale, risk and execution evidence remain available. No new LLM explanation.
- Only structured known refs are read: run_config (safe projection), ctx:DECISION_ID, recovery:*, news:*.
  Context payload hash must match DecisionRecord.features_ref and snapshot/candidate summaries must match the decision.
  News refs are small persisted summaries (as_of/enabled/assets/counts/flags/context_id/action/reason/story_ids).
  NEWS_* audit events also export. No news behavior or provider queries added; absent news evidence stays undefined.
- Raw model/tool recordings and full configs are excluded. Secret-bearing unstructured/chained content is refused
  before export; sensitive dictionary fields are stripped. Original payloads are identified by hashes only.

## Bundle reconstruction / reproducibility

SOURCE.json contains safe structured evidence plus exact state-chain bodies; verify checks chain/trades/checkpoints,
regenerates SUMMARY and every stable derivative artifact, and compares bytes. FILE_HASHES hashes every other file;
GENERATION.json is excluded only from the aggregate reproducible-content digest. FILE_HASHES cannot hash itself.
Original source hashes vs safe snapshot hash are explicitly different identifiers. No authenticity/signature claim.
Full model/strategy backtest replay still needs original public data/config; the existing replay command writes a new
simulated run and is outside this read-only layer.

## Verification / boundaries

Use OBSERVABILITY_VERIFICATION.json for final counts/results. Development used focused RED→GREEN tests. Final gate
runs full default pytest once because interface/cli.py is shared, strict Pyright, repository secret scan, deterministic
export/verification and separate BACKTEST/SHADOW simulator-source zero proofs. No real model or NineRouter calls,
no Wallex private/public network calls, no keys, no real orders. No strategy/F2/risk/execution/reconciliation/recovery/
warm-up/news behavior/activation/adapter source changed. Future LIVE challenge evidence/video remain pending.
