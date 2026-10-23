# News infrastructure review outcome

Base: d31e60f. One fresh whole-branch reviewer: `gpt-6.1-sol`, reasoning `max`.
The reviewer inspected 20 staged files and verified 23 locally preserved pinned license/README hashes.
No Critical finding. One consolidated author TDD fix pass; no second reviewer or repeated broad gate.

## Findings and evidence

| Grade after owner-agent review | Finding | Resolution / regression |
|---|---|---|
| Important | Old eligible article survived a newer observed correction to date/asset | Resolve latest known per-provider/article revision before eligibility; `test_latest_known_correction_supersedes_old_eligible_version` covers old/missing/future dates and asset correction, including earlier-context invariance |
| Important | Predictable temp name and ancestor symlinks could overwrite/read outside the archive | Exclusive random temporary files and ancestor/leaf symlink rejection on write/read/export; predictable-temp and directory-indirection regressions |
| Important | Provider-advertised limited/pagination/total coverage was lost | Optional supplied metadata in capture, PROVIDER_LIMITED/PROVIDER_PAGINATION and PARTIAL_PROVIDER context flags; advertised-limit regression |
| Important | UTC overflow, extreme JSON numbers/depth escaped provider failure isolation | Safe UTC conversion, malformed metadata rejection, bounded JSON depth and exception containment; parametrized malformed-provider regressions |
| Important | Truncated display title created false duplicate equivalence | Hash full normalized headline/text before truncation; title and summary truncation regressions |
| Important (regraded from Minor) | Missing enabled archive silently looked verified empty | Missing/uninitialized archive is unavailable; explicitly initialized empty export remains valid; separate regressions |
| Material documentation accuracy (regraded) | Claimed 10s total attempt deadline was only a socket timeout | Correct all user-facing claims to socket inactivity; disclose no total wall-clock guarantee. No false wall-clock test or cancellation framework added |

The 15 behavioral regressions were observed RED before fixes, then the whole focused news suite passed:
`uv run pytest tests/news --tb=short` → **67 passed**. The explicitly initialized empty-export case is an additional
compatibility invariant, not a claimed initial failure. Existing bounded-list fixture now initializes its snapshots
directory explicitly under the clarified missing-archive contract. The first fix run exposed deep JSON being accepted
as a rejected row (CPython parsing depth is not a dependable application limit); the same fix pass added explicit
depth <=16 and reached GREEN. Simultaneously conflicting revisions are flagged/excluded rather than arbitrarily
choosing a version. Final full-suite/type/smoke evidence is in NEWS_INFRA_VERIFICATION.json.
The single final gate's full pytest passed (328 passed, one skipped, two opt-in deselected). Strict Pyright then found
two unknown-type diagnostics on a variadic test-double lambda, not production behavior. Replace it with an explicitly
typed equivalent; rerun that relevant test suite and Pyright only, then resume the remaining unrun gate steps.

## Rulings, in order

1. Require fetched time as well as publication time: present-day backfills/edits cannot certify historical availability.
   Cost: intentionally narrower history; publication-only backtests are not certified.
2. Use cryptocurrency.cv hosted API only: pinned proprietary LICENSE does not grant code/SDK reuse, while its pinned
   README permits the hosted API. Cost: service/terms may change; no guaranteed quota, history or publisher rights.
3. Deny CoinDesk collection and keep GDELT seen time distinct from publication. Cost: less coverage and no strict GDELT
   stories without publication evidence.
4. Review the full staged tree before the one final gate/commit: owner requests one gate, AGENTS requires full tests
   and typing before commits. Cost: no intermediate commit range; exact staged diff/spec/ledger supplied instead.
5. Regrade missing enabled archive as Important: path typos must not look verified empty. Cost: intentionally empty
   callers must explicitly initialize/export an empty archive.
6. Correct the total-deadline overclaim rather than building a new cancellation system. Cost: slow-drip wall time is
   unbounded; ingest outside trading cycles. Prose is reviewed against actual transport, not tautological text tests.
7. Upstream algorithm correctness/profitability/Wallex compatibility were not audited. Accept as reference evidence only,
   not reusable approval. Cost: Claude must perform a separate mathematical/data/cost/license check before adoption.
8. Availability/DNS/quotas/terms can change after observation. Accept one root-owned public smoke and explicit statuses,
   not uptime certification. Cost: failed sources require operational attention; there is no automatic daemon.
9. Publisher redistribution rights are not blanket legal grants. Keep raw news ignored/local and retain links/attribution.
   Cost: publishing content requires a separate applicable-rights check.
10. Publisher labels/aliases do not prove editorial independence or confidence. Report known labels only and leave
    ambiguous entities unresolved. Cost: no automatic reliability/sentiment/trading inference.
11. Future strategy integration/LIVE readiness are out of scope and remain disabled. Cost: Claude must explicitly design
    and verify any later prompt/context use; this branch cannot establish profitability or LIVE readiness.
12. Reviewer deferred broad tests/typing/tool smokes to the root's one final gate. Cost: no completion claim until that
    gate has actual results. Unavailable Postman/Pigment/NightVision and no owned DAST target remain explicit, not PASS.
13. The owner already selected commit/push on this exact branch, with no merge. Follow that choice instead of asking a
    redundant integration menu or creating an unrequested PR. Cost: any later PR/integration remains owner-controlled;
    preserve the worktree and local archive.

## Deferred minor

The machine-readable registry labels the cryptocurrency.cv repository `REFERENCE_ONLY` while the human registry and
OPEN_SOURCE_DECISIONS correctly distinguish the actually used hosted API from unused proprietary code. Clarify the
code-versus-service classification in that JSON later; no runtime or permission change depends on this label.
