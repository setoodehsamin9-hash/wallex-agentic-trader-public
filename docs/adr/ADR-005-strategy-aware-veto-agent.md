# ADR-005 — Strategy-aware decision agent and the bounded VETO role

Status: Accepted 2026-10-23 (Phase 05 final pre-LIVE). Supersedes the "agent is the final selector" default for F2 runs.

## Context

The decision agent (decision_v2) judged every candidate with C1-era breakout criteria: distance to a prior high, volume
confirmation, target edge after costs. It was shown every feature plus a deterministic regime label. F2 is a 4h
time-series-momentum strategy (EMA48 band, 5-ATR stop and chandelier trail, no take-profit, multi-day holding; the
regime filter mainly cuts drawdown). On the 417 real F2 candidates of V6+V7 GPT-6.1 Sol WAITed 84.6% of the time and
on all 18 deterministic entries (+228k TMN), citing "no breakout / low volume / too extended". In SHADOW it WAITed on
both natural F2 candidates for the same reasons. The model was evaluating a strategy that was not running.

## Options

A. Agent is the final selector, but strategy-aware: versioned `StrategyBrief`, only the strategy's evidence, a
   generic prompt "evaluate the supplied candidate under the CURRENT strategy's declared hypothesis" (decision_v3).
B. Deterministic F2 selects; the agent has a bounded risk-veto role (veto_v1). It may confirm the deterministic
   candidate or veto it only for permitted, evidenced reasons (DATA_QUALITY, REGIME_UNFAVORABLE, CONFLICTING_SIGNALS,
   LOW_LIQUIDITY, EXECUTION_STATE_UNSAFE). Any other WAIT is `VETO_UNSUPPORTED` and the deterministic candidate stands.
C. Keep decision_v2 (rejected: shown to block the strategy for reasons outside its hypothesis).

## Decision

Both A and B share the brief and the strategy-only rendering. The role is chosen by evidence, using a rule fixed
before any real call: hard gates, then (valid-entry preservation + invalid-entry veto)/2, with ties within 0.05
going to B (smaller authority). The evaluation used a fresh set from VALIDATION V1..V5 (67 cases; the 417 holdout
was not used). Result: both roles scored 0.989, so **B (VETO, veto_v1)** is frozen
(reports/phase-05/ai/f2-alignment/FROZEN.json). The 417 cases were then run once as a retrospective holdout.

Invariants kept:
- The model has no write tool and cannot name a symbol, price, quantity, stop, trail or limit.
- In the VETO role it cannot pick another candidate (UNKNOWN_REFERENCE → WAIT).
- Every model failure is still WAIT: no output, schema error, timeout, late answer, unknown reference, expired
  candidate. A `VETO_UNSUPPORTED` entry is re-checked at clock time like any VALID decision and goes through policy
  → risk → execution unchanged. `risk_posture` stays reduce-only.
- Replay passes the role, so recorded VETO-role runs replay deterministically.

## Consequences

- The AI stays causal: an evidenced veto blocks an entry that deterministic code would have taken. In the fresh
  set it vetoed 23/23 invalid cases. It cannot block valid F2 entries for reasons the strategy disowns.
- An overruled veto is not a model failure (`ValidationStatus.answered`). It is journaled as `AI_VETO_UNSUPPORTED`
  and is visible in observability.
- The fresh evaluation found one validator inconsistency: snapshot quality flags have no feature id to cite. The
  validator now accepts a DATA_QUALITY veto without feature ids when the snapshot carries quality flags. The prompt
  is unchanged.
- LIVE must run exactly the frozen role/prompt (readiness item `ai_role_eval` checks role, prompt version, prompt
  sha256 and model id). Changing it requires a new evaluation on a fresh set, never on the holdout.
