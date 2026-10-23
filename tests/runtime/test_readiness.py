"""Deterministic readiness gate: READY_FOR_SHADOW / READY_FOR_LIVE_PRECHECK / BLOCKED. LIVE stays BLOCKED while any
mandatory owner input, account preflight or execution-state item is unresolved; nothing here calls a network or model."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from wallex_agent.agent.decision_agent import load_prompt
from wallex_agent.contracts import Environment, MarketMeta
from wallex_agent.journal.store import JournalStore
from wallex_agent.runtime.config import RunConfig, ShadowSettings, missing_live_requirements
from wallex_agent.runtime.preflight import run_preflight
from wallex_agent.runtime.readiness import AccountEvidence, AiEvalEvidence, OwnerAttestations, ReadinessReport, readiness_gate

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
F2 = {"version": "f2-test", "entry_band_atr": "0.5", "stop_atr": "5", "trail_atr": "5", "max_holding_s": 604800,
      "candidate_ttl_s": 900, "regime_filter": True}
WINDOW = {"version": "tw-test-1", "timezone": "Asia/Tehran", "no_new_entry_before_close_s": 1800, "flatten_before_close_s": 900,
          "windows": [{"days": ["SAT", "SUN", "MON", "TUE", "WED"], "start": "09:00", "end": "21:00"}]}


def _f2(make_cfg: Callable[..., RunConfig], **extra: Any) -> RunConfig:
    return make_cfg(**{"run.environment": Environment.SHADOW, "run.bar_interval_seconds": 14400, "features.ema_bars": 48,
                       "features.regime_er_bars": 42, "features.regime_rank_bars": 1080, "strategy.f2": F2,
                       "strategy.active": "F2_tsm", "execution.exit_order_ttl_s": 60,
                       "limits.cutoff": {"no_new_entry_before_end_s": 14400, "liquidation_start_before_end_s": 7200},
                       "shadow": ShadowSettings().model_dump(), **extra})


def _complete(make_cfg: Callable[..., RunConfig]) -> RunConfig:
    cfg = _f2(make_cfg, **{"trading_window": WINDOW, "competition.end_utc": datetime(2026, 11, 1, tzinfo=UTC),
                           "run.live_autonomous_enabled": True})
    data = cfg.model_dump(mode="python")
    data["limits"]["approved_by_owner"] = True
    return RunConfig.model_validate(data)


def _attest(symbols: tuple[str, ...] = ("BTCTMN", "ETHTMN")) -> OwnerAttestations:
    return OwnerAttestations(version="own-1", trading_window_source="official challenge notice", u04_autonomy="AUTONOMOUS_ALLOWED",
                             eligible_symbols_confirmed=symbols, limits_approved_on="2026-10-23")


def _account(fee: str = "0.0025", tmn: str = "5000000") -> AccountEvidence:
    return AccountEvidence(read_at=NOW, fee_rates={"BTCTMN": Decimal(fee), "ETHTMN": Decimal(fee)}, spendable_tmn=Decimal(tmn),
                           locked_tmn=Decimal(0), open_orders=0, non_tmn_assets=())


def test_live_requires_the_exit_order_ttl(make_cfg: Callable[..., RunConfig]) -> None:
    cfg = _complete(make_cfg)
    assert "execution.exit_order_ttl_s" not in missing_live_requirements(cfg)
    data = cfg.model_dump(mode="python")
    data["execution"]["exit_order_ttl_s"] = None
    assert "execution.exit_order_ttl_s" in missing_live_requirements(RunConfig.model_validate(data))


def test_preflight_fails_when_the_real_fee_exceeds_the_assumed_fee(make_cfg: Callable[..., RunConfig]) -> None:
    class Account:
        def balances(self) -> dict[str, tuple[Decimal, Decimal]]:
            return {"TMN": (Decimal(5_000_000), Decimal(0))}

        def fee_rate(self, symbol: str) -> Decimal | None:
            return Decimal("0.0035")

        def open_orders(self) -> list[dict[str, Any]]:
            return []

    cfg = _complete(make_cfg)
    rep = run_preflight(cfg, now=NOW, account=Account(), metas={}, eligible=frozenset(), marks={}, require_clean_tree=False)
    fee = [c for c in rep.checks if c[0].startswith("fee_rate:")]
    assert fee and not any(ok for _, ok, _ in fee)


def test_shadow_config_is_ready_for_shadow_but_live_is_blocked_without_owner_inputs(make_cfg: Callable[..., RunConfig],
                                                                                   tmp_path: Path) -> None:
    g = readiness_gate(_f2(make_cfg), store=JournalStore(tmp_path / "j.sqlite"), attestations=None, account=None,
                       ai_qa_passed=None, now=NOW)
    assert g.level == "READY_FOR_SHADOW"
    blocked = {i.name for i in g.items if i.status == "BLOCKED"}
    assert {"official_trading_window", "competition_cutoff", "u04_autonomy", "market_eligibility", "account_preflight",
            "owner_limits"} <= blocked


def _metas(min_notional: str = "50000") -> dict[str, MarketMeta]:
    return {s: MarketMeta(symbol=s, quote_asset="TMN", price_precision=0, amount_precision=6, min_qty=Decimal("0.000001"),
                          min_notional=Decimal(min_notional), market_order_enabled=True, metadata_version="t")
            for s in ("BTCTMN", "ETHTMN")}


def _ai(cfg: RunConfig, **kw: Any) -> AiEvalEvidence:
    _, sha = load_prompt(cfg.agent.prompt_version)
    base: dict[str, Any] = {"role": cfg.agent.role, "prompt_version": cfg.agent.prompt_version, "prompt_sha256": sha,
                            "model_id": "cx/gpt-6.1-sol", "passed": True, "source": "test"}
    return AiEvalEvidence.model_validate(base | kw)


def _gate(cfg: RunConfig, store: JournalStore, **kw: Any) -> ReadinessReport:
    args: dict[str, Any] = {"attestations": _attest(), "account": _account(), "ai_qa_passed": None, "now": NOW, "metas": _metas(),
                            "evidence_ok": True}
    return readiness_gate(cfg, store=store, **(args | kw))


def test_everything_resolved_gives_live_precheck_only(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    g = _gate(_complete(make_cfg), JournalStore(tmp_path / "j.sqlite"))
    assert g.level == "READY_FOR_LIVE_PRECHECK", [i for i in g.items if i.status != "PASS"]
    names = {i.name for i in g.items}
    assert {"actual_fee_rate", "available_tmn_balance", "existing_orders_assets", "precision_min_order", "exit_ttl_cancel_safety",
            "observability_evidence", "news_disabled_for_trading", "warmup_path", "execution_state_safe"} <= names


def test_each_new_live_item_blocks_on_its_own(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    cfg = _complete(make_cfg)
    stale = _account().model_copy(update={"read_at": NOW - timedelta(hours=2)})
    dirty = _account().model_copy(update={"open_orders": 1})
    # the schema already caps it at 300 s; the gate re-checks it (defence in depth), so bypass validation here
    slow_exit = cfg.model_copy(update={"execution": cfg.execution.model_copy(update={"exit_order_ttl_s": 900})})
    cases = {"account_preflight": _gate(cfg, store, account=stale), "existing_orders_assets": _gate(cfg, store, account=dirty),
             "precision_min_order": _gate(cfg, store, metas={"BTCTMN": _metas()["BTCTMN"]}),
             "precision_min_order ": _gate(cfg, store, metas=_metas(min_notional="99000000")),
             "exit_ttl_cancel_safety": _gate(slow_exit, store), "observability_evidence": _gate(cfg, store, evidence_ok=None)}
    for name, g in cases.items():
        assert g.level == "READY_FOR_SHADOW" and name.strip() in {i.name for i in g.items if i.status == "BLOCKED"}, name


def test_llm_live_needs_the_frozen_ai_role_and_prompt(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    base = _complete(make_cfg)
    cfg = base.model_copy(update={"agent": base.agent.model_copy(update={"decision_provider": "LLM", "model_id": "cx/gpt-6.1-sol",
                                                                         "role": "VETO", "prompt_version": "veto_v1"})})
    assert _gate(cfg, store, ai_qa_passed=True, ai_eval=_ai(cfg)).level == "READY_FOR_LIVE_PRECHECK"
    for bad in (None, _ai(cfg, passed=False), _ai(cfg, role="SELECTOR"), _ai(cfg, prompt_sha256="0" * 64),
                _ai(cfg, model_id="gm/gemini-x")):
        g = _gate(cfg, store, ai_qa_passed=True, ai_eval=bad)
        assert g.level == "READY_FOR_SHADOW" and "ai_role_eval" in {i.name for i in g.items if i.status == "BLOCKED"}
    # a NINEROUTER_MODEL override on the run host would run a model that was never evaluated
    g = _gate(cfg, store, ai_qa_passed=True, ai_eval=_ai(cfg), resolved_model_id="gm/gemini-x")
    assert "ai_role_eval" in {i.name for i in g.items if i.status == "BLOCKED"}


def test_veto_role_requires_a_veto_prompt() -> None:
    import pytest

    from wallex_agent.runtime.config import AgentSettings

    assert AgentSettings(role="VETO", prompt_version="veto_v1").role == "VETO"
    with pytest.raises(ValueError):
        AgentSettings(role="VETO", prompt_version="decision_v3")
    with pytest.raises(ValueError):
        AgentSettings(role="SELECTOR", prompt_version="veto_v1")


def test_news_is_not_wired_into_the_trading_path(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    g = _gate(_complete(make_cfg), JournalStore(tmp_path / "j.sqlite"))
    assert next(i for i in g.items if i.name == "news_disabled_for_trading").status == "PASS"
    import wallex_agent.runtime as runtime_pkg

    for src in Path(runtime_pkg.__file__).parent.glob("*.py"):
        assert "wallex_agent.news" not in src.read_text(), src.name


def test_each_unresolved_live_item_keeps_live_blocked(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    cfg = _complete(make_cfg)
    llm = cfg.model_copy(update={"agent": cfg.agent.model_copy(update={"decision_provider": "LLM", "model_id": "cx/x"})})
    cases = [
        _gate(cfg, store, attestations=_attest(("BTCTMN",))),
        _gate(cfg, store, account=_account(fee="0.0035")),
        _gate(cfg, store, account=_account(tmn="4000000")),
        _gate(llm, store, ai_qa_passed=False, ai_eval=_ai(llm)),
    ]
    assert [g.level for g in cases] == ["READY_FOR_SHADOW"] * 4
    assert "actual_fee_rate" in {i.name for i in cases[1].items if i.status == "BLOCKED"}
    assert "available_tmn_balance" in {i.name for i in cases[2].items if i.status == "BLOCKED"}


def test_missing_exit_ttl_or_unbuildable_strategy_blocks_even_shadow(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    no_ttl = _f2(make_cfg, **{"execution.exit_order_ttl_s": None})
    assert readiness_gate(no_ttl, store=store, attestations=None, account=None, ai_qa_passed=None, now=NOW).level == "BLOCKED"
    no_ema = _f2(make_cfg, **{"features.ema_bars": None})
    assert readiness_gate(no_ema, store=store, attestations=None, account=None, ai_qa_passed=None, now=NOW).level == "BLOCKED"
