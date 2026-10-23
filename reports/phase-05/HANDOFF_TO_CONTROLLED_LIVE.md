# Handoff to controlled LIVE (after Phase 05 live-readiness + forward SHADOW)

Date: 2026-10-23 · Branch `phase-05/live-readiness-shadow` (from `phase-04/strategy-round2` @ `751942e`; not merged to
`main`). Candidate unchanged: **F2_tsm f2-0.1.0** (no strategy hunting or tuning in this phase).
Gate: `uv run wallex-agent readiness --config config/shadow-f2.toml` → **READY_FOR_SHADOW; LIVE BLOCKED**.

## 1. What changed (kernel; tests first; safety-reviewed)

| Area | Change | Evidence |
|---|---|---|
| Warm-up | `market/warmup.py` `warmup-1`: hash-checked 1h cache/seed → public `udf/history` 1h only for the missing tail (7-day chunks) → live minutes. Closed, aligned, deduped (revisions counted), resolution-checked, ≤ now. SHADOW refuses to start (`WarmupUnavailable`) if history < required, > 1% missing, stale (> 3 h) or wrong resolution; report journaled (`warmup` ref, `WARMUP_INSUFFICIENT` on resume) | tests/market/test_warmup.py, tests/runtime/test_shadow_warmup.py; live: BTC 4,511 bars (4,481 pack seed + 30 public) |
| Data quality | `market/quality.py` `window-quality-1`, shared by BACKTEST `_flags` and SHADOW cycles; native 1h candles complete minute-feed gaps (`combine_hours`); non-OK statuses journaled `DATA_QUALITY` | tests/market/test_window_quality.py; reports/phase-05/GAP_EVIDENCE.json (25.9% → 0.6% blocked 4h decisions); G10 re-run: 6/7 slices identical, V7 4 → 6 trades (= simulator) |
| Exit safety | `execution.exit_order_ttl_s` (≤ 300 s, **required for LIVE**). Resting exit supervised: TTL cancel, immediate cancel on stop breach while a non-stop exit rests, CANCEL_PENDING persisted before the venue call, replacement only after a journaled second terminal lookup (`CANCEL_CONFIRMED`), lost cancel → UNKNOWN → RECONCILE_REQUIRED (no resubmit), retries spaced by TTL, no cancel while orders are not allowed, partial fills shrink the remainder, FILLED/REJECTED final, missing order record latches kill, rebuilt CANCEL_PENDING/unconfirmed CANCELED reproduced on replay, live venue `remember()`s adopted orders | tests/execution/test_exit_safety.py (11), tests/recovery/test_state_log.py, tests/execution/test_live_adapter.py; reviewer: 0 blocking, 2 MAJOR + 6 MINOR all fixed |
| Preflight | real fee must be ≤ the assumed fee | tests/runtime/test_readiness.py |
| Readiness gate | `runtime/readiness.py` + CLI `readiness`: SHADOW items (strategy buildable, exit TTL, warm-up path) and LIVE items (official window, cutoff, U04, eligibility, account preflight, owner limits, execution state, real-model QA if LLM) | tests/runtime/test_readiness.py |
| Config | `config/shadow-f2.toml` (frozen F2, simulation-labelled limits, exit TTL 60 s, seed = hash-pinned pack 1h, GPT-6.1 Sol via NineRouter, no trading window → provisional) | — |

## 2. Real model (NineRouter only)

Exact id from the router's own `GET /v1/models`: **`cx/gpt-6.1-sol`** (owned_by `cx`, tools). Responders verified
`ninerouter:gpt-6.1-sol` (no fallback). Key read from the owner-supplied path outside the repo; never printed/stored.
Results: `GPT61_NINEROUTER_EVAL_FA.md`, `ai/GPT61_NINEROUTER_EVAL.json`, `ai/GPT61_F2_CASES.json`.

## 3. Forward SHADOW

`sh-f2-p05` (05:35–08:12 UTC, `FORWARD_SHADOW_FA.md`): warm-up OK (4,511 1h bars/symbol: 4,481 pack seed + 30
public), 15 s polling, one 4h boundary (08:00): **natural F2 candidates for BTCTMN and ETHTMN; GPT-6.1 Sol WAITed on
both** (VALID, CONFLICTING_SIGNALS/WEAK_EVIDENCE, 14–15 s, 0 tools); 0 entries/trades; window quality OK; zero-write
proof PASS (`real_wallex_writes = 0`). Restart drill `sh-f2-drill`: SIGINT → resume READY, cache reused (1 public bar).
News A/B/C (`NEWS_ABC.json`): A 2 / B 2 / C 0 entries — **INCONCLUSIVE (n = 2)**; both vetoes came from one irrelevant
aggregator story labelled MACRO (Italian domestic politics): `news-overlay-0.1.0`'s global-MACRO trigger is too broad
(false positive). Rule unchanged (pre-registered); next version should restrict the global trigger to official sources
and be forward-tested again. News stays disabled and veto-only.

GPT-6.1 Sol on 417 real F2 candidates (V6+V7): 416 valid, 64 ENTER, WAIT 84.6%, 1 unknown reference (validator →
WAIT), pinned; **WAIT on all 18 decisions where the deterministic selector opened a trade (+228,155 TMN realized in the
kernel)**. Candidate-path fixture: GPT-6.1 Sol ENTER → EXECUTE → REDUCE → FILLED (SHADOW sim), replay identical.

## 4. Blockers before controlled LIVE (all owner-side unless marked)

1. Official trading window + competition end/cutoff (U01) → `[trading_window]`, `competition.end_utc` + attestation.
2. U04 autonomy answer (AUTONOMOUS allowed?) → attestation + deliberate `live_autonomous_enabled`.
3. Market eligibility of BTCTMN/ETHTMN confirmed.
4. Read-only account preflight with the challenge sub-account key (no withdrawal): fee ≤ 0.25%, spendable ≈ 5,000,000
   TMN, no locked TMN, no open orders/foreign assets. No key was available here → BLOCKED.
5. Owner-approved limits (`config/limits.toml`, no nulls, `approved_by_owner = true`) — checklist in LIVE_READINESS_FA.md.
6. Agent role decision (owner): the current agent (prompt `decision_v2`, C1-era breakout criteria) vetoes F2's
   validated entries (18/18 in QA, 2/2 natural candidates in SHADOW). Options: BASELINE selector for F2 entries with
   the agent advisory-only, APPROVAL_REQUIRED for QA, or a separate phase aligning the agent with F2's semantics
   (strategy description, not tuning on these samples) followed by a fresh evaluation. Not decided here.
7. Engineering before a `live-*` tag: LIVE composition is still disabled (`runtime/compose.py`); a fake-transport
   end-to-end drill of resting-exit TTL/cancel on `WallexLiveVenue` (ShadowVenue is IOC, so SHADOW cannot exercise
   it); confirm Wallex cancel semantics (late fills after CANCELED? cancelled orders retrievable?) from official docs.
8. Run LIVE only from a clean tagged commit after `preflight` passes on the run host.
