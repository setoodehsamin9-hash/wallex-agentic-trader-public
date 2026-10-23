# ADR-001: Agent framework — PydanticAI for the bounded reasoning loop, own deterministic orchestrator

**Status:** Accepted (Phase 02). Revisit at the Phase 03 gate listed under *Consequences*.
**Date:** 2026-10-22
**Deciders:** Project owner (approves), lead architect (proposes)

## Context

The challenge scores genuine AI use (35), strategy logic (40), risk management (30), reproducibility (25) and
backtest/shadow evidence (20), next to returns and stability. The system must be agentic: state, tools, structured
actions, a multi-step workflow, explicit tool permissions and auditable decisions. It must not become
"market data → one LLM call → BUY/SELL".

What the framework actually has to carry is narrower than it sounds, because of decisions in ADR-002:

- The model never holds a write tool. Orders, approvals, risk, ledger and exits are deterministic code.
- So the framework is needed for **one bounded loop**: observe a frozen snapshot → call a few read tools → return a
  validated `AgentDecision`. The same loop with a different output type serves the **assistant**.
- Workflow state (cycle stage, pending approvals, kill latch) and trade truth (orders, fills, balances) live in
  our own SQLite-backed services, never in framework memory or an MCP session.

Forces:

| Force | Implication |
|---|---|
| Wallex MCP is first-class, Streamable HTTP, 52 tools, **no safety annotations** | Need an MCP client plus our own name allowlist (default deny) |
| Wallex order schema only requires side/symbol/type | Typed output validation is necessary but not sufficient; domain validator stays ours |
| Replay must work without a model or the network | Framework must let us inject a scripted/recorded model and serve recorded tool results |
| Tool-call retries can duplicate side effects | Retry behaviour must be explicit and off for anything that writes (nothing the model sees writes) |
| Short hackathon, team skills/model access unknown (U11) | Fewest moving parts; provider-agnostic |

Evidence: `research/phase-02/AGENT_FRAMEWORK_RESEARCH_FA.md` (static review of LangGraph, LangChain, PydanticAI,
OpenAI Agents SDK, MCP Python SDK at pinned SHAs) and a Phase 02 throwaway spike, summarised below.

### Phase 02 spike (minimum verification, 2026-10-22)

`pydantic-ai-slim[mcp]==2.59.0` (pulls `fastmcp-slim 4.1.3`, `mcp 2.3.0`), Python 3.12, **public endpoint, no key**:

| Check | Result |
|---|---|
| `MCPToolset("https://api.wallex.ir/mcp")` over Streamable HTTP lists tools | 52 tools, identical names/input shapes to Phase 01 capture |
| `.filtered()` name allowlist | model saw exactly the 3 allowlisted reads |
| Scripted model (`FunctionModel`, no LLM) tries hidden `wallex_spot_post_account_orders` | rejected locally as *Unknown tool name*; nothing sent to Wallex |
| Scripted model calls `wallex_credit_prop_package_details{id:"183"}` | real public result returned to the next model turn |
| Structured output | `Decision` Pydantic model validated |
| Observed caveats | default `tool_error_behavior='retry'` (we set `'error'`/`'failed'`); console banner suggests Logfire (no exporter is configured; set `PYDANTIC_AI_NO_BANNER=1`); `UsageLimits(request_limit, tool_calls_limit, cost_limit)` available for hard caps |

Also observed via direct public calls: `wallex_basic_candle_history` with `resolution="5"` returns
`isError=false` but payload `{"s":"error","errmsg":"Invalid resolution!"}`. **MCP envelope success is not data
success**; adapters must check the payload. The spike code stayed in the scratchpad and is not part of the repo.

## Decision

1. **PydanticAI** (`pydantic-ai-slim[mcp]`, pinned exactly) is the agent runtime for the two LLM roles:
   - *Decision agent*: `output_type=AgentDecision`, toolsets = internal read tools over the frozen snapshot +
     Tool Gateway–filtered Wallex public reads, `UsageLimits` caps, `tool_error_behavior` not `retry`.
   - *Assistant*: same pattern, read-only answer type, journal/portfolio/risk query tools.
2. **Our own small deterministic orchestrator** (plain Python state machine, `wallex_agent.runtime`) owns the
   trading cycle, pending approvals, kill latch, schedulers and persistence. No graph framework.
3. **Trade approval is not a framework feature.** PydanticAI's deferred tool approval is not used for orders,
   because orders are not agent tools. Approval tickets are our own server-side records (ADR-002).
4. The model provider stays pluggable (`AGENT_MODEL="provider:model"`); the candidate default to benchmark first is
   `anthropic:claude-sonnet-5-5`, with a cheaper model for the assistant if latency/cost demand it. Final choice in
   Phase 03 after a schema-validity / latency / availability benchmark from the actual host (U11).

## Options considered

### Option A — PydanticAI + own orchestrator (chosen)

| Dimension | Assessment |
|---|---|
| Complexity | Low–Med: one agent library; workflow is ~one module we fully understand |
| MCP / tools | Native Streamable HTTP client; `filtered`/`prepared` toolsets; verified against Wallex |
| State & approval | Framework holds only conversation; workflow/approval state in our DB (what we need anyway) |
| Determinism & replay | `FunctionModel`/`TestModel` and recorded-response provider; no hidden graph state to restore |
| Auditability | We capture every tool call/result ourselves; optional OTel/Logfire is opt-in |
| Testing | Model-free tests; spike proved a hidden tool never reaches the server |
| Lock-in | Low; provider-agnostic; the agent is one adapter behind `DecisionProvider` |

**Cons:** we write the orchestrator, persistence and approval store ourselves (small, and required regardless);
PydanticAI moves fast (pin exactly, upgrade deliberately); `TestModel` calls registered tools by default, so tests
must use fake tools, not live ones.

### Option B — LangGraph (+ MCP adapter, durable checkpointer)

| Dimension | Assessment |
|---|---|
| Complexity | Med–High: nodes, reducers, checkpointer, interrupt/resume semantics |
| State & approval | Strong pause/resume; but resume **re-runs the interrupted node from its start** |
| Determinism | Checkpoints are not trade truth; an independent ledger is still required |
| MCP | Through `langchain-mcp-adapters` (separate package, own `mcp` version constraints) |

**Rejected for v1:** our cycle is a short, mostly linear pipeline with one wait state, and that wait state is a
ticket in our DB, not a suspended graph. The interrupt re-execution semantics are a footgun next to side effects,
and the checkpointer would duplicate state we must keep in the ledger anyway. Revisit if the workflow grows real
branching (multi-step human dialogs, long-lived agent plans).

### Option C — LangChain `create_agent`

Builds a LangGraph `StateGraph` internally, so it inherits Option B's state model plus more dependency layers; its
new `langchain.mcp` namespace is beta. No advantage over A for a bounded single-agent loop. **Rejected.**

### Option D — OpenAI Agents SDK

Strong candidate: Runner, Streamable HTTP MCP with header/filter support, `RunState` approval serialization,
`ScriptedModel` tests. **Not chosen** because the provider is undecided (OpenAI-default; parity for other providers
not guaranteed), tracing captures sensitive data by default and needs care, and it offers no capability we need
that A lacks. Would be the fallback if the chosen model is OpenAI and A shows a blocking defect.

### Option E — Custom loop on the official MCP Python SDK

Lowest dependency count, but we would rebuild the model↔tool loop, output validation/retry, provider adapters and
test models. Cheap prototype, expensive to make robust. **Rejected**; we still use the MCP SDK indirectly.

### Option F — Multi-agent frameworks (CrewAI, AutoGen, agent fleets)

Several agents acting on one account multiply coordination, reservation and audit problems with no proven benefit
(Phase 01 and Condor review). **Rejected.**

## Trade-off analysis

The choice that matters most is *where state and authority live*, not which agent library runs the loop. Once the
model is confined to "choose among provided candidates or WAIT, using read tools", the best framework is the one
that adds the least hidden state and the best model-free testing: Option A. LangGraph's main strength
(durable interrupts) solves a problem we deliberately moved into deterministic, DB-backed services.

## Consequences

- Easier: model-free tests of the whole decision path; replay from recorded outputs; swapping providers; ablation
  (LLM vs. baseline selector behind the same `DecisionProvider` port).
- Harder: we own orchestrator correctness (restart, idempotency, schedulers) — covered by Phase 03 tests.
- Revisit trigger (Phase 03 gate): if the model benchmark shows the chosen provider cannot reliably produce
  `AgentDecision` through PydanticAI, or a PydanticAI upgrade breaks MCP filtering, re-evaluate D, then B.
- Dependency rule: `pydantic-ai-slim[mcp]` pinned to an exact version in `pyproject.toml` and `uv.lock`; upgrades are
  an ADR-noted change with the agent contract tests re-run.

## Action items

1. [ ] Phase 03: implement `agent/decision_agent.py` and `agent/assistant.py` on `DecisionProvider` (see handoff).
2. [ ] Phase 03: model benchmark on fixed snapshots — schema-valid rate, latency p50/p95, cost — from the run host.
3. [ ] Phase 03: recorded-response `ReplayDecisionProvider` and deterministic `BaselineSelector` (B0) for ablation.
