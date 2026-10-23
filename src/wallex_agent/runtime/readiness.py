"""Deterministic readiness gate (Phase 05): BLOCKED / READY_FOR_SHADOW / READY_FOR_LIVE_PRECHECK.

Pure function of the run config, the journal and OWNER-authored inputs (attestations, a read-only account preflight
result, the real-model QA result). No network, no model, no private call. READY_FOR_LIVE_PRECHECK only means the
static prerequisites are met: the LIVE preflight (`runtime/preflight.py`) must still pass on the run host, from a
clean tagged commit, immediately before an owner-started LIVE run. Unknowns are never resolved by default values.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from wallex_agent.agent.decision_agent import load_prompt
from wallex_agent.contracts import AutonomyMode, Contract, MarketMeta, UtcDatetime
from wallex_agent.journal.store import JournalStore
from wallex_agent.strategies import build_strategy, strategy_history

from .config import RunConfig, missing_live_requirements

Level = Literal["BLOCKED", "READY_FOR_SHADOW", "READY_FOR_LIVE_PRECHECK"]
MINUTE_WINDOW_S = 2 * 86400
ACCOUNT_MAX_AGE_S = 3600
"""A read-only account preflight older than this is not evidence about the account at LIVE start."""
MAX_LIVE_EXIT_TTL_S = 300
"""A resting LIVE exit must be cancelled, confirmed and re-issued within this many seconds."""


class OwnerAttestations(Contract):
    """Written by the owner (never by an agent). Every field left empty keeps its LIVE item BLOCKED."""

    version: str
    trading_window_source: str | None = None
    """Where the configured [trading_window] / cutoff hours come from (official notice/brief reference)."""
    u04_autonomy: Literal["AUTONOMOUS_ALLOWED", "APPROVAL_REQUIRED_ONLY"] | None = None
    eligible_symbols_confirmed: tuple[str, ...] = ()
    limits_approved_on: str | None = None


class AccountEvidence(Contract):
    """Result of an owner-run READ-ONLY account preflight (fee, balances, open orders). No secret is stored here."""

    read_at: UtcDatetime
    fee_rates: dict[str, Decimal]
    spendable_tmn: Decimal = Field(ge=0)
    locked_tmn: Decimal = Field(ge=0)
    open_orders: int = Field(ge=0)
    non_tmn_assets: tuple[str, ...] = ()


class AiEvalEvidence(Contract):
    """Frozen AI-role evaluation result (scripts/phase05_f2_alignment.py summarize). LIVE must run exactly this role/prompt."""

    role: Literal["SELECTOR", "VETO"]
    prompt_version: str
    prompt_sha256: str
    model_id: str
    passed: bool
    source: str


class GateItem(Contract):
    name: str
    scope: Literal["SHADOW", "LIVE"]
    status: Literal["PASS", "BLOCKED"]
    detail: str


class ReadinessReport(Contract):
    level: Level
    items: tuple[GateItem, ...]


def readiness_gate(cfg: RunConfig, *, store: JournalStore | None, attestations: OwnerAttestations | None,
                   account: AccountEvidence | None, ai_qa_passed: bool | None, now: datetime,
                   metas: dict[str, MarketMeta] | None = None, ai_eval: AiEvalEvidence | None = None,
                   evidence_ok: bool | None = None, resolved_model_id: str | None = None) -> ReadinessReport:
    items: list[GateItem] = []

    def add(name: str, scope: Literal["SHADOW", "LIVE"], ok: bool, detail: str) -> None:
        items.append(GateItem(name=name, scope=scope, status="PASS" if ok else "BLOCKED", detail=detail))

    # ---------------------------------------------------------------- SHADOW-mandatory (deterministic capability)
    built, why = True, "strategy builds from config"
    try:
        assert cfg.strategy is not None and cfg.features is not None
        build_strategy(cfg.strategy, cfg.costs, cfg.execution, cfg.features)
    except (AssertionError, ValueError) as exc:
        built, why = False, f"strategy not buildable: {exc}"
    add("strategy_buildable", "SHADOW", built, why)
    add("exit_order_ttl", "SHADOW", cfg.execution.exit_order_ttl_s is not None,
        f"execution.exit_order_ttl_s={cfg.execution.exit_order_ttl_s}")
    needs = (strategy_history(cfg.strategy, cfg.features) * cfg.run.bar_interval_seconds
             if built and cfg.strategy and cfg.features else 0)
    add("warmup_path", "SHADOW", cfg.shadow is not None and (needs <= MINUTE_WINDOW_S or cfg.run.bar_interval_seconds % 3600 == 0),
        f"history needed {needs // 3600}h; native-1h warm-up {'required' if needs > MINUTE_WINDOW_S else 'not needed'}")

    # ---------------------------------------------------------------- LIVE-mandatory (owner inputs + account + state)
    a = attestations
    add("official_trading_window", "LIVE", cfg.trading_window is not None and bool(a and a.trading_window_source),
        f"trading_window={'set' if cfg.trading_window else 'missing'}; source={a.trading_window_source if a else None}")
    cut = cfg.limits.cutoff
    end = cfg.competition.end_utc
    add("competition_cutoff", "LIVE", end is not None and end > now and cut.no_new_entry_before_end_s is not None
        and cut.liquidation_start_before_end_s is not None, f"competition.end_utc={end.isoformat() if end else None}")
    u04 = a.u04_autonomy if a else None
    if cfg.run.autonomy is AutonomyMode.AUTONOMOUS:
        ok = u04 == "AUTONOMOUS_ALLOWED" and cfg.run.live_autonomous_enabled
    else:
        ok = u04 is not None
    add("u04_autonomy", "LIVE", ok, f"autonomy={cfg.run.autonomy.value} owner_answer={u04} live_autonomous_enabled="
        f"{cfg.run.live_autonomous_enabled}")
    confirmed = set(a.eligible_symbols_confirmed) if a else set[str]()
    add("market_eligibility", "LIVE", set(cfg.run.symbols) <= confirmed,
        f"symbols={list(cfg.run.symbols)} confirmed={sorted(confirmed)}")
    if account is None:
        add("account_preflight", "LIVE", False, "no read-only account preflight result (fee, balance, open orders)")
        for name in ("actual_fee_rate", "available_tmn_balance", "existing_orders_assets"):
            add(name, "LIVE", False, "needs the read-only account preflight")
    else:
        age = (now - account.read_at).total_seconds()
        add("account_preflight", "LIVE", 0 <= age <= ACCOUNT_MAX_AGE_S, f"read {int(age)}s ago (max {ACCOUNT_MAX_AGE_S}s)")
        tol = cfg.limits.kill.reconciliation_tolerance_tmn or Decimal(0)
        fees_ok = all(account.fee_rates.get(s) is not None and account.fee_rates[s] <= cfg.costs.fee_rate for s in cfg.run.symbols)
        add("actual_fee_rate", "LIVE", fees_ok,
            f"fees={ {k: str(v) for k, v in account.fee_rates.items()} } <= assumed {cfg.costs.fee_rate}: {fees_ok}")
        bal_ok = abs(account.spendable_tmn - cfg.run.starting_capital_tmn) <= tol and account.locked_tmn == 0
        add("available_tmn_balance", "LIVE", bal_ok,
            f"spendable={account.spendable_tmn} vs {cfg.run.starting_capital_tmn} (tol {tol}); locked={account.locked_tmn}")
        clean = account.open_orders == 0 and not account.non_tmn_assets
        add("existing_orders_assets", "LIVE", clean,
            f"open_orders={account.open_orders} non_tmn_assets={list(account.non_tmn_assets)}")
    if metas is None:
        add("precision_min_order", "LIVE", False, "no market metadata (price/amount precision, min qty, min notional)")
    else:
        cap = cfg.limits.risk.max_position_notional_tmn
        bad = [s for s in cfg.run.symbols if (m := metas.get(s)) is None or m.min_qty <= 0 or m.min_notional <= 0
               or (cap is not None and m.min_notional > cap)]
        add("precision_min_order", "LIVE", not bad,
            f"unusable metadata for {bad}" if bad else f"precision/min-order metadata present for {list(cfg.run.symbols)}")
    ttl = cfg.execution.exit_order_ttl_s
    add("exit_ttl_cancel_safety", "LIVE", ttl is not None and 0 < ttl <= MAX_LIVE_EXIT_TTL_S,
        f"exit_order_ttl_s={ttl} (LIVE max {MAX_LIVE_EXIT_TTL_S}s; resting exit is cancelled, confirmed, re-issued)")
    limits_missing = [m for m in missing_live_requirements(cfg) if m.startswith("limits.")]
    add("owner_limits", "LIVE", not limits_missing and bool(a and a.limits_approved_on),
        f"missing={limits_missing}; approved_on={a.limits_approved_on if a else None}")
    unfinished = [m.run_id for m in store.unfinished_runs()] if store is not None else []
    add("execution_state_safe", "LIVE", not unfinished, f"unfinished runs needing recovery/abandon: {unfinished}")
    if cfg.agent.decision_provider == "LLM":
        add("real_model_qa", "LIVE", ai_qa_passed is True, f"NineRouter QA passed={ai_qa_passed} model={cfg.agent.model_id}")
        ok, detail = False, "no frozen AI role evaluation evidence"
        if ai_eval is not None:
            try:
                _, sha = load_prompt(cfg.agent.prompt_version)
            except FileNotFoundError:
                sha = None
            model = resolved_model_id or cfg.agent.model_id  # the id the run will really use (NINEROUTER_MODEL wins)
            ok = (ai_eval.passed and ai_eval.role == cfg.agent.role and ai_eval.prompt_version == cfg.agent.prompt_version
                  and ai_eval.prompt_sha256 == sha and ai_eval.model_id == model == cfg.agent.model_id)
            detail = (f"role {cfg.agent.role}/{ai_eval.role}; prompt {cfg.agent.prompt_version}/{ai_eval.prompt_version} "
                      f"sha_match={ai_eval.prompt_sha256 == sha}; model config={cfg.agent.model_id} resolved={model} "
                      f"eval={ai_eval.model_id}; "
                      f"eval passed={ai_eval.passed} ({ai_eval.source})")
        add("ai_role_eval", "LIVE", ok, detail)
    add("observability_evidence", "LIVE", evidence_ok is True,
        "evidence bundle verified offline (hashes, reconstruction, REAL WRITE=0)" if evidence_ok else f"evidence verified={evidence_ok}")
    add("news_disabled_for_trading", "LIVE", "news" not in RunConfig.model_fields,
        "news overlay is not part of RunConfig or the trading path (news-overlay-0.1.0 produced a false-positive MACRO veto)")

    shadow_ok = all(i.status == "PASS" for i in items if i.scope == "SHADOW")
    live_ok = all(i.status == "PASS" for i in items if i.scope == "LIVE")
    level: Level = "BLOCKED" if not shadow_ok else ("READY_FOR_LIVE_PRECHECK" if live_ok else "READY_FOR_SHADOW")
    return ReadinessReport(level=level, items=tuple(items))
