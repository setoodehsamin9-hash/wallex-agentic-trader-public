# Handoff to LIVE launch (Phase 05 final pre-LIVE, 2026-10-23)

Branch `phase-05/final-prelive` (not merged to main). No real trade, no private Wallex call, REAL WALLEX WRITES = 0.

## State

- **Agent:** strategy-aware brief (`f2-brief-1`) and the frozen **VETO** role (`veto_v1`, sha `8a71819a…7346`, model
  `cx/gpt-6.1-sol` via NineRouter only). Rationale is in ADR-005.
- **Fresh evaluation** (V1..V5, 67 cases, selection rule fixed in advance): SELECTOR 0.989 and VETO 0.989, so VETO
  wins the tie as the role with less authority.
- **Retrospective holdout** (417 V6+V7 cases, run once after freezing, nothing tuned): 415 VALID, 2 UNKNOWN_REFERENCE
  (the model miscopied the snapshot id, so both became WAIT), 0 vetoes. It entered 17 of the 18 deterministic entry
  points; decision_v2 entered 0 of 18.
- **Counterfactual replay in the real kernel, 2026-08-20..10-13:**

  | Decider | Trades | Net TMN | Max drawdown |
  |---|---|---|---|
  | Deterministic F2 | 18 | +228,155 | 1.25% |
  | decision_v2 | 9 | +186,966 | 0.70% |
  | Frozen VETO | 18 | +205,845 | 1.25% |

  The VETO role is no longer harmful but added no useful selectivity on this holdout. Its only loss came from one
  snapshot-id miscopy at an entry point.
- **72h robustness (frozen F2, 3,522 windows):** 31.3% positive, 32.0% negative, 36.7% no trade, median ≈ 0. Treat
  LIVE as small expected edge with capped loss, not a profit forecast.
- **Natural frequency:** about 0.23 entries/day. If both symbols have a candidate now, the next 4h boundary has one
  with probability 0.98.
- **Safety-boundary review:** PASS, no BLOCKER or MAJOR findings. Three MINORs fixed in `b0dfe0e`; two documented
  (the evidence bundle is not bound to the config hash; the news item is structural).
- **SHADOW rehearsal `sh-f2-veto-2`** (15 min, VETO config): warm-up OK, 0 errors, zero-write PASS, published under
  `runs/published/sh-f2-veto-2`. There was no 4h boundary inside the window, so no model decision was made.

## LIVE precheck

Full table: `reports/phase-05/LIVE_PRECHECK_TABLE.txt`. Level `READY_FOR_SHADOW`.

- **PASS:**
  - strategy buildable
  - exit TTL/cancel safety
  - warm-up path
  - precision/min-order metadata (public)
  - execution state / recovery
  - real-model QA
  - AI role eval (role/prompt sha/resolved model)
  - observability evidence (frozen-replay bundle verified offline, REAL WRITE=0)
  - news disabled for trading
- **BLOCKED (owner inputs only):**
  - official trading window
  - competition end/cutoff
  - U04 autonomy answer
  - market eligibility confirmation
  - read-only account preflight: freshness, actual fee, TMN balance, open orders/assets
  - owner limit approval

## Launch steps (owner)

1. Fill `config/live-f2.toml`: `[trading_window]`, `[competition].end_utc` and `live_autonomous_enabled` (only after
   U04). Review the limits and set `approved_by_owner = true`.
2. Set the challenge sub-account key (no withdrawal permission) on the run host only. Run the read-only account
   preflight and write `AccountEvidence`. Write `OwnerAttestations`.
3. Run the precheck:
   ```bash
   uv run wallex-agent readiness --config config/live-f2.toml --attestations OWNER.toml --account ACCOUNT.json --ai-qa reports/phase-05/ai/f2-alignment/AI_QA_SMOKE.json --ai-eval reports/phase-05/ai/f2-alignment/AI_EVAL_EVIDENCE.json --evidence var/precheck/evidence/frozen-replay --public-metadata --format table
   ```
   It must report `READY_FOR_LIVE_PRECHECK`. Then run `uv run wallex-agent preflight --config config/live-f2.toml`.
4. Tag a clean commit `live-YYYYMMDD-N` and start LIVE from that tag. During the challenge: no hot edits; a change
   means a new tag with the reason journaled.

## Known limitations (not tuned, by rule)

- The model can miscopy the snapshot id (0.5% on the holdout). This fails closed to WAIT. A future fix would bind
  the snapshot id in code, and it needs a fresh evaluation.
- `NINEROUTER_MODEL` must stay unset or equal `cx/gpt-6.1-sol` on the run host. The gate blocks otherwise.
