# Historical data handoff

Data status: PARTIAL. Code and final verification: PASS. Four native daily series remain BLOCKED as documented below.

Base `d31e60f`; branch `phase-04/historical-data-pack`. No strategy, entry/exit, parameter, risk-threshold or final
market-selection decision is made here. Muse section 9 supplies five collection candidates: BTCTMN, USDTTMN,
ETHTMN, XAUTTMN, BNBTMN. All remain **challenge eligibility UNRESOLVED**. No private call, key or exchange write.

Local worktree: `/Users/saminsetoodeh/.codex/worktrees/historical-data-pack/Trade-Agent`.

Measured pack: 549 exact public responses (54,826,478 bytes), 25 series / 899,377 exported bars. Capture interval:
2026-10-22 17:02:27–17:19:45 UTC. All five minute series cover 2026-07-24 through 2026-10-22 exclusive (646,384
retained / 648,000 requested minute bars). Hourly history starts 2022-10-22 for BTC/USDT/ETH/BNB; XAUT begins
2024-08-06 16:00 UTC. Daily data reach 2018-11-27, but **BTC/USDT/ETH/BNB daily series are BLOCKED for precise
time-based experiments**: 440 off-grid opens are quarantined without shifting timestamps. Their actual historical
close semantics remain unverified. Four impossible OHLC rows are separately quarantined (XAUT minute 1, USDT
hourly 2, BNB hourly 1). Full coverage/hash table is in the Persian report; every excluded original row is in quality JSON.

## Paths and commands

Run from this worktree (all paths below are repository-relative):

```bash
uv sync --frozen --python 3.12
uv run python scripts/historical_data_pack.py
```

The command resumes SHA-256-checked public raw responses instead of re-fetching them. Two read workers share
a one-second request-start gate; each read has a 20-second timeout and at most three attempts with bounded backoff.
Permanent HTTP errors fail immediately; invalid payload structure or conflicting candles stop export. A broken cache hash
fails closed. Preserve the cache and investigate; do not silently replace it.
If only the response or only its receipt survives an interruption, collection also fails closed before HTTP I/O.
Restore the complete original pair from a trusted backup, or use a distinct `--archive` path and preserve a new
manifest; keep the old evidence. Never invent its original timestamp or silently overwrite the surviving member.

- Committed collection recipe: `data/historical/collection_plan.json`.
- Committed provenance/quality summary: `data/historical/MANIFEST.json`.
- Ignored exact HTTP bytes and per-response receipts: `data/raw/historical-pack/http/`.
- Ignored datasets: `data/raw/historical-pack/export/datasets/{1m,1h,1d,15m,4h}/`.
- Detailed quality evidence: `data/raw/historical-pack/export/quality/<interval>/<symbol>.json`.
- Current public metadata: `data/raw/historical-pack/export/metadata/` (also copied beside each dataset).
- Inspectable audit: `reports/phase-04/HISTORICAL_DATA_QUALITY.ipynb`.

Offline regeneration (zero network; original capture timestamps are retained):

```bash
uv run python scripts/historical_data_pack.py --offline \
  --out data/cache/historical-pack-rebuild --manifest data/cache/historical-pack-rebuild/MANIFEST.json
cmp data/historical/MANIFEST.json data/cache/historical-pack-rebuild/MANIFEST.json
```

Fresh online regeneration is reproducible in procedure, but public history and metadata may be revised. Its hashes
and collection timestamps can differ. Bit-for-bit regeneration requires this local raw archive. Large data are
ignored deliberately; another machine must fetch its own archive and preserve its new manifest, or receive this
archive through an owner-approved mechanism. Do not assume a clone contains the large datasets.

## Formats and compatibility

Each dataset uses the existing `manifest.json` plus `<SYMBOL>_<minutes>m.csv.gz`, columns `t,o,h,l,c,v`.
`t` is integer Unix UTC open time; money and volume load as Decimal; gzip has zero mtime. Each file has both a
compressed-byte SHA-256 and canonical bars SHA-256. The minute dataset keeps the existing runtime format.

```python
from pathlib import Path
from wallex_agent.market.dataset import load_dataset

minute = load_dataset(Path("data/raw/historical-pack/export/datasets/1m"))
hour = load_dataset(Path("data/raw/historical-pack/export/datasets/1h"), expected_resolution_seconds=3600)
```

The current BACKTEST / Experiment Harness core expects **minute** bars and performs its own aggregation. Use the
`1m` directory as `dataset_dir`. Hourly/daily/derived datasets are research inputs requiring an explicit consumer
interval; the default loader rejects them so they cannot silently enter the minute core. No trading behavior changed.
`metadata/package_183.json` is a public package snapshot, not proof of the challenge's final allowed universe.

Top manifest schema `1`: `plan_sha256`, collection bounds, metadata hashes and parsed public constraints,
`datasets[]` with directory/interval/manifest hash, `series[]` with source endpoint/tool, symbol, requested interval,
actual observed interval, requested/observed UTC bounds, row count, content hashes, quality flags and detailed
quality-file hash; `raw_responses[]` records URL, exact-byte hash, size, file and original capture timestamp.
Paths inside `series` are relative to `output_directory`; raw filenames are relative to `archive_directory`.

## Interpretation and limits

- Only closed bars are retained. UTC start is inclusive and end is exclusive. No gap filling, interpolation,
  fabricated candles, historical trades or order-book history. Current snapshots cannot establish historical depth.
- Native `1`, `60`, `1D` are collected separately. A probe requesting `15` returned 60-second bars; `240` returned
  3600-second bars. Therefore `15m` derives from complete minute buckets and `4h` from complete hourly buckets,
  using the existing aggregator. Native and derived data are distinguished, never relabelled.
- Requests can be truncated or return `no_data`; the plan's requested horizon is not guaranteed coverage. Detailed
  reports count leading, internal and trailing gaps and retain per-chunk spacing histograms. A minimum spacing in
  a sparse chunk is an observation, not proof that a missing bar represents a changed source resolution.
  A non-empty native minute/hour chunk without verified requested spacing (including a singleton or uniformly
  coarser sparse chunk) stops export. This deliberately sacrifices coverage rather than assign an unverified close
  time. Ordinary gaps with observed native spacing are retained. Daily off-grid evidence has the BLOCKED exception below.
- Daily historical rows also contain UTC 01:00 / 23:00 opens and irregular spacings. The pack neither guesses their
  cause nor shifts them to midnight. It retains only grid-aligned rows, records 440 exclusions and marks affected
  daily series BLOCKED. Those files are reference evidence, not approved time-aligned backtest input. Raw responses
  remain available if the daily interval/close semantics can later be established from an authoritative source.
- Non-finite/non-positive prices, negative volume and bad OHLC geometry are individually quarantined: exact raw
  responses, rejected values and reasons remain in evidence; the bar is omitted without price repair, causing an
  explicit gap and PARTIAL / INVALID_SOURCE_BAR_QUARANTINED status. The original strict parser still rejects them.
  Invalid epoch timestamps, malformed structure and conflicting duplicates stop export. Identical duplicates and raw ordering
  defects remain visible in evidence before normalization. Missingness after quarantine may bias an experiment.
- Zero-volume bars and large close changes are retained. The 20% discontinuity diagnostic only requests inspection;
  it is not a trading/risk rule or evidence of bad prices. Gaps can explain large multi-period changes.
- Public precision/minQty/minNotional are current snapshots. A reported zero is not a verified executable minimum;
  stepSize/tickSize are not assumed to be increments. Account-specific fee is null and remains experiment config.
- Volume retains Wallex's `v`; its economic unit is not independently verified. Do not infer historical fee tier,
  spread, fill probability, slippage, intrabar event order or challenge trading windows from candles.
- `split_bars` accepts owner-authored chronological UTC boundaries and includes a bar only when its entire interval
  fits. It rejects duplicates, disorder and invalid bars. No train/test ratio is selected. The tests prove boundary
  non-overlap and exclusion of future/straddling bars; feature warmup, embargo and position leakage remain harness work.
- Historical coverage and data quality alone establish neither profitability nor final market suitability.

## Verification

Final gate: **309 passed, 1 skipped, 2 deselected**; strict Pyright **0 errors / 0 warnings**; project-pattern secret
scan **PASS**; offline regeneration **0 HTTP calls**, identical complete manifest, dataset and quality hashes;
all notebook cells and chronological boundary assertions passed. See `HISTORICAL_DATA_VERIFICATION.json`.
Independent `gpt-6.1-sol / max` review found three Important defects; all fixed in one RED→GREEN pass, with no
Critical or deferred Minor findings. See `HISTORICAL_DATA_REVIEW.md`. Dataset limitations remain unresolved.

Measured coverage and the complete hash table are in `HISTORICAL_DATA_PACK_FA.md`. Delivery branch:
`phase-04/historical-data-pack`, based exactly on `d31e60f`. The final owner message provides the pushed commit SHA.
No merges are performed. Keep this worktree: the ignored raw archive is part of the local handoff.
