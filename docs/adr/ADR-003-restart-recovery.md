# ADR-003 — Persistent restart / crash recovery

Status: **Accepted for Phase 03** (SHADOW/BACKTEST implemented and tested; LIVE interfaces + fake-venue tests only)
Date: 2026-10-22 · Branch: `phase-03/restart-recovery`

## Context

Before this ADR a restart was refused (`RecoveryRequired`) because holdings, reservations, fill ids, pending exits
and approvals lived only in memory. ADR-002 requires the kill switch to survive restart, lost responses to become
`UNKNOWN` → reconcile by `client_order_id`, and trade truth to be the ledger, never framework/model memory.

## Options

1. **Periodic full snapshots** of ledger/orders. Simple, but a crash between an external effect and the next snapshot
   loses facts, and a snapshot cannot show *where* the process died.
2. **Event-sourced state log + deterministic replay** of the same kernel calls. Every mutation is persisted when it
   happens; replay reproduces the in-memory state exactly and can verify itself.
3. Adopt a framework's cache/reconciliation (NautilusTrader). Rejected: whole-platform adoption (ADR-001, licence,
   time budget); we reimplement the concept only.

## Decision

Option 2.

- **State log** (`journal.store` table `state_log`, `recovery/state_log.py`): per-run, append-only, contiguous `seq`,
  `hash = sha256(prev + body)`. Records: `ORDER` (intent, verdict, exit plan, strategy version — written **before**
  the venue call), `FILL` (fill id, amounts, dust threshold used, cash and position qty observed after it), `STATE`
  (every order state change), `RESERVE`/`RELEASE` (ledger hook), `RISK` (peak equity, max drawdown, drawdown pause,
  session baselines, AI breaker streak — on change), `CHECKPOINT` (ledger digest; clean stop, interrupt, recovery).
- **Rebuild** (`recovery/rebuild.py`): replays into a fresh `Ledger` with the same `apply_fill` arguments; verifies
  every recorded balance, intent hash, fill uniqueness, reservation balance and checkpoint digest. Any divergence →
  `RebuildError`. Loss-streak history is rebuilt from replayed trades.
- **Coordinator** (`recovery/coordinator.py`), one explicit state machine:
  `LOAD → VALIDATE → REBUILD → CLASSIFY → RECONCILE → APPROVALS → RESERVATIONS → REARM → SCHEDULE → VERIFY` →
  `READY | EXITS_ONLY | BLOCKED`. Each step appends `RECOVERY_*` risk events; the report is stored as payload ref
  `recovery:<time>`. No model, no agent loop.
- **Compatibility** (VALIDATE): schema version, environment, autonomy, config hash, limits version, trading-window
  version, starting capital, code commit, strategy versions, model id and prompt hash must equal the persisted
  manifest; LIVE additionally refuses dirty code. Mismatch → `BLOCKED` (start a new run). No migrations.
- **Ambiguity** (CLASSIFY/RECONCILE): every non-terminal order becomes `UNKNOWN`. Simulators keep no state outside
  the process, so their outcome is *settled from persisted fills* (fully filled → `FILLED`, else remainder `CANCELED`).
  A real exchange (`FillSource.EXCHANGE`) is queried by `client_order_id`; found fills apply once by fill id; not found
  stays `UNKNOWN`. **Nothing is ever resubmitted.** Then exchange balances must match the ledger within
  `kill.reconciliation_tolerance_tmn`; mismatch → `BLOCKED` + kill latch (ADR-002 trigger). No balance source →
  entries gated. While orders are still `UNKNOWN` the balance check is deferred (`BALANCE_RECONCILIATION_PENDING`):
  the runtime runs it once, when the last order gate clears; only a pass reopens entries, a mismatch latches kill.
- **Unexpected errors** inside recovery are journaled as `RECOVERY_ERROR` → `BLOCKED` + kill latch.
- **Entry gate**: while any order is `UNKNOWN` or balances are unverified the outcome is `EXITS_ONLY` — exits,
  reconciliation and liquidation run, every decision cycle is `BLOCKED (RECOVERY_RECONCILIATION_PENDING…)` before the
  model is called. Polls clear order gates automatically once the venue answers (`RECOVERY_RECONCILED`).
- **Approvals: invalidated on restart.** PENDING/APPROVED tickets of the run become `EXPIRED` with a note; their
  reservations are released. Reason: a human approval binds to a moment and a context; replaying it after an
  unobserved gap is unsafe, and AUTONOMY_AND_PERMISSIONS does not require persistence. The algorithm re-proposes and
  risk re-evaluates; approval still never overrides REJECT.
- **Reservations**: exactly those of non-terminal entry orders survive (conservative while UNKNOWN); the rest are
  released (journaled). A non-terminal entry without its reservation → `BLOCKED`.
- **Exits**: positions keep their `ExitPlan`; a non-terminal (incl. `UNKNOWN`) SELL stays the single pending exit, so
  no second sell is sent. A position without plan or outside the watched symbols latches the kill switch. Runners
  prime market snapshots so the exit watcher works before the next bar (`TradingRuntime.prime_snapshot`).
- **Kill switch**: already persisted globally; recovery reports it and latches it for untrustworthy state
  (corrupt log, failed rebuild, missing state log, balance mismatch, invariant violation). Incompatible config does
  **not** latch (it is an operator choice, not corruption).
- **Composition**: `TradingRuntime(resume=True)` builds no journaling hooks until `install_recovered`; reusing a run
  without `resume` still raises `RecoveryRequired`. SHADOW: `resume_shadow` → coordinator → shared `_drive` loop;
  `BLOCKED` raises `RecoveryBlocked` before any cycle. CLI: `shadow --resume RUN_ID`, read-only `recover RUN_ID`.
- **Sealed runs are not reopened** (`RUN_SEALED`); a normal end writes a `SHUTDOWN` checkpoint before sealing.
  An operator who chooses not to resume (e.g. after a code change) runs `wallex-agent abandon RUN_ID --reason R
  --operator NAME`: journaled `RUN_ABANDONED` (with the rebuilt open positions/orders), then sealed. Refused for LIVE.
- **INCOMPATIBLE** latches the kill switch in LIVE only (real positions would otherwise be left unmanaged).

## Consequences

- The chain is an unkeyed sha256: it detects accidental corruption, gaps and in-place edits, not a deliberate rewrite
  that recomputes every hash, and not truncation of the most recent records. In LIVE the exchange balance check is
  the backstop; a keyed/externally anchored head hash is a possible later hardening.
- Each mutation costs one SQLite insert (WAL). Backtests add roughly one `RISK` row per marked bar.
- Equity curve history before the crash is not restored (reporting only); `consecutive_errors` restarts at 0
  (an already latched kill stays latched).
- LIVE still needs: a real private `query(client_order_id)` adapter verified against Wallex, an account balance source
  (`AccountPort.balances`), and an owner decision whether a long-`UNKNOWN` order may ever be declared absent. Until
  then a LIVE resume with unresolved orders stays `EXITS_ONLY`.
- BACKTEST is re-run, not resumed (deterministic); `inspect_run` verifies any run's log replays to its final ledger.
