# Public historical data pack

Spec: owner's 2026-10-22 historical-data-pack request; AGENTS.md remains binding.
Base: d31e60f. Branch: phase-04/historical-data-pack. No strategy or safety-kernel changes.

## Global constraints

- Public GET only, no credentials, no private endpoints, no writes to Wallex.
- Collect every Muse shortlist market: BTCTMN, USDTTMN, ETHTMN, XAUTTMN, BNBTMN.
- Collection candidates are not confirmed challenge-eligible markets.
- Reuse Bar, parse_udf_history, aggregate, WallexPublicClient and write_dataset/load_dataset.
- Keep raw chunks and large exports in ignored data/raw; commit provenance, manifest, fixtures and commands.
- Never fill gaps or invent historical order books, trades, account fees or market limits.
- Closed UTC bars, Decimal OHLCV; chronological split boundaries supplied by the experiment owner.

## Task 1: Collect and verify the pack

1. Inspect public endpoint behavior and existing interfaces; record actual resolution and collection bounds.
2. RED: focused tests for quality, public-only bounded retries, checked resume, deterministic export and splits.
3. GREEN: minimal independent research-data tooling; preserve runtime behavior and dataset interface.
4. Collect public candles and metadata, retain hashed raw evidence, generate canonical datasets and quality evidence.
5. Write Persian report and English Claude handoff with exact paths and regeneration commands.
6. Fresh whole-branch review, fix important findings with RED→GREEN, then one final verification gate.
7. Final gate: pytest (repository commit contract), strict Pyright, secret scan, offline regenerate/hash smoke.
8. Commit and push this branch only; do not merge or change Claude's branch.

Expected: all checks exit zero; dataset hashes reproduce offline; every limitation appears in the handoff.

## Review Focus

Raw payload failures, duplicate conflicts across chunks, poisoned/missing cache, UTC alignment, incomplete bars,
resolution changes disguised as gaps, boundary/leading/trailing gaps, non-finite/non-positive OHLCV, strict
aggregation, metadata units/unknown limits, no silent refresh of collection provenance, and split close-time leakage.
