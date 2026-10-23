# Observability implementation ledger

Spec: owner request in this chat. Base: 751942e7d4e36c158247366651d1cdf7c47dd196.
Branch: phase-05/observability-evidence. Only this worktree is writable.

1. Read-only snapshot reader, strict source validation, pure ledger reconstruction and metrics (RED → GREEN).
2. Stored decision explanations, Persian operator CLI, deterministic export/reconstruction/hash checks (RED → GREEN).
3. Submission/demo documentation, one independent evidence/boundary review, focused fixes, one final gate, commit/push.

Pre-flight: 1 produces the immutable observation consumed by 2; exports carry a source snapshot so offline reconstruction uses the identical observer. 3 consumes test/export evidence only.

Ruling: use the owner's detailed specification as the execution plan; keep a short ledger instead of generating a second planning workflow. Focused tests during development; full pytest once at final gate (CLI is shared).
Ruling: record-and-replay skill is absent. Implement ordering/hash/reconstruction requirements directly. reporting-analyst and generate-status-report are service-specific; use only faithful-evidence/concise-status presentation guidance, no service queries or publishing.
Ruling: never instantiate JournalStore in observer commands (its constructor changes schema/WAL). Read SQLite mode=ro, query_only, one read transaction; no immutable=1 because active WAL must be visible.
Ruling: legacy exchange lifecycle events do not audit every cancel/write. Count remains UNKNOWN for LIVE without complete operation-level audit evidence. Simulated zero needs matched persisted ORDER/STATE and simulator source evidence, never environment alone.

Task 1: complete. 16 focused inspector tests pass (P&L, fees, drawdown, undefined values, restart counting, UNKNOWN, source identity, read-only/malformed evidence).
Task 2: complete. 26 focused tests pass, including native runtime BACKTEST and SHADOW journals, deterministic exports, timestamp-independent content hashes, secret rejection/filtering, CLI, and offline bundle reconstruction.
Compatibility finding: trades table primary key is run_id:trade_id while body keeps the ledger trade_id. Reader validates this existing namespacing.

Final review: one independent reviewer, five Important findings, no Critical/minor findings. All accepted; no second review requested.
- Source sidecars: RED demonstrated -shm/-wal creation; fixed by twice-byte-checked DB+WAL capture into private scratch, SQLite on copy only. Native active-WAL test also verifies unchanged source directory/files.
- Event-only orders: RED showed phantom INTENT orders after FILLED; fallback now advances state/filled, with no inferred cash.
- Context binding: RED replaced an internally valid ctx payload; now payload hash/snapshot/candidate summaries bind to the recorded decision.
- Equal-time valuation: RED reproduced pre-fill equity at the same timestamp; equity-table freshness is strictly later, otherwise undefined.
- Missing source tables: RED dropped decisions/exec_events; derived AI/risk/execution counts now remain undefined.
Review declined-to-judge rulings: actual exchange/model behavior is outside authorized scope and no calls were made; documentation and final gate remain parent-owned and will be completed before push. No deferred minors.
Ruling update: direct mode=ro is insufficient for the no-source-sidecar guarantee. Use scratch copy with stable byte capture; no immutable=1 on a live source. Costs: two full DB/WAL reads and temporary storage; changing snapshots fail honestly.
Final-gate repair: full pytest first pass 504 passed / 2 skipped / 2 deselected. Strict typing exposed a test fixture's protocol parameter name (context vs ctx); renamed, focused test passed, strict typing then clean.
Final-gate smoke repair: real native context bundle exposed insertion-order-dependent demo text after SOURCE JSON roundtrip. Extended native integration test RED, changed structured display to canonical JSON, all 41 observer tests GREEN. Run the final required checks again after this presentation-only fix; no trading code changed.

Task 3: complete. Final repaired gate: full pytest 504 passed / 2 skipped / 2 deselected; Pyright strict 0 errors; repository secret scan 0 findings; native recorded BACKTEST and SHADOW exports byte-identical at fixed timestamps; different generation timestamps preserve stable content hash; API and CLI exports identical; every stable artifact reconstructs. Evidence and proof counts are in OBSERVABILITY_VERIFICATION.json; local synthetic bundles remain under var/observability-final-gate/.
Finish ruling: owner already chose commit/push on this branch, without merge or PR. Keep the isolated worktree and its ignored smoke artifacts for review/integration; no cleanup of Claude's worktree or any other checkout.
