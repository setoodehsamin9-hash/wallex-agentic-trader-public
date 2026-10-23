"""Composition root: builds the decision provider for the configured environment. In LIVE (Phase 05) the decider is the
same LLM/BASELINE provider with only the allowlisted public read tools; the LIVE venue and every start check live in
runtime/live.py. REPLAY is refused in LIVE."""

import os
from typing import Any

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.agent.context import DecisionProvider
from wallex_agent.agent.decision_agent import LlmDecisionProvider
from wallex_agent.agent.ninerouter import build_model, load_settings, resolve_api_key
from wallex_agent.agent.replay import ReplayDecisionProvider
from wallex_agent.contracts import Environment
from wallex_agent.journal.store import JournalStore
from wallex_agent.tools import Consumer, exposed_tools

from .config import RunConfig

WALLEX_MCP_URL = os.environ.get("WALLEX_MCP_URL", "https://api.wallex.ir/mcp")


class LiveNotEnabled(RuntimeError):
    pass


def build_decider(cfg: RunConfig, store: JournalStore, *, replay_store: JournalStore | None = None) -> tuple[DecisionProvider, dict[str, str | None]]:
    a = cfg.agent
    env = cfg.run.environment
    if env is Environment.LIVE and a.decision_provider == "REPLAY":
        # Building a decider sends nothing; runtime/live.py gates every write. Recorded outputs never trade real money.
        raise LiveNotEnabled("LIVE never replays recorded model outputs")
    if a.decision_provider == "BASELINE":
        return BaselineSelector(), {"route": "B0 deterministic selector (no language model)", "provider": None, "model_id": None}
    if a.decision_provider == "REPLAY":
        if not a.replay_run_id:
            raise ValueError("agent.replay_run_id is required for REPLAY")
        return ReplayDecisionProvider(replay_store or store, a.replay_run_id, exit_enabled=a.allow_exit_position, role=a.role), {
            "route": f"replay of recorded outputs from run {a.replay_run_id} (no model, no network)", "provider": "replay",
            "model_id": None}
    settings = load_settings(model_id=a.model_id, timeout_s=a.decision_deadline_s)
    key = resolve_api_key()
    model = build_model(settings, key)
    toolset: Any = None
    if a.use_wallex_mcp_tools and env is not Environment.BACKTEST:
        from pydantic_ai.mcp import MCPToolset

        allowed = exposed_tools(Consumer.DECISION_AGENT, env)
        toolset = MCPToolset(WALLEX_MCP_URL, tool_error_behavior="failed", max_retries=0).filtered(
            lambda ctx, td: td.name in allowed)
    provider = LlmDecisionProvider(model, model_label=settings.model_id, settings=a, store=store, mcp_toolset=toolset,
                                   stream=settings.stream, secret_literals=(key,), environment=env)
    return provider, {"route": f"NineRouter ({settings.base_url}) → {settings.model_id}", "provider": "ninerouter",
                      "model_id": settings.model_id, "endpoint": settings.base_url, "settings_sha256": provider.settings_sha256,
                      "prompt_sha256": provider.prompt_sha256}
