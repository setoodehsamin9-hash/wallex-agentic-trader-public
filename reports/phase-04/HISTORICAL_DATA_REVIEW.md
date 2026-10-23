# Historical pack review

Fresh read-only reviewer: `gpt-6.1-sol`, reasoning `max`, per owner instruction. Base `d31e60f`; staged final tree
reviewed before the single final gate. No network or checkout/index mutation by reviewer.

Result: 0 Critical, 3 Important, 0 Minor. All Important findings fixed in one RED→GREEN pass:

| Finding | Correction | Reproducer |
|---|---|---|
| Partial cache overwrites retained capture; stale receipt survives | Fail before HTTP if one cache member is missing; reject changed receipt within a session | `test_partial_cache_is_preserved_without_silent_refetch`, `test_conflicting_cached_receipt_cannot_leave_stale_in_memory_provenance` |
| Coarser bars can be labelled minute data and leak across split close time | Refuse non-empty native chunks with unverified/mismatched interval; keep shifted daily evidence BLOCKED | `test_coarser_or_unverified_chunk_never_becomes_executable_minute_data` |
| Second build with the same archive loses provenance | Track receipts per access and scope them per build/dataset | `test_pack_offline_regeneration_reproduces_manifest_and_views` (same-archive third build) |

Focused market suite after fixes: **62 passed**. Full gate results are in `HISTORICAL_DATA_VERIFICATION.json`.

Decisions and limits carried forward:

- Invalid OHLCV rows are quarantined without repair; cost: less coverage and potentially non-random missingness.
- Daily off-grid rows are quarantined without shifting timestamps; cost: possibly valid venue-day bars are omitted;
  affected series remain BLOCKED until their semantics are authoritatively established.
- Review uses the staged tree before the one final gate, rather than an unverified intermediate commit; cost: no
  intermediate commit history, so the reviewer assessed the complete diff and pinned base.
- Profitability, final market/fee/parameter choices are deliberately unassessed; downstream experiment owners must decide.
- Economic volume units and shifted daily closes remain unverified; volume-sensitive/daily claims need further evidence.
- Root performs the reserved complete gate; the independent review alone does not certify tests, typing or scans.

No deferred Minor findings. No merge or changes to Claude's strategy branch.
