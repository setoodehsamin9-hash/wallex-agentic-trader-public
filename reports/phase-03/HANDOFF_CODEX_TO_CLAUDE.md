# Codex → Claude continuation handoff — Phase 03 PARTIAL

Date: 2026-10-22. Continue Phase 03. Do not restart from Phase 02 or treat the old Phase 04 handoff as acceptance.
Branch: **phase-03/trading-kernel**, pushed to origin; main not merged. No LIVE tag, real trade or private Wallex call.

## Checkpoint and commits

Recovered **fbac63e** with 197 passing tests, clean Pyright and secret scan. Only two owner-supplied Muse research
files were untracked; read and preserved unchanged. Existing Claude/earlier Codex implementation was retained.

| Commit | Scope |
|---|---|
| fbac63e | Inherited checkpoint; earlier financial fixes/handoff preserved |
| d22f670 | Bounded NineRouter/PydanticAI integration, native optional MLflow eval, tool audits/filter, SHADOW guard/status, tests |
| 3aabb72 | Disable MLflow telemetry before import; use synchronous native evaluation traces; failing-first regressions |

Implementation/test tip: **3aabb72**. A following commit contains reports/research/evidence only; obtain its hash
with git log -1 --format='%H %s'. Local runtime artifacts record the code/dirty flag actually used; no LIVE provenance is claimed.

## Safe/ordinary work completed by Codex

- Inspected actual authenticated NineRouter /v1/models: OpenAI-compatible; gemini-3.8-flash available and first QA model.
- Reused PydanticAI Agent, UsageLimits, run_sync timeout, OpenAIProvider-owned client lifecycle, FunctionModel,
  capture_run_messages and hooks. Timeout honored; SDK retries zero; model/base URL/settings remain configurable.
- Audited actual offered function schemas; captured successful, invalid, unavailable and interrupted tool attempts.
  Model thinking/message histories are not persisted. Tools remain SAFE_READ; candidate validation is unchanged.
- Used native MLflow 3.8.1 datasets, deterministic scorers, genai.evaluate and safe boundary traces, optional
  evaluation dependency group only. SQLAlchemy constrained below 2.1 after a reproduced compatibility failure.
  Local decorator-scorer registration is unsupported; source-controlled scorers are passed directly, not bypassed.
  phase trace_count is cumulative within that experiment; do not sum it as new per-phase traces.
- Persian CLI/status shows model latency/call/error counts; final CLI summary reads the enriched stored status.
- SHADOW rejects another environment before public I/O. Added a fake-model integrated write-attempt test:
  unavailable tool → WAIT, no orders, LIVE/private constructors trapped.
- Documented exact upstream licenses/commits in docs/OPEN_SOURCE_DECISIONS.md. Condor/Jesse reference only;
  no framework imports, copied code, numeric defaults or strategy tuning.

## Evidence and fresh verification

- Plain uv run pytest: **217 passed, 1 deselected** with the optional evaluation group installed.
  Strict uv run pyright: **0 errors, 0 warnings**. One upstream deprecation warning; no failing tests.
  Without the optional MLflow group, its integration test skips; deterministic trading tests still run.
- Final uv run --group evaluation pytest -m ninerouter: **1 passed, 217 deselected**, 201.11 seconds.
  Three complete suites are retained, not cherry-picked: preliminary13/15 structured outputs (loop defect);
  lifecycle-fixed15/15; final15/15. Final operational subset7/7 valid, candidate discipline100%, unknown refs0,
  provider errors/timeouts0, WAIT6/7, B0 disagreements6/7, p50=9669ms, sample p95=43973ms, ~17 requests.
  The7 cases use2 recorded historical contexts plus explicitly synthetic QA conflict/weak/stale/tool/write-temptation
  variants. The qa-held position is fictitious; these evaluation variants have no execution adapter.
- Read-tool limitation: final operational3 attempts/0 successful, all rejected extra reason arguments;
  all final phases4 attempts/0 successful. One internal read succeeded in the prior fixed suite after retry.
  Do not relax schema or describe this as reliable tool adherence.
- Three final fault probes (1s deadline, local refused port, unknown router model) all WAIT.
  Scripted malformed output, invented candidate, stale decision, exhausted budget and unavailable tool tests pass.
- Actual current-public Agent probe: valid WAIT,17709ms,2 requests, six offered read tools,0 writes/private tools.
  No MCP tool was called. Three tools were frozen internal reads and three were allowlisted Wallex public reads.
- Total ordinary real-model decision invocations across this continuation50; nine injected failures separate
  (three are local offline connection tests). See ai/EVAL_RUN_INDEX.json; these are not independent benchmark samples.
- Paired8h VALIDATION period, 2026-10-05 00:30–08:30 UTC, fixed before observing the result:
  B0:3 trades,-43964TMN,-0.8793%; AI:2 trades,-27884TMN,-0.5577%. AI WAITed1/3 opportunities.
  Both negative; small already-seen validation sample, no profitability or holdout-superiority claim.
  Socket-blocked replay matched64 complete validated decisions, trade economics, execution events and summary,
  zero missing responses; run-specific identities differ. Config copy preserves every financial/strategy value.
- Earlier long B0 validation remains -165497TMN/-3.3099%,1726 decisions/9 trades, unchanged.
- Public SHADOW: main900s strategy cadence2 cycles plus existing60s connectivity cadence6 cycles; no candidates,
  AI calls, risk rejects/reductions, orders, fills, execution errors or unresolved orders. Hypothetical P&L0 is
  uninformative. **Forward candidate → real AI → risk → simulated fill remains unproven**, requiring longer SHADOW.
- Runtime zero-write proof: public URL/method allowlist, trapped LIVE/private/account constructors, ShadowVenue;
  integrated fake write temptation also WAITed with zero orders. **REAL WALLEX WRITES = 0; private calls = 0.**
- Fresh scanner checked repository files, published runs and local MLflow/SQLite artifacts against patterns and
  the actual configured credential literal, with only two exact synthetic test exceptions. See CODEX_SECRET_SCAN.json.
  Policy matrix/tool exposure and whitespace checks passed.

## Sensitive areas NOT changed in this continuation

No modifications versus fbac63e to risk/, portfolio/, execution/, policy/, tools/catalog.py, production config
(including numerical thresholds), strategy/features, journal persistence, recovery or LIVE gates. No real write
adapter instantiated; no Wallex private credential consumed. No persistent restart/recovery work performed.

Earlier financial fixes from 3e5e4eb through ad018b6 still MUST be reviewed:
portfolio/ledger.py; execution/engine.py; risk/kill_switch.py; approval/service.py; journal/store.py;
runtime/engine.py; runtime/decision_wait.py; prior shadow/replay/export/assistant changes.
The prior transient non-green391fe1c commit and its separate ad018b6 type correction remain in history and are not hidden.

## Sensitive integration changes Claude MUST review

Paths below are relative to src/wallex_agent/:

| File | Reason / dedicated evidence |
|---|---|
| agent/decision_agent.py | Refilter injected toolsets, preserve failure audit, bounded native sync execution; tests/agent/test_decision_agent.py |
| agent/ninerouter.py | Native owned HTTP lifecycle across eval loops, timeout/retry enforcement, direct-provider/URL-secret rejection; test_ninerouter_transport.py |
| runtime/compose.py | Pass host-owned environment to the allowlist filter; agent malicious-toolset and SHADOW tests |
| runtime/shadow.py | Early environment guard and reporting counters only; test_shadow.py including write attempt and existing protective maintenance tests |

Independent GPT-6.1 Sol/max read-only reviewer: **PASS** on the main implementation diff; one minor CLI
summary issue corrected. The subsequent evaluation telemetry/flush fix was author-tested, not independently
re-reviewed. See REAL_AI_SAFETY_REVIEW.md; neither review establishes LIVE readiness.

## Exact plan state

| Task | State |
|---|---|
|1 config/hash/calendar;2 public data;3 features/C1/B0|Inherited green implementation preserved; no retuning|
|4 ledger/risk|Inherited tested scope preserved; final Claude safety review still required|
|5 execution/reconciliation|PARTIAL: durable resume/cancel completeness/LIVE exit escalation absent|
|6 journal/export/replay|PARTIAL: local exact replay works; exports are not a portable full journal/payload bundle|
|7 orchestrator/BACKTEST|Existing deterministic end-to-end plus real-AI paired/replay evidence verified|
|8 bounded Agent/eval|Small real-model native evaluation implemented; full30-snapshot benchmark and reliable read-tool adherence not established|
|9 CLI/approval/assistant|Status improved; inherited assistant counterfactual limitation unchanged|
|10 evidence|AI/B0 evidence added; active forward SHADOW/capital-safety acceptance gates incomplete|
|11 LIVE|Disabled; no private spike or activation|

## Exact remaining blockers / recommended next Claude work

1. **Safety review first**, including earlier accounting/order fixes plus Agent tool filtering and current MLflow isolation.
2. **Durable restart/recovery** under TDD: accepted-but-lost response, crash before/after send, late fill/cancel,
   reservation/approval ownership and re-arming protective exits. Keep existing fail-closed restart guards.
3. **Risk/Execution verification**, then longer public SHADOW with natural candidates and real Agent decisions;
   fix model read-tool argument adherence without weakening strict trade contracts.
4. Prepare strategy-tuning/live-readiness work only after review. Preserve negative trials and time-ordered splits.
   LIVE also needs owner-approved limits, official competition/window times, fees/account mapping, U04 and
   explicit authorization; none was invented here.

## Reproduce

Credential is set by the owner locally through NINEROUTER_API_KEY_FILE outside the repository; never paste it.
No real-model command below runs in ordinary pytest.

```bash
uv sync --locked --group evaluation
uv run pytest
uv run pyright
uv run pytest tests/agent tests/runtime/test_shadow.py tests/runtime/test_safety_controls.py tests/runtime/test_reporter.py
uv run wallex-agent policy-matrix
uv run wallex-agent tool-exposure
NINEROUTER_MODEL=gemini-3.8-flash uv run --group evaluation pytest -m ninerouter
NINEROUTER_MODEL=gemini-3.8-flash uv run --group evaluation wallex-agent eval-ninerouter --models gemini-3.8-flash --repeats 1 --mcp --out var/new-eval/eval.json
WALLEX_AGENT_STATE_DIR=var/new-shadow NINEROUTER_MODEL=gemini-3.8-flash uv run wallex-agent shadow --config config/shadow.toml --decider LLM --duration 300 --run-id new-shadow
WALLEX_AGENT_STATE_DIR=var/new-paired uv run wallex-agent backtest --config reports/phase-03/ai/PAIRED_CONFIG.toml --split VALIDATION --decider BASELINE --run-id new-b0 --export
WALLEX_AGENT_STATE_DIR=var/new-paired NINEROUTER_MODEL=gemini-3.8-flash uv run wallex-agent backtest --config reports/phase-03/ai/PAIRED_CONFIG.toml --split VALIDATION --decider LLM --run-id new-ai --export
WALLEX_AGENT_STATE_DIR=var/new-paired uv run wallex-agent replay new-ai
```

New model calls are stochastic and may differ. Exact replay of this recorded paired experiment needs the local
source var/real-ai-evidence/ablation/journal.sqlite and replay.sqlite. The portable model-free replay regressions
are in tests/agent/test_decision_agent.py and tests/runtime/test_runtime.py. Do not reuse an existing run ID/path.

```bash
uv run python - <<'PY'
from pathlib import Path
from wallex_agent.journal.export import known_secret_literals, scan_for_secrets
for path in ("runs/published", "reports/phase-03/ai", "reports/phase-03/shadow"):
    scan_for_secrets(Path(path), extra_literals=known_secret_literals())
print("published evidence secret scan: PASS")
PY
```

Full local source/artifact scan used var/real-ai-evidence/scan.py, documented by CODEX_SECRET_SCAN.json.
Tracked evidence: NINEROUTER_EVAL_FA.md, SHADOW_EVIDENCE_FA.md, OWNER_REPORT_FA.md, ai/*.json,
shadow/REAL_AI_PUBLIC_SMOKE.json, REAL_AI_SAFETY_REVIEW.md, CODEX_VERIFICATION.json.
Exact continuation file list: REAL_AI_CHANGED_FILES.txt. Earlier CODEX_CHANGED_FILES.txt remains the prior takeover scope.

Known evidence-only issue: the one-off SHADOW post-run formatter omitted label/period and failed after both
runtimes sealed successfully. Export was read from the sealed journals without editing them; no fabricated fills.
Two public-probe startups were stopped before model invocation due to MLflow telemetry/import lock contention;
fixed with the supported DISABLE flag before import. Original failure evidence was retained locally.
