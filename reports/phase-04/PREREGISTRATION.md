# Phase 04 pre-registration — strategy round 1 (written and committed BEFORE any grid result)

Date: 2026-10-22 · Branch `phase-04/strategy-lab` · Owner of all design decisions: Claude (lead strategy architect).
Evidence used so far: `reports/phase-04/diagnosis/DIAGNOSIS.json` (development window only, 2026-09-07..10-13),
Phase 03 C1/B0/AI results, Muse market/strategy/risk research (evidence, not specification).
Only one probe trial (C2 c2-0.1.0, stop 2, hold 12h, TRAIN slice t1, base cost: 9 trades, -3.42%) was run before
this file, to time the harness and check fill mechanics. It is disclosed here and is part of the grid below.

## 1. Diagnosis that motivates the design (dev window only)

- Round trip at the assumed cost (fee 0.25%/side + 5 bps slippage/side) = 60 bps, before spread.
  15m median ATR = 28 (BTC) / 33 (ETH) bps -> cost is ~2x a 15m bar. 1h ATR ~62/75 bps (cost ~1x). 4h ~131/174 bps.
- 15m returns mean-revert (lag-1 autocorr -0.09/-0.10; variance ratio VR(16) 0.71/0.69); 1h VR(16) 0.81/0.75;
  4h VR(16) 1.16/1.07. Short-horizon breakouts fight the market.
- C1 15m breakouts had forward returns no better than random entry; shipped geometry replay: TP 30%, stop 64%,
  gross -4 bps/trade, net -64 bps/trade; median favourable excursion 1.4 ATR vs 3 ATR target.
- Strong positive drift (buy & hold +16.9% BTC, +11.4% ETH in TMN). Any long result must beat a drift control.
- Mean reversion (1h Bollinger dip): gross +29 bps at 4h -> below cost -> rejected for this round.
- 4h bars: one 80-minute outage blocks ~30% of cycles under the existing GAP rule and samples are tiny -> rejected.
- No zero-fee TMN market in the metadata snapshot; no historical order book -> no order-book family.

## 2. Fixed for every configuration (not searched)

1h bars; symbols BTCTMN, ETHTMN (experimental candidates: challenge eligibility unconfirmed); long-only spot;
features atr_bars 14, trend_bars 24, volume_bars 20; decision on bar close, entry LIMIT (+20 bps cap) filled from
the next minute; exits run every minute (stop first); NO take-profit; candidate TTL 600 s; cost-awareness
`ATR_bps >= 1.0 x estimated round trip`; risk limits = shipped simulation placeholders (unchanged; each period resets
capital and the 3% total-loss budget); each period flattens before its end (cutoff config).

## 3. Candidate families (max 3) and grids

| Family | Hypothesis | Rules | Grid (base config) | n |
|---|---|---|---|---|
| **C1 c1-0.2.0** breakout-hold (revision of C1) | 1h breakouts held 12-24h showed gross forward returns above cost (+67..94 bps at 16h, small n); C1 failed from 15m cost/geometry, not necessarily from the breakout idea | close > prior L-bar high + 0.1 ATR and close > SMA24; stop = entry - k ATR; exit by stop or max hold | lookback_bars {24, 48} x stop_atr {2, 3} x max_holding_s {43200, 86400} | 8 |
| **C2 c2-0.1.0** trend-hold (time-series momentum) | multi-hour returns are not mean-reverting and an established up-trend preceded ~2x the random 12h forward return; low turnover keeps fee burden down | close >= e x ATR above SMA24 and 24h return > 0; stop = entry - k ATR; exit by stop or max hold | entry_trend_atr {0.5, 1.0} x stop_atr {2, 3} x max_holding_s {43200, 86400} | 8 |
| **CTRL ctrl-0.1.0** drift control (benchmark, not a candidate) | being long at random times with the same exits captures only drift | enter whenever flat (trend_gate = false) | stop_atr {3} x max_holding_s {43200, 86400} | 2 |

Total 18 configurations. AI may later select among these deterministic candidates; it cannot change any number.

## 4. Periods (UTC, chronological, disjoint, 1h-aligned; warm-up uses only earlier bars)

| Role | Slices |
|---|---|
| TRAIN | t1 09-10..09-17, t2 09-17..09-24, t3 09-24..10-01 |
| VALIDATION | v1 10-01..10-07, v2 10-07..10-13 |
| TEST (untouched) | x1 10-13..10-18, x2 10-18..10-22 |

TEST bars were not read by the diagnosis. Disclosure: Phase 03 evaluated C1 c1-0.1.0 (15m) on 10-13..10-22, so the
window is fresh for these new families but not pristine. TEST runs only for frozen survivors (section 6).

## 5. Costs (per side, ASSUMED until the account fee is read)

| id | fee | slippage | round trip | role |
|---|---|---|---|---|
| base | 0.25% | 5 bps | 60 bps | decision |
| adverse | 0.35% | 10 bps | 90 bps | robustness gate |
| low | 0.20% | 5 bps | 50 bps | information only (public API sample fee); never used to select |

## 6. Decision rules (committed now; applied to TRAIN+VALIDATION, base cost, pooled over 5 slices)

Hard REJECT if any: (R1) pooled net P&L <= 0; (R2) pooled closed trades < 30 -> INSUFFICIENT EVIDENCE;
(R3) net > 0 in fewer than 3 of 5 slices, or any slice return < -3%; (R4) pooled profit factor < 1.1;
(R5) fees > 50% of pooled pre-fee P&L (or pre-fee P&L <= 0); (R6) does not beat the CTRL with the same max_holding_s
on pooled net -> BETA ONLY; (R7) mean pooled net of its +-1-step grid neighbours <= 0 (isolated peak).
Robustness: a survivor must keep pooled net > 0 at the adverse cost, else FRAGILE (not frozen).
Freeze: at most one configuration per family (centre of the acceptable plateau, ranked by VALIDATION net / max
drawdown, then lower turnover, then simplicity), at most 2 in total. TEST: each frozen configuration once at base and
adverse cost, plus CTRL and buy & hold as benchmarks. KEEP only if TEST pooled net > 0 at base, >= 0 at adverse, and
above CTRL. If nothing survives R1-R7 + robustness, TEST is NOT run and stays untouched for Phase 05.

## 7. One revision round

At most one evidence-driven revision (new version id, written here as an addendum BEFORE running it), evaluated on
TRAIN+VALIDATION only under the same rules; its trials are added to the trial count. No further tuning.

## 8. Addendum — round 1 outcome and the single revision (written after round 1, BEFORE round 2 runs)

Round 1 (`reports/phase-04/experiments/ROUND1_EVALUATION.md`, 270 trials + 100 interrupted duplicate rows with
identical result hashes + 1 probe): **all 16 configurations REJECTED**. Best: C1 lookback 24 / 24h hold, pooled +17k
TMN, 19 trades, PF 1.07, fees 86% of pre-fee P&L, negative at adverse cost. Trade stats
(`TRADE_STATS_base.json`): fees ~50 bps/trade vs best mean price move +53-55 bps/trade; 24h holds ~2x the 12h move;
every 12h configuration loses; BTC positive / ETH negative in every 24h C1/C2 configuration.
Mechanism found: in C2, 8-12 of 13-24 time exits per configuration were followed by re-entry in the same symbol
within 2 hours (a round trip paid while the trend continued); C1 had 0.

Revision hypothesis (one mechanism): C2's gross edge is consumed by forced time exits inside persisting trends;
allowing up to 48h holding removes the exit/re-entry round trips. Stop fixed at 3 ATR (better than 2 ATR in all four
round-1 C2 pairs). Universe unchanged (BTC-only is recorded as an observation for Phase 05, not tested here, to keep
one change per revision). Caveat recorded in advance: holds > 12h are deployable only if overnight holding is
allowed inside the (still unknown) trading windows (AGENTS.md 9c).

| Experiment | Version | Grid | n |
|---|---|---|---|
| p04-r2-c2 | c2-0.2.0 (stop 3, max hold 48h) | entry_trend_atr {0.5, 1.0} | 2 |
| p04-r2-ctrl | ctrl-0.2.0 (gate off, stop 3, max hold 48h) | — | 1 |

Same splits, costs and rules R1-R7 + adverse robustness; R6 compares with ctrl-0.2.0; R7 neighbour = the other
entry_trend_atr value. If nothing survives, TEST stays untouched. Total recorded trials after round 2: 270 + 45 (+100
interrupted duplicates, +1 probe).
