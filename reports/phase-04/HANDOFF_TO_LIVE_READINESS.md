# Handoff to LIVE readiness (after Phase 04 strategy round 2)

Date: 2026-10-23 · Branch `phase-04/strategy-round2` (from `phase-04/strategy-lab` @ `9ff1711` + cherry-picked
data pack `a1621fd` → `b5b1bf0` and news infra `60952c4` → `59fbf5c`; **not merged to `main`**).
Evidence: `reports/phase-04/STRATEGY_ROUND2_FA.md`, `reports/phase-04/round2/*` (pre-registration, data profile,
economics, gate/TRAIN/VAL/G10/TEST, stats, leak check), kernel tests `tests/strategies/test_round2_f2.py`.

## 1. Strategy status

**Candidate:** `F2_tsm` f2-0.1.0 — 4h time-series momentum: enter when close >= EMA48 + 0.5 ATR14 and close >
close 48 bars ago, regime-r2 TREND_UP/RANGE only; chandelier stop 5 ATR (ratchet-only), exit when close < EMA48 at
a 4h close, 168 h cap. Config: `config/experiments/phase04r2/base-4h-f2.toml` (simulation placeholder limits).
Gates: G1–G9 (research simulator, VALIDATION) and G10 (kernel via Experiment Harness, 7/7 slices same sign, +471,210
TMN pooled at base) PASS; the single TEST read (2026-10-13..10-22) is KEEP but tiny: research +0.41% / kernel +9,949 TMN,
2 trades, both closed by the end-of-period exit. It is a **SHADOW candidate, not a LIVE-approved strategy**, until
every blocker below is closed by the owner.

Facts that shape LIVE use (VALIDATION 2025-03..2026-10-13):
- Positive expectancy per trade (+135 bps net mean, 95% CI +45..+230) but **right-skewed**: median trade −94 bps,
  47% winners; profits come from a few multi-day trends.
- **Multi-day holding is intrinsic** (median hold ~3 days, cap 7 days). Any nightly flat rule or a short challenge
  window truncates the winners: 72 h episodes were positive only ~29% of the time (flat ~35%), mean ≈ +0.2% at 100%
  notional. Expected 3-day P&L at kernel sizing is small relative to its variance.
- Kernel sizing (0.5% equity at the 5-ATR stop) yields ~8% notional per position; returns scale with the
  owner-approved risk budget, and so does drawdown.

## 2. Blocking items before any LIVE tag

1. **Trading-window hours (AGENTS 9c, U01).** F2 holds overnight and for days. If positions must be flat each night or
   only a few hours are tradable, F2 must be re-validated under that schedule (a different hypothesis) or not used.
2. **Real account fee (U06).** All results assume 0.25%/side + 5 bps (adverse 0.35% + 10 bps also positive on VAL).
   Read `GET /v1/account/fee` (owner-run, read-only) and re-run G10/TEST costs if materially different.
3. **Market eligibility (U-series).** BTCTMN/ETHTMN only were the decision universe; challenge eligibility unconfirmed.
4. **SHADOW/LIVE history bootstrap.** The live feed keeps ~2 days of bars; F2 needs >= 48 + 14 bars of 4h and the
   regime filter ~1,140 bars (~190 days). Without a deterministic, public read-only 1h history bootstrap (aggregated
   to complete 4h buckets) F2 fails closed (no candidates) in SHADOW/LIVE. Must be built and tested.
5. **Resting non-stop sells in LIVE (reviewer MAJOR).** A LIMIT EXIT_SIGNAL/EXIT_TIME sell that misses on a fast move
   stays open without expiry/cancel; while it rests the coordinator skips that position's stop and end-of-run
   flatten. Pre-existing for time/AI exits; F2 makes non-stop exits common. Fix (expiry + cancel/re-price with
   reconciliation by client_order_id) and test before LIVE.
6. **GAP rule at 4h.** One incomplete 4h bucket (any missing minute) blocks entries for 49 decision bars (~8 days)
   under the contiguity check. Decide (owner + engineering) whether that is acceptable for a 3-day run, or define a
   documented tolerance; do not silently relax it.
7. **Owner-approved `config/limits.toml`** (no nulls), official cutoff, U04 for AUTONOMOUS, SHADOW gate (zero real
   writes, zero duplicate/overspend in failure fixtures) — unchanged from Phase 03 requirements.
8. **Restart detail.** The trail's high water is not persisted (falls back to the approved stop after a restart) and
   a pending `signal_exit` flag is re-derived only at the next 4h close. Safe, but document it in the run guide.

## 3. Non-blocking follow-ups

- Approval bundles now show the trail; operators must be told it only tightens.
- News stays disabled; forward A/B/C plan in `reports/phase-04/NEWS_FORWARD_TEST_PLAN_FA.md`. Wiring the
  veto-only `news-overlay-0.1.0` into the runtime needs tests first, reviewer pass and a decision-log entry.
- Kernel and research simulator agree on VALIDATION (same trade count and slice signs; see `round2/G10.md`).
- BNB/XAUT were positive for F2 on VALIDATION but negative on TRAIN and are thin markets (81% / 69% zero-volume
  minutes): do not add them without their own cost/slippage evidence.

## 4. Verification at hand-off

Final gate results: `reports/phase-04/round2/VERIFICATION_R2.json` (full pytest, Pyright strict, secret scan,
deterministic experiment re-run hash check, replay smoke).
