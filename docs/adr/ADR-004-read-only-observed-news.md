# ADR-004 — Optional observed-time news context

Status: Accepted for this infrastructure branch, 2026-10-22; trading use remains disabled.

## Context

The owner requests replayable news infrastructure without changing Claude's trading design. Publication dates alone
cannot prove that a historical observation, revision or source attribution was available at a decision time.

## Options

1. Fetch news inside an Agent tool or strategy cycle: introduces network, prompt-injection and replay uncertainty.
2. Backfill by publication date alone: exposes present-day discovery/edits to past decisions.
3. Capture public feeds separately, freeze/hash observations, expose a bounded cached read-only port.

## Decision

Use option 3. `NewsContextPort` has only `context(decision_time, assets=...)`. `CachedNewsProvider` defaults disabled
and is not wired into existing decision prompts, strategies, policy, risk, execution or live adapters. No new model
tool or write capability is exposed. Ingestion is an explicit public-GET CLI action with local immutable storage.

First require local `fetched_at <= decision_time`, then resolve the latest known revision per provider/article URL,
then apply publication/freshness/asset filters before syndication clustering and context assembly. A correction to
an old/missing/future publication or another asset must not resurrect an obsolete version. Conflicting simultaneous
versions are explicitly excluded; publisher homepages are not reliable revision identities. Historical observation
references remain in bounded provenance without exposing superseded text. Missing publication is retained in storage
but excluded from decision context. GDELT index-seen time is not publication. Future captures cannot change historical
context, including provider status or provenance. Fingerprints cover full normalized text before display truncation.

Reuse existing frozen Contract/UTC/hash utilities and the Python standard library; no news/ML/trading framework,
provider SDK or new dependency. Preserve original raw hashes and all observations; only the context view is trimmed.

## Consequences

The library is safe to inspect/replay without changing current decisions. Today's historical discoveries are not
valid earlier backtest inputs. Context defaults (24h age window, six stories, 12,000 JSON characters, four provenance
observations/story; 50 rows/feed, 1 MiB/body, 256 snapshots/archive) are operational bounds, not tuned strategy/risk
parameters. The strategy owner must decide any later news effects, persistence in DecisionRecord and context limits.
An archive exceeding its bound requires an explicitly bounded export/partition. No automatic live activation occurs.
An enabled missing/uninitialized archive is unavailable, not verified empty. Ancestor/leaf symlinks are denied and
exclusive random temporary files prevent predictable-temp overwrite. The archive assumes a trusted local owner;
it is not a sandbox against concurrent hostile filesystem mutation. Transport has a 10s socket-inactivity timeout,
not a total wall-clock deadline; ingestion must remain outside trading cycles.
