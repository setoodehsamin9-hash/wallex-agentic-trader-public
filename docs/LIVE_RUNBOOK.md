# LIVE activation runbook

**Phase 05 update (2026-10-23):** a LIVE composition exists (`runtime/live.py`, `wallex-agent run --env LIVE`).
It has been tested on fakes only. Start it only on the owner's machine, with `WALLEX_API_KEY` set in that shell and
nowhere else. It refuses to start, and sends nothing, unless all of these hold:
- the config passes LIVE enforcement, including the U04 gate;
- no unfinished run exists;
- the read-only account preflight passes;
- warm-up succeeds.

During the run a balance watch latches the kill switch when exchange TMN and the ledger diverge. A crashed LIVE run
must be inspected with `wallex-agent recover RUN_ID`. There is no LIVE resume yet, so a new session is refused while
it is unfinished.

The private response mappings are still PROVISIONAL. The first owner-run read-only preflight
(`wallex-agent preflight --config config/live-f2.toml --account-out ACCOUNT.json`) is their first real check: any
parse failure fails closed.

Owner sequence:
1. `preflight` (GET only)
2. `readiness ... --account ACCOUNT.json --attestations OWNER.toml`
3. tag `live-YYYYMMDD-N`
4. `run --env LIVE --config config/live-f2.toml`

The historical Phase 03 text follows.

Phase 03 had no enabled LIVE composition. The REST adapter and account parser are tested on fakes only; neither this
document nor simulation success authorizes private calls or orders.

## Required before any activation implementation

1. Obtain the official competition start/end in UTC (U01), the owner's exact recurring allowed hours and IANA
   timezone, and the organiser's U04 answer about autonomous deterministic order submission. Do not invent hours.
2. Obtain owner-approved limits and real fee/precision evidence for the challenge sub-account. Simulation configs
   have `approved_by_owner=false`; changing that flag alone establishes no readiness.
3. Restart recovery exists (ADR-003): `wallex-agent recover RUN_ID` (read-only) then resume through the recovery
   coordinator; reuse of a run without resume is still refused. For LIVE it still needs a verified private
   `query(client_order_id)` adapter and an account balance source; without them a resume stays `EXITS_ONLY`
   (no new entries, nothing resubmitted). Never bypass `BLOCKED` by starting a fresh run over real open orders.
4. Finalize the provisional private response mappings, cancellation/late-fill behavior, rate limiting and exit
   escalation using sanitized fixtures. The current canceled-order reconciliation checks are simulator evidence,
   not a production polling/rate-limit design.
5. Complete the model benchmark/ablation and shadow acceptance gates, then obtain the required Claude safety review.

## Private read-only verification: later, only on explicit owner instruction

The owner must specifically authorize the private spike in that session. Configure credentials on the run host,
outside the repository; never paste them into chat, config, prompts, issues or journals. Use only the challenge
sub-account key without withdrawal permission. Verify balances, spendable TMN, fee asset/rate, allowed markets,
precision, existing orders/positions/assets and reservations. A material mismatch fails preflight.

The current CLI `preflight` uses `account=None` and public metadata, so it cannot establish a real-account PASS.
It is not a private-account connection wizard. Its output must not be relabeled as account verification.

## Release and explicit activation

- Full deterministic tests, strict Pyright, secret scan, replay and failure drills must pass on the exact release.
- Source must be clean and committed; the owner explicitly authorizes creation/activation of `live-YYYYMMDD-N`.
  Record that commit/tag, dependency/config/prompt hashes, clock and model identity in the manifest.
- Implement LIVE wiring only behind completed preflight and all gates. Never add a bypass or manual buy/sell path.
- In APPROVAL_REQUIRED, the algorithm fixes the entire intent; the operator only approves/rejects it. Recheck risk
  on current time, market and balances. AUTONOMOUS additionally requires resolved U04 and deliberate host activation.
- Kill blocks new entries while exits/reconciliation continue. Model or human approval cannot override risk REJECT.
- Before window close/cutoff, cancel pending entries, liquidate holdings to TMN and reconcile final fills/dust. A lost
  response is UNKNOWN and looked up by client ID; never blindly resubmit/cancel or declare success from HTTP alone.

No private spike, real order, live tag or activation was performed during Codex takeover.
