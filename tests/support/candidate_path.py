"""Integrated candidate-path scenario (Phase 03 integration evidence). No network unless a real NineRouter model is
passed in; no Wallex client at all; the only venue is the SHADOW simulator.

market snapshot -> features -> C1 candidate -> decision provider -> validation -> policy -> Risk Engine
-> ShadowVenue simulated execution -> journal (decision record + state log) -> replay of the recorded decision.

`scripted_selector` is a PydanticAI FunctionModel that selects the first candidate the kernel offered. It exists only
to exercise the execution path deterministically and is ALWAYS labelled SCRIPTED (never reported as an AI decision).
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from support.synthetic import REPO, metadata_dir, synthetic_bars
from wallex_agent.agent.context import DecisionProvider
from wallex_agent.agent.replay import ReplayDecisionProvider
from wallex_agent.contracts import CodeVersion, DecisionRecord, Environment
from wallex_agent.execution.sim_shadow import ShadowVenue
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import aggregate
from wallex_agent.market.book import OrderBook
from wallex_agent.market.dataset import Dataset, load_dataset, write_dataset
from wallex_agent.recovery.coordinator import inspect_run
from wallex_agent.recovery.rebuild import ledger_digest
from wallex_agent.runtime.backtest import universe_from_metadata
from wallex_agent.runtime.config import RunConfig, load_run_config
from wallex_agent.runtime.engine import TradingRuntime

T = datetime(2026, 10, 3, 6, 15, tzinfo=UTC)
"""A bar close where the C1 strategy produces a BTCTMN breakout candidate on the synthetic series."""
CODE = CodeVersion(git_commit="c" * 40, dirty=False)


def dataset(root: Path) -> Dataset:
    d = root / "ds"
    write_dataset(d, "synthetic-test", {"BTCTMN": synthetic_bars(3, 1e8, 0.0), "ETHTMN": synthetic_bars(3, 5e6, 1.3)},
                  extra={"note": "synthetic"})
    metadata_dir(d / "metadata")
    return load_dataset(d)


def shadow_cfg(**overrides: Any) -> RunConfig:
    """The repository backtest config switched to SHADOW (synthetic dataset), with dotted-key overrides."""
    data = load_run_config(REPO / "config" / "backtest.toml").model_dump(mode="python")
    for dotted, value in {"run.environment": Environment.SHADOW, **overrides}.items():
        node = data
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    data["backtest"]["dataset"] = "synthetic-test"
    return RunConfig.model_validate(data)


def scripted_selector() -> FunctionModel:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompt = next(p.content for m in messages for p in getattr(m, "parts", []) if isinstance(p, UserPromptPart))
        body = json.loads(str(prompt).split("\n", 1)[1])
        cand = body["CANDIDATES"][0]
        decision = {"snapshot_id": body["snapshot_id"], "action": "ENTER", "selected_candidate_id": cand["candidate_id"],
                    "regime_assessment": body["deterministic_regime"], "confidence": "MEDIUM",
                    "reason_codes": ["BREAKOUT_CONFIRMED"], "evidence_feature_ids": cand["evidence_feature_ids"],
                    "brief_rationale": "اسکریپت آزمایشی: اولین کاندیدای ارائه‌شده انتخاب شد"}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, decision)])

    return FunctionModel(fn, model_name="scripted-selector")


def _runtime(cfg: RunConfig, ds: Dataset, store: JournalStore, run_id: str, decider: DecisionProvider
             ) -> tuple[TradingRuntime, ShadowVenue]:
    metas, eligible, universe = universe_from_metadata(ds.path / "metadata")
    books: dict[str, OrderBook] = {}
    venue = ShadowVenue(fee_rate=cfg.costs.fee_rate, book=books.get)
    rt = TradingRuntime(cfg, store=store, venue=venue, decider=decider, metas=metas, eligible=eligible,
                        universe_version=universe, run_id=run_id, code=CODE, dataset_id=ds.name, data_source="FIXTURE")
    rt.start(T - timedelta(minutes=1), dependency_lock_sha256="a" * 64)
    for symbol in rt.symbols:
        price = [b for b in ds.bars[symbol] if b.ts_open + timedelta(minutes=1) <= T][-1].close
        books[symbol] = OrderBook(bids=((price * Decimal("0.9997"), Decimal(1)),), asks=((price * Decimal("1.0003"), Decimal(1)),),
                                  received_at=T)
        rt.update_market(symbol, mark=price, book=books[symbol])
    return rt, venue


def _cycle(rt: TradingRuntime, ds: Dataset) -> DecisionRecord:
    bars = [b for b in aggregate(ds.bars["BTCTMN"], 900) if b.ts_open + timedelta(seconds=900) <= T]
    return rt.cycle("BTCTMN", T, bars[-rt.history_window:], flags=frozenset())


def _summary(rec: DecisionRecord, rt: TradingRuntime, venue: ShadowVenue) -> dict[str, Any]:
    v = rec.risk_verdict
    pos = rt.ledger.positions.get("BTCTMN")
    return {"status": rec.status.value, "candidates": [c.candidate_id for c in rec.candidates],
            "effective_action": rec.effective_action.value,
            "selected_candidate_id": rec.validated.decision.selected_candidate_id if rec.validated and rec.validated.decision else None,
            "validation": rec.validated.status.value if rec.validated else None,
            "provenance": rec.provenance.source.value if rec.provenance else None,
            "model_id": rec.provenance.model_id if rec.provenance else None,
            "policy": rec.policy_outcome.value if rec.policy_outcome else None,
            "risk_outcome": v.outcome.value if v else None,
            "risk_failed_checks": [c.name for c in v.checks if not c.passed] if v else [],
            "intent": None if rec.order_intent is None else {"side": rec.order_intent.side, "qty": str(rec.order_intent.qty),
                                                              "limit_price": str(rec.order_intent.limit_price),
                                                              "client_order_id": rec.order_intent.client_order_id},
            "execution_states": [e.state.value for e in rec.execution_events],
            "simulated_submissions": len(venue.submitted), "venue_environment": venue.environment.value,
            "venue_fill_source": venue.fill_source.value,
            "position_qty": str(pos.qty) if pos else None,
            "stop_price": str(pos.plan.stop_price) if pos and pos.plan else None}


def run_case(cfg: RunConfig, ds: Dataset, store_path: Path, label: str,
             decider: Callable[[JournalStore], DecisionProvider]) -> dict[str, Any]:
    """One source run and one replay run in the same journal; returns the evidence dict. The decider is built
    against the journal, as the composition root does, so model outputs are recorded for replay."""
    assert cfg.run.environment is Environment.SHADOW
    store = JournalStore(store_path)
    rt, venue = _runtime(cfg, ds, store, f"proof-{label}", decider(store))
    rec = _cycle(rt, ds)
    source = _summary(rec, rt, venue)
    rebuilt, _ = inspect_run(store, f"proof-{label}", cfg)
    source["state_log_replays_to_ledger"] = ledger_digest(rebuilt) == ledger_digest(rt.ledger)

    replay_rt, replay_venue = _runtime(cfg, ds, store, f"replay-{label}",
                                       ReplayDecisionProvider(store, f"proof-{label}", exit_enabled=cfg.agent.allow_exit_position))
    replay = _summary(_cycle(replay_rt, ds), replay_rt, replay_venue)
    keys = ("status", "effective_action", "selected_candidate_id", "validation", "risk_outcome", "intent",
            "execution_states", "position_qty")
    same = all((source[k] if k != "intent" or source[k] is None else {**source[k], "client_order_id": None})
               == (replay[k] if k != "intent" or replay[k] is None else {**replay[k], "client_order_id": None}) for k in keys)
    return {"label": label, "source": source, "replay": replay, "replay_matches": same,
            # The runtime's only write port is the in-process simulator: no Wallex client exists in this scenario at all.
            "real_wallex_writes": 0 if isinstance(rt.venue, ShadowVenue) and isinstance(replay_rt.venue, ShadowVenue) else None}
