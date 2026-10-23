# News intelligence infrastructure handoff

Status: infrastructure/test gate PASS; public-source coverage and pre-capture history PARTIAL. No trading use is enabled.
Base d31e60f; branch `phase-04/news-intelligence-infra`.
Worktree: `/Users/saminsetoodeh/.codex/worktrees/news-intelligence-infra/Trade-Agent`.

## Interface

`wallex_agent.ports.NewsContextPort.context(decision_time, *, assets=()) -> NewsContext` is synchronous and read-only.
`CachedNewsProvider` implements it. Default `enabled=False`: no archive read, network, prompt alteration or new tool.
The existing DecisionContext, decision prompts, candidate generation, strategy/risk/execution/live adapters and
tool catalog are unchanged. Only an optional protocol was added to the shared ports module.

```python
from datetime import UTC, datetime
from pathlib import Path
from wallex_agent.news.context import CachedNewsProvider
from wallex_agent.news.store import NewsArchive

port = CachedNewsProvider(NewsArchive(Path("var/news-intelligence")))
disabled = port.context(datetime.now(UTC), assets=("BTC",))
research_port = CachedNewsProvider(NewsArchive(Path("var/news-intelligence")), enabled=True)
context = research_port.context(datetime.now(UTC), assets=("BTC",))
context_json = context.model_dump_json()
context_hash = context.context_id
```

For experiments pass the experiment's explicit UTC decision clock, not wall time. Accepted assets are BTC, ETH,
USDT, BNB and XAUT; these are text/entity aliases, **not challenge market eligibility**. Unknown asset requests fail.
Macro/regulatory stories are global only if they have no mapped asset. Do not reinterpret Bitcoin Cash, Bitcoin SV,
Ethereum Classic, Airbnb or unqualified gold as the supported assets.

## Capture, export and replay

Run from this worktree, Python 3.12 / existing uv.lock:

```bash
uv sync --frozen --python 3.12
uv run python scripts/news_intelligence.py fetch --archive var/news-intelligence \
  --sources cryptocurrency_cv federal_reserve bitcoin_core gdelt
uv run python scripts/news_intelligence.py context --archive var/news-intelligence \
  --enabled --as-of <timezone-aware-decision-time> --assets BTC
uv run python scripts/news_intelligence.py export --archive var/news-intelligence --out data/cache/news-export
uv run python scripts/news_intelligence.py context --archive data/cache/news-export \
  --enabled --as-of <same-decision-time> --assets BTC
```

`context` defaults disabled unless `--enabled` is deliberately passed. `context` and `export` are offline; only
`fetch` performs fixed registered public HTTPS GETs. Export destination must not already exist. A fetch returns
per-provider status, snapshot identity, item/rejection counts and error code; one failed provider does not masquerade
as complete coverage. No account, news API key, Wallex connection, model or scheduler is required.

Archive format: ignored `var/news-intelligence/raw/<payload-sha256>.bin` and
`snapshots/<canonical-capture-sha256>.json`. Every capture is immutable and includes provider/feed URL, original local
fetched UTC time, raw hash, source row count, optional provider total/has-more/limited values, versioned normalized items
and errors. Item IDs verify normalized content/provenance; full-text fingerprints precede display truncation.
An export preserves identical bytes/timestamps/hashes. Missing/corrupt raw or capture data fails closed; replay never
downloads it. Bound is 256 captures per archive; export/partition a suitable collection rather than silently skipping
history. Temporary partial files are not visible snapshots. Missing/uninitialized archives return ARCHIVE_UNAVAILABLE,
not a verified empty result; an intentionally empty archive has an explicit `snapshots/` directory. Leaf/ancestor
symlinks are rejected, including export destinations; use physical local paths. Secure random exclusive temporary
files replace predictable names. This is an owner-controlled archive, not a sandbox against concurrent malicious
filesystem changes. Keep raw news local unless its specific rights permit sharing.

The preserved public smoke archive is at
`/Users/saminsetoodeh/.codex/worktrees/news-intelligence-infra/Trade-Agent/var/news-intelligence/public-smoke`.
From any checkout containing this module, reproduce its exact cached view with:

```bash
uv run python scripts/news_intelligence.py context \
  --archive /Users/saminsetoodeh/.codex/worktrees/news-intelligence-infra/Trade-Agent/var/news-intelligence/public-smoke \
  --enabled --as-of 2026-10-22T20:03:34.636264+00:00
```

Expected context ID: `c23ee14eeb2b1ff73548926b10a3aa06412ab8e77376998480cbca36011a12f7`, three stories,
5,508 JSON characters. Capture/raw hashes are in NEWS_PUBLIC_SOURCE_SMOKE.json. `fetch` creates **new** observations
and new fetched-time hashes; it cannot recreate this historical observation if the ignored archive is lost. Use
immutable export/offline replay for identical evidence, and keep/export this worktree's archive before removing it.

## Contract and causal limits

NewsItem contains observation/content ID, provider, optional original publisher, canonical URL, supplied publication
UTC or null, local fetched UTC, bounded headline/summary, deterministic assets/unresolved entities, category, supplied
language/sentiment/importance, missing/truncation flags and raw provenance. Missing original publisher stays null;
an aggregator is not invented as the publisher. No LLM infers tickers, sentiment, importance or missing dates.

Apply `fetched_at <= decision_time` first, resolve the latest known revision **per provider/article URL**, then require
`published_at <= decision_time`, freshness and requested-asset relevance, and only then cluster syndication. Corrected
dates/assets cannot resurrect a superseded eligible version. Simultaneously conflicting versions are excluded with a
flag; homepage URLs cannot reliably identify revisions. Missing/future/stale publication is excluded; GDELT `seendate`
is provider observation and cannot substitute for publication.
Future captures also cannot affect provider status, source counts, chosen revision or context hash. This is an
observed-time archive starting with this task, not reliable 2017–2026 historical trading news. A current feed containing
an old publication cannot justify using it in an earlier backtest. No historical news history is fabricated.

Deduplication uses canonical article URL, exact long normalized headline on the same UTC publication date, and
full normalized-text hash when enough supplied summary exists. Display truncation never creates headline/content
equivalence. No embeddings/fuzzy ML or sentiment voting. Tracking parameters
are removed; semantic query parameters and repeated-parameter order are retained. All observations stay in captures.
Context groups eligible latest versions, reports distinct **known current** publishers, observation counts and a bounded
provenance sample. Earlier known publication-dated observation references are retained without resurrecting their text;
omitted provenance remains recoverable from snapshots.
Publisher labels do not establish independent reporting or accuracy; no trading confidence is derived from source count.

Default bounds: six stories, 12,000 serialized JSON characters, four provenance observations/story, 24h publication
age. Freshness expires exclusively at publication + max age; age/remaining-time fraction are time metadata, not a
signal/risk/confidence score. These are operational defaults, not selected strategy parameters. `ContextLimits`
is host-authored. Truncation/missing-date/stale/provider/archive errors are explicit. Text is always labelled
`UNTRUSTED_NEWS_DATA_NOT_INSTRUCTIONS`; never treat it as system instructions, commands or order parameters.

## Sources, permissions and tooling

See `research/phase-04/NEWS_SOURCE_POLICY.md`. Cryptocurrency.cv hosted API is permitted by its pinned README,
but its source/SDK is proprietary; none copied or installed. Federal Reserve RSS and Bitcoin Core RSS/Atom parser
support provide independent official sources. GDELT is optional and initially returned 429; seen-time-only stories
do not enter strict context. CoinDesk is registered **collection-denied** under current automated-access terms.
No full article crawling or AI/sentiment API is used. No numeric upstream quota or complete history is promised.
Each read uses a 10-second **socket-inactivity** timeout, not a total attempt deadline; slow-drip responses can take
longer. There are at most two attempts, and 429/other 4xx or malformed data stop that source. Fetch outside trading
cycles; no scheduler or wall-clock cancellation framework was added.

Postman CLI/tools, Pigment application tools and NightVision harness were unavailable in this session. There is
no served owned HTTP app/API to DAST; no third-party news or Wallex service is security-scanned. Do not claim a
Postman/NightVision scan ran or that this library is DAST-certified. Local synthetic HTTP/redirect/XML/body/provenance
tests, strict typing, replay and a public-source availability smoke are the verification path.

Final gate: full `uv run pytest` → 328 passed, one skipped, two opt-in deselected; Pyright strict → zero diagnostics;
project-pattern secret scan → PASS; offline deterministic export/replay → PASS with zero network. Focused news suite
67 passed. Two strict unknown-type diagnostics on a test lambda were corrected without production changes; the 19
related boundary tests and Pyright alone were rerun before resuming the remaining gate steps. No repeated full suite.
Public smoke: cryptocurrency.cv three records (provider advertised total 2,932, limited/has-more true), Fed 15,
Bitcoin Core 50 of 112 (explicit local cap), GDELT HTTP_429. Do not interpret this as complete crypto/global coverage.
Exact evidence: NEWS_INFRA_VERIFICATION.json, NEWS_OFFLINE_REPLAY_SMOKE.json, NEWS_PUBLIC_SOURCE_SMOKE.json.
One fresh gpt-6.1-sol/max review plus one TDD fix pass; NEWS_REVIEW_OUTCOME.md records findings, rulings and a deferred
minor JSON registry code-versus-service classification clarification. This label does not alter runtime permissions.

## Algorithm sources and integration later

`research/phase-04/ALGORITHM_SOURCE_REGISTRY.md` maps twelve pinned repositories/resources to relevant mechanisms,
licenses, maintenance evidence, reuse boundaries and limitations. LEAN/Jesse indicator references, Hummingbot/Nautilus
execution concepts, Qlib research workflow and FinNLP/FinGPT data/NLP examples are references, not strategy choices.
GPL/LGPL/unclear licenses, model weights, datasets and provider terms require their own review before future reuse.

After the owner chooses to integrate, run **in Claude's own working branch**, not in this infrastructure worktree:

```bash
git fetch origin phase-04/news-intelligence-infra
git cherry-pick FETCH_HEAD
uv run pytest
uv run pyright
```

The final owner message supplies the immutable pushed commit SHA for an explicit cherry-pick instead of FETCH_HEAD.
Preserve both sides of concurrent ADR/decision-log/ports edits if conflicts arise. Keep news disabled while integrating.
Claude still owns whether/how news affects WAIT, regime, candidate confidence or risk reduction, and any decision-log
schema/prompt wiring. No BUY/SELL rule, strategy parameter, risk limit or execution behavior is implemented here.
