# News intelligence infrastructure

Spec: owner's 2026-10-22 news-infrastructure request; AGENTS.md binding. Base d31e60f.
Branch: phase-04/news-intelligence-infra. Work is independent of Claude's strategy branch.

## Global constraints

- Public GET news only, no Wallex dependency/key/private/write calls, no model calls or upstream code copying.
- No strategy/risk/execution/config behavior changes. Add only a read-only context port and disabled-by-default provider.
- Immutable versioned contracts, Decimal where needed, explicit UTC, no invented dates/sentiment/importance/entities.
- Public headlines/brief supplied summaries/URLs with attribution; raw captures local and ignored; no full article fetching.
- RSS/Atom plus replaceable JSON adapters, bounded bodies/requests/retries/items/context; no heavy news/ML framework.
- Replay requires both publication and local observation <= decision time; today's backfill is not point-in-time history.
- URL/headline/hash dedupe retains every provenance observation; deterministic aliases, ambiguous names unresolved.
- Registry is pinned evidence/reference only; concrete dependency/API decisions recorded separately, no strategy recommendations.

## Task 1: Implement and hand off

1. Verify provider availability, public usage terms and pinned repository licenses/activity; create compact evidence.
2. RED→GREEN: contracts/normalization, deterministic aliases and dedupe, freshness/bounded context/future filtering.
3. RED→GREEN: bounded read transport and malformed/provider-failure handling, immutable capture cache/export/offline replay.
4. RED→GREEN: disabled read-only port leaves existing decision behavior unchanged; no trading integration is enabled.
5. Write source/algorithm registry and Persian/English handoffs with exact commands and source restrictions.
6. One fresh whole-branch reviewer (gpt-6.1-sol, max or below); one TDD fix pass for Important/Critical findings.
7. One final gate: full pytest (shared port/commit contract), strict Pyright, secret scan, offline replay and public-source smoke.
8. Commit/push only this branch; keep worktree clean, never merge main or Claude's branch.

Expected: tests and Pyright exit zero; replay hashes are deterministic, no future items/context or write tool crosses the port.

## Review Focus

Same-article updates and publication dates that change; fetched-time leakage; missing/future/naive dates; duplicate source
counts; syndicated headline ambiguity; SSRF/redirects/XML entities/oversized payloads; raw-text prompt injection;
cache immutability/tamper/path traversal; partial provider failure; context bounds; source terms versus source-code license;
no hidden network on replay/default-disabled paths; no change to existing decision prompts or trading behavior.
