# Phase 04 strategy round 2 — pre-registration (committed BEFORE any family result)

Date: 2026-10-23 · Branch `phase-04/strategy-round2` (from `phase-04/strategy-lab` @ `9ff1711` + cherry-picked
`a1621fd` data pack, `60952c4` news infra). Owner of all profitability design decisions: Claude.

Evidence available when this file was written (and nothing else):
- `DATA_PROFILE.json`: data validation (hashes vs committed pack manifest, gaps, illiquidity markers, 1h vs 1m cross-check).
- `ECONOMICS_TRAIN.json` part A: TRAIN-only unconditional volatility/cost, forward moves, variance ratios, regime
  frequencies and regime-conditional forward returns. No strategy, entry rule or exit rule has been evaluated.
- Phase 04 round 1/2 conclusions (C2 v0.2 = leading hypothesis; edge ≈ drift; 15m/1h too costly).

## 1. Data actually used

| Use | Dataset (pack, ignored, hash-verified vs `data/historical/MANIFEST.json`) | Range |
|---|---|---|
| research simulator (all roles) | native `1h` BTCTMN, ETHTMN, BNBTMN, XAUTTMN (XAUT from 2024-08-06) | 2022-10-22 .. 2026-10-13 |
| 4h decision bars / regime | aggregated from native 1h, complete aligned 4-hour buckets only | same |
| kernel harness (VAL-B only) | native `1m` BTCTMN, ETHTMN (existing kernel input) | 2026-07-24 .. 2026-10-13 |
| decomposition only | `1h` USDTTMN | TRAIN |

NOT used: the four BLOCKED native daily series (loader refuses them), XAUT daily, derived `15m`, anything at or after
2026-10-13 (loader cut + assertion). Daily statistics, where shown, are aggregated from complete 1h buckets.

## 2. Periods (UTC, chronological, disjoint; each slice starts flat and is flattened at its end)

| Role | Slices |
|---|---|
| warm-up only | 2022-10-22 .. 2023-02-01 (regime ranks need 90+ days) |
| TRAIN | T1 2023-02-01..07-01 · T2 07-01..12-01 · T3 2023-12-01..2024-05-01 · T4 05-01..10-01 · T5 2024-10-01..2025-03-01 |
| VAL-A (unseen) | V1 2025-03-01..08-01 · V2 08-01..2026-01-01 · V3 2026-01-01..06-01 · V4 06-01..07-24 |
| VAL-B (minute-backed) | V5 2026-07-24..08-20 · V6 08-20..09-16 · V7 09-16..10-13 — disclosure: 09-07..10-13 was the Phase 04 dev window |
| TEST (locked) | X 2026-10-13..10-22 — read only by a configuration that passed every gate below |

## 3. Costs (per side; account fee still unread, U06)

base 0.25% + 5 bps slip (60 bps RT, decision) · adverse 0.35% + 10 bps (90 bps RT, robustness gate) ·
low 0.20% + 5 bps (50 bps RT, information only, never used to select). Spread is not modelled (no historical book).

## 4. Deterministic regime layer `regime-r2-0.1.0` (fixed; not searched)

On CLOSED 4h bars: ER = Kaufman efficiency ratio over 42 bars (7 d); ER rank and ATR(14)/close rank vs the trailing
1080 bars (180 d, causal). Priority: SHOCK (vol rank >= 0.95) > QUIET (vol rank <= 0.20) > TREND_UP / TREND_DOWN
(ER rank >= 0.5, sign of the 7-day return) > RANGE; UNKNOWN during warm-up. **Regime filter ON = entries allowed
only when the latest closed 4h label is TREND_UP or RANGE** (avoid down-trends, low-volatility noise and shocks).
No LLM, no news. Activity (24h volume rank) is reported diagnostically only (volume unit unverified).

## 5. Families (4 max) — written before results

Common to F1–F3: decision on a closed bar of timeframe `tf`; entry LIMIT close +20 bps filled at the next 1h open;
initial stop = entry ref − m × ATR14(tf); **chandelier trail** stop = max(stop, highest high since entry − m × ATR at
entry), never lowered (concept from Hummingbot's ratchet trailing barrier, re-implemented, ATR-scaled); time cap 168 h;
long-only spot; one position per symbol. Every F1–F3 configuration **requires overnight / multi-day holding**.

| Family | Hypothesis / regime | Entry | Exit | Expected frequency | Why move can exceed cost | Grid (2^4 = 16 each) |
|---|---|---|---|---|---|---|
| **F1 DCB** Donchian breakout (Jesse `donchian`, LEAN `DonchianChannel`; Turtle concept) | range breakouts start multi-day trends that a trailing exit can ride; trend/range regimes | close > highest high of the PRIOR N bars | chandelier / cap | 1h: ~4–8 / month / symbol; 4h: ~1–3 | trend legs ≫ 1 ATR(4h) ≈ 130 bps; cost/ATR(4h) = 0.47 | tf {1h,4h} × N {24,72} × m {3,5} × regime {off,on} |
| **F2 TSM** time-series momentum with hysteresis (Jesse/LEAN EMA; Freqtrade MultiMa = reference only) | trend persistence (TRAIN: BTC/ETH TREND_UP fwd 48h ≫ TREND_DOWN) | close >= EMA(L) + 0.5 ATR and close > close[-L] | close < EMA(L) at a decision close, chandelier, cap | 1h: ~3–6; 4h: ~1–2 | holds through the trend; exit only on trend loss → few round trips | tf {1h,4h} × L {48,168} × m {3,5} × regime {off,on} |
| **F3 VBO** volatility squeeze → expansion (Bollinger; Jesse `bollinger_bands_width`, LEAN `BollingerBands`) | low-vol compression precedes directional expansion | min over last 6 bars of rank(BBW(24, 2σ)) vs 180 bars <= q, then close > upper band | chandelier / cap | 1h: ~2–5; 4h: ~1 | expansion moves are multi-ATR; entry after compression avoids QUIET noise | tf {1h,4h} × q {0.2,0.4} × m {3,5} × regime {off,on} |
| **F4 MRR** range mean reversion (Bollinger dip; economics-gated) | in RANGE regime, closes below the lower band revert to the mean | regime == RANGE and close < lower band (24, 2σ) | close >= middle band, stop 3 ATR, cap 48 h | ~2–4 | only if reversion to the mean exceeds 1.5x cost | gate config only; if it passes: tf {1h,4h} × cap {24h,48h} × m {3,5} |

Economics gate (BEFORE any grid): gate configs F1 (4h, N24, m3, off), F2 (4h, L48, m3, off), F3 (4h, q0.2, m3, off),
F4 (4h, cap 48h, m3) on TRAIN BTC+ETH pooled at base: **gross move per trade before fees and slippage must be >= 1.5 x
the 60 bps round trip (90 bps) with >= 20 trades**, else the family is REJECTED without running its grid.

## 6. Controls (mandatory)

- **RAND-X**: Bernoulli entries over every decision bar, rate calibrated to the strategy's trade count, with the
  strategy's exact exits; 30 seeds on the TRAIN grid, 300 seeds for frozen configurations on VALIDATION.
- **RAND-R** (regime-on configurations): the same, but entries only where the regime filter allows → does the entry
  signal add anything beyond the regime?
- **Buy & hold** BTC+ETH equal weight per slice, one round trip at the scenario cost.
- **C2 v0.2 baseline** (1h, close >= SMA24 + 0.5 ATR and 24h return > 0, fixed stop 3 ATR, max hold 48 h) and its
  CTRL v0.2 (gate off) re-expressed in the research simulator; plus exposure x B&H as a beta-equivalent reference.
- **Simulator calibration**: C2 v0.2 and CTRL v0.2 through the Experiment Harness (Decimal kernel, 1m data) on VAL-B
  vs the research simulator on the same slices (trade count, per-trade net bps). Kernel sizing differs (risk-based),
  so TMN totals are not compared.

## 7. TRAIN selection (per family; base cost; BTC+ETH portfolio pooled over T1–T5)

S1 pooled net > 0 · S2 >= 3/5 slices positive · S3 >= 40 trades · S4 pooled net > RAND-X median (30 seeds) ·
S5 mean pooled net of the ±1-step neighbours (each single-dimension change) > 0. Freeze at most ONE per family:
largest (pooled net − RAND-X median), ties → fewer trades → simpler (regime off, 4h). Its regime-flipped twin is also
run on VALIDATION for the regime comparison (a candidate only if it passes on its own). Nothing passes → family stops.

## 8. VALIDATION acceptance (frozen configs; VAL-A + VAL-B, 7 slices; BTC+ETH)

G1 pooled net > 0 at base · G2 pooled net >= 0 at adverse · G3 >= 20 trades · G4 >= 4/7 slices positive and worst
slice > −8% · G5 worst-slice MaxDD <= 20% and <= worst-slice B&H MaxDD · **G6 beats RAND-X: one-sided p <= 0.10
(300 seeds) at base, and net above the RAND-X median at adverse** · G7 vs B&H: net >= B&H net, or net/MaxDD >= B&H
net/MaxDD · G8 mean VALIDATION net of the ±1-step neighbours > 0 · G9 net > 0 on BTC and on ETH separately (BNB/XAUT
reported as breadth information; thin markets: 81% / 69% zero-volume minutes) · G10 kernel confirmation: the frozen
config re-expressed in the kernel (implemented only if G1–G9 pass) has the same sign on VAL-B through the harness.

**LIVE-candidate status requires G1–G10, then TEST once** (research sim + harness, base and adverse, with RAND-X and
B&H): KEEP if TEST net > 0 at base, >= 0 at adverse and above the RAND-X median. TEST (9 days) is a sanity check, not
proof. If nothing passes G1–G10, TEST is NOT read.

## 9. Pre-registered comparisons (information, not gates)

Regime ON vs OFF (same other parameters) on TRAIN and VALIDATION; 1h vs 4h; per-symbol; exit-reason mix; 3-day
episodes on VALIDATION (non-overlapping, start flat, flatten at end: share positive, mean, worst) vs RAND-X and B&H.

## 10. Revision and stop rule

At most ONE evidence-driven revision, written here as an addendum BEFORE it runs, evaluated with the same splits and
gates. Then stop: either a candidate passes, or the families were tested honestly and failed. Multiple-testing note:
4 economics gates + up to 48 grid configs + 1 revision; selection bias is controlled by unseen VALIDATION, random-entry
p-values and neighbour stability, not removed.

## 11. Addendum — G10 kernel confirmation (written after VALIDATION G1–G9, BEFORE any kernel/harness result)

Outcome so far (`VAL.md`): only **F2|4h|EMA48|m5|regime-on** passes G1–G9 in the research simulator; F1 frozen,
F1 twin and F2 twin fail G5 (worst-slice drawdown > 20%). No revision is used.

G10 as executed (deviation, stated in advance): the regime filter needs ~187 days of 4h history, which the 1m dataset
(from 2026-07-24) cannot supply, so the frozen configuration is confirmed through the Experiment Harness on the
**native 1h pack dataset** (the kernel now honours the dataset base resolution; exits then run per hour, entries fill
at the next hour open with the kernel's own TTL rule) over all seven VALIDATION slices. Minute-level fidelity is
checked separately with the regime-off twin on the 1m dataset over VAL-B (V5 has only partial EMA warm-up), plus the
pre-registered C2 v0.2 calibration on 1m. Kernel sizing is the risk engine's (0.5% equity at the initial stop, max 2
positions, simulation placeholder limits), not 100% notional, so TMN totals are not comparable with the simulator.

G10 PASS rule: kernel pooled net > 0 at base over VAL-A+VAL-B **and** >= 5 of 7 slices with the same sign as the
research simulator, with no kill-switch latch or unresolved order. Known differences (not failures): signal exits
fill one base bar later than in the simulator; GAP-flagged cycles (missing 4h buckets) skip entries; a 1h entry may
fill on the second bar inside the kernel TTL.
