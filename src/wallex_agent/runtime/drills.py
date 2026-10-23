"""SHADOW failure-injection drills on the real kernel (TradingRuntime + RiskEngine + ExecutionEngine + ShadowVenue).

Market data: a FROZEN capture of live public Wallex candles + depth (or synthetic data in tests), served from memory so
every drill is reproducible. Because the live market may not offer a C1 breakout at capture time, drills that need
an entry use a labelled drill candidate built from the live book (stop -1%, target +2%); the strategy is not changed.
Every drill asserts zero real Wallex writes (the ShadowVenue has no network path; the public client has no write).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.agent.context import DecisionContext, DecisionProvider
from wallex_agent.agent.validator import validate
from wallex_agent.contracts import (
    AgentAction,
    AgentDecision,
    AutonomyMode,
    Confidence,
    CycleStatus,
    DecisionProvenance,
    DecisionSource,
    Environment,
    FeatureSet,
    MarketSnapshot,
    OrderPurpose,
    OrderState,
    QualityFlag,
    ReasonCode,
    RegimeLabel,
    SignalCandidate,
    ValidatedDecision,
    ValidationStatus,
)
from wallex_agent.execution.engine import DuplicateSubmit
from wallex_agent.execution.sim_shadow import ShadowVenue
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import Bar
from wallex_agent.market.book import OrderBook

from .calendar import WindowStatus
from .config import CompetitionConfig, RunConfig, TradingWindowConfig, WindowSpec
from .engine import TradingRuntime


@dataclass
class DrillResult:
    name: str
    passed: bool
    evidence: dict[str, Any]


@dataclass(frozen=True)
class MarketCapture:
    """Frozen market data per symbol: closed strategy bars, the book at capture time, and capture time."""

    bars: dict[str, list[Bar]]
    books: dict[str, OrderBook]
    at: datetime
    source: str


class DrillCandidates:
    """Wraps the real strategy: emits one clearly labelled drill candidate from the live ask."""

    strategy_id = "DRILL_candidate"

    def __init__(self, version: str) -> None:
        self.version = f"drill-{version}"

    def candidates(self, snapshot: MarketSnapshot, features: FeatureSet) -> list[SignalCandidate]:
        ref = snapshot.book.ask if snapshot.book is not None else features.features[0].value or Decimal(1)
        return [SignalCandidate(candidate_id=f"drill-{snapshot.snapshot_id[:12]}", snapshot_id=snapshot.snapshot_id,
                                strategy_id=self.strategy_id, strategy_version=self.version, symbol=snapshot.symbol,
                                reference_price=ref, stop_price=(ref * Decimal("0.99")).quantize(Decimal(1)),
                                take_profit_price=(ref * Decimal("1.02")).quantize(Decimal(1)), max_holding_seconds=3600,
                                exit_policy_id="drill", estimated_round_trip_cost_bps=Decimal(60), evidence_feature_ids=(),
                                created_at=snapshot.as_of, expires_at=snapshot.as_of + timedelta(minutes=10))]


class ScriptedDecider:
    """Decider double for drills: returns a fixed failure/answer through the REAL validator."""

    source_name = "LLM"

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
        self.calls += 1
        now = ctx.now
        prov = DecisionProvenance(source=DecisionSource.LLM, model_id="drill", model_settings_sha256=None, prompt_version=None,
                                  prompt_sha256=None, model_requests=1, latency_ms=0, raw_output_ref=None, started_at=now, finished_at=now)
        if self.mode == "timeout":
            return validate(None, ctx, received_at=ctx.deadline + timedelta(seconds=1), exit_enabled=False,
                            failure=ValidationStatus.TIMEOUT, detail="drill: provider timeout"), prov
        if self.mode == "invented":
            d = AgentDecision(snapshot_id=ctx.snapshot.snapshot_id, action=AgentAction.ENTER, selected_candidate_id="c-invented",
                              regime_assessment=RegimeLabel.UNCERTAIN, confidence=Confidence.HIGH,
                              reason_codes=(ReasonCode.BREAKOUT_CONFIRMED,), brief_rationale="drill: invented id")
            return validate(d, ctx, received_at=now, exit_enabled=False), prov
        return BaselineSelector().decide(ctx)


def _cfg(base: RunConfig, **over: Any) -> RunConfig:
    d = base.model_dump(mode="python")
    d["run"]["environment"] = Environment.SHADOW
    d["run"]["autonomy"] = AutonomyMode.AUTONOMOUS
    for k, v in over.items():
        node = d
        parts = k.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = v
    return RunConfig.model_validate(d)


def _runtime(cfg: RunConfig, cap: MarketCapture, decider: DecisionProvider, tmp: Callable[[str], JournalStore], name: str,
             metas: dict[str, Any], eligible: frozenset[str], competition: CompetitionConfig | None = None) -> TradingRuntime:
    venue = ShadowVenue(fee_rate=cfg.costs.fee_rate, book=lambda s: cap.books.get(s))
    rt = TradingRuntime(cfg, store=tmp(name), venue=venue, decider=decider, metas=metas, eligible=eligible, universe_version="drill",
                        run_id=f"drill-{name}", code=__import__("wallex_agent.runtime.provenance", fromlist=["x"]).current_code_version(),
                        dataset_id=cap.source, data_source="WALLEX_REST", competition_override=competition)
    rt.strategy = DrillCandidates(rt.strategy.version)  # type: ignore[assignment]
    rt.start(cap.at, dependency_lock_sha256="drill")
    for s, b in cap.books.items():
        if s in rt.symbols:
            rt.update_market(s, mark=None, book=b)
    return rt


def run_drills(base: RunConfig, cap: MarketCapture, *, metas: dict[str, Any], eligible: frozenset[str],
               store_factory: Callable[[str], JournalStore], ninerouter_timeout: Callable[[DecisionContext], tuple[ValidatedDecision, DecisionProvenance]] | None = None) -> list[DrillResult]:
    out: list[DrillResult] = []
    sym = next(iter(cap.books))
    now = cap.at
    bars = cap.bars[sym]

    def submissions(rt: TradingRuntime) -> int:
        return len(rt.venue.submitted)  # type: ignore[attr-defined]

    # 1. stale data -> DATA_UNUSABLE, no AI call, no order
    d = ScriptedDecider("baseline")
    rt = _runtime(_cfg(base), cap, d, store_factory, "stale", metas, eligible)
    rec = rt.cycle(sym, now, bars, flags=frozenset({QualityFlag.STALE}))
    out.append(DrillResult("stale_data", rec.status is CycleStatus.DATA_UNUSABLE and d.calls == 0 and submissions(rt) == 0,
                           {"status": rec.status.value, "block_reason": rec.block_reason, "ai_calls": d.calls}))

    # 2. NineRouter timeout -> WAIT (real NineRouter with a 1 s deadline when provided, else scripted)
    if ninerouter_timeout is not None:
        class _NR:
            source_name = "LLM"

            def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
                return ninerouter_timeout(ctx)  # type: ignore[misc]
        dec: DecisionProvider = _NR()
    else:
        dec = ScriptedDecider("timeout")
    rt = _runtime(_cfg(base), cap, dec, store_factory, "nr-timeout", metas, eligible)
    rec = rt.cycle(sym, now, bars, flags=frozenset())
    out.append(DrillResult("ninerouter_timeout", rec.status is CycleStatus.WAITED and submissions(rt) == 0 and rec.validated is not None
                           and rec.validated.status in (ValidationStatus.TIMEOUT, ValidationStatus.STALE, ValidationStatus.PROVIDER_ERROR),
                           {"status": rec.status.value, "validation": rec.validated.status.value if rec.validated else None,
                            "real_ninerouter": ninerouter_timeout is not None}))

    # 3. invalid AI output (invented candidate id) -> WAIT
    rt = _runtime(_cfg(base), cap, ScriptedDecider("invented"), store_factory, "invalid-ai", metas, eligible)
    rec = rt.cycle(sym, now, bars, flags=frozenset())
    out.append(DrillResult("invalid_ai_output", rec.status is CycleStatus.WAITED and submissions(rt) == 0,
                           {"status": rec.status.value, "validation": rec.validated.status.value if rec.validated else None}))

    # 4. simulator timeout (lost response) -> UNKNOWN -> reconciled, exactly one submission
    rt = _runtime(_cfg(base), cap, ScriptedDecider("baseline"), store_factory, "sim-timeout", metas, eligible)
    rt.venue.drop_next_response = True  # type: ignore[attr-defined]
    rec = rt.cycle(sym, now, bars, flags=frozenset())
    states = [e.state for e in rec.execution_events]
    out.append(DrillResult("execution_simulator_timeout", OrderState.UNKNOWN in states and submissions(rt) == 1
                           and not rt.execution.unresolved(),
                           {"states": [s.value for s in states], "submissions": submissions(rt), "unresolved": rt.execution.unresolved()}))

    # 5. duplicate submit attempt -> refused
    rt = _runtime(_cfg(base), cap, ScriptedDecider("baseline"), store_factory, "duplicate", metas, eligible)
    rec = rt.cycle(sym, now, bars, flags=frozenset())
    dup_refused = False
    if rec.order_intent is not None and rec.risk_verdict is not None:
        try:
            rt.execution.submit(rec.order_intent, rec.risk_verdict, now=now, plan=None)
        except DuplicateSubmit:
            dup_refused = True
    out.append(DrillResult("duplicate_submit", dup_refused and submissions(rt) == 1, {"submissions": submissions(rt)}))

    # 6. reservation / double-spend attempt: entries on two symbols in the same tick with cash for about one
    syms = [x for x in cap.books if x in cap.bars][:2]
    small = _cfg(base, **{"run.starting_capital_tmn": Decimal(1_000_000), "limits.risk.per_trade_budget_fraction": "0.5",
                          "limits.risk.max_position_notional_tmn": "900000", "limits.risk.max_total_exposure_tmn": "5000000",
                          "limits.risk.total_loss_budget_tmn": "500000", "limits.risk.session_loss_budget_tmn": "500000",
                          "run.symbols": tuple(syms)})
    rt = _runtime(small, cap, ScriptedDecider("baseline"), store_factory, "double-spend", metas, eligible)
    recs = [rt.cycle(x, now, cap.bars[x], flags=frozenset()) for x in syms]
    spent = sum((p.cost_tmn for p in rt.ledger.positions.values()), Decimal(0))
    out.append(DrillResult("double_spend", len(syms) == 2 and rt.ledger.spendable_tmn >= 0 and rt.ledger.cash_tmn >= 0
                           and spent <= small.run.starting_capital_tmn,
                           {"symbols": syms, "statuses": [r.status.value for r in recs], "spent_tmn": str(spent.quantize(Decimal(1))),
                            "cash_tmn": str(rt.ledger.cash_tmn.quantize(Decimal(1))),
                            "second_failed_checks": [c.name for c in recs[-1].risk_verdict.checks if not c.passed] if recs[-1].risk_verdict else [],
                            "second_qty": str(recs[-1].order_intent.qty) if recs[-1].order_intent else None}))

    # 7. trading-window closure: entries blocked with next open; open position flattened inside the flatten buffer
    local_close = (now + timedelta(minutes=10)).astimezone(UTC)
    tw = TradingWindowConfig(version="tw-drill", timezone="UTC", no_new_entry_before_close_s=1200, flatten_before_close_s=900,
                             windows=(WindowSpec(days=("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"),
                                                 start=(local_close - timedelta(hours=6)).strftime("%H:%M"), end=local_close.strftime("%H:%M")),))
    rt = _runtime(_cfg(base), cap, ScriptedDecider("baseline"), store_factory, "window-open", metas, eligible)
    rt.cycle(sym, now, bars, flags=frozenset())  # opens a position while the window is fully open
    had_position = sym in rt.ledger.positions
    rt.calendar = __import__("wallex_agent.runtime.calendar", fromlist=["TradingCalendar"]).TradingCalendar(tw, CompetitionConfig(), base.limits.cutoff)
    st: WindowStatus = rt.calendar.status(now)
    rec = rt.cycle(sym, now + timedelta(seconds=1), bars, flags=frozenset())
    rt.run_exits(now + timedelta(seconds=2), {sym: cap.books[sym]})
    out.append(DrillResult("trading_window_closure", had_position and rec.status is CycleStatus.BLOCKED and st.must_flatten
                           and sym not in rt.ledger.positions,
                           {"window_reason": st.reason, "blocked_reason": rec.block_reason,
                            "flattened": sym not in rt.ledger.positions, "trades": [t.exit_reason for t in rt.ledger.trades]}))

    # 8. kill switch: entries blocked, exits continue
    rt = _runtime(_cfg(base), cap, ScriptedDecider("baseline"), store_factory, "kill", metas, eligible)
    rt.cycle(sym, now, bars, flags=frozenset())
    rt.kill.latch("drill", at=now)
    rec = rt.cycle(sym, now + timedelta(seconds=1), bars, flags=frozenset())
    pos = rt.ledger.positions.get(sym)
    exit_action = None
    if pos is not None:
        st8 = rt.calendar.status(now)
        exit_action = rt.exits.exit_now(sym, OrderPurpose.KILL, cap.books[sym].bids[0][0], rt.risk_ctx(sym, now + timedelta(seconds=2), st8),
                                        rt.symbols[sym].meta, AutonomyMode.AUTONOMOUS)
    out.append(DrillResult("kill_switch", rec.status in (CycleStatus.RISK_REJECTED, CycleStatus.BLOCKED) and pos is not None
                           and exit_action is not None and exit_action.outcome == "EXECUTED",
                           {"entry_after_kill": rec.status.value, "exit_after_kill": exit_action.outcome if exit_action else None}))

    # 9. cutoff liquidation: competition end inside the liquidation buffer -> flat
    comp = CompetitionConfig(start_utc=now - timedelta(hours=1), end_utc=now + timedelta(hours=2))
    rt = _runtime(_cfg(base), cap, ScriptedDecider("baseline"), store_factory, "cutoff", metas, eligible, competition=comp)
    rt.cycle(sym, now, bars, flags=frozenset())
    opened = sym in rt.ledger.positions
    rt.calendar = __import__("wallex_agent.runtime.calendar", fromlist=["TradingCalendar"]).TradingCalendar(
        None, CompetitionConfig(start_utc=now - timedelta(hours=1), end_utc=now + timedelta(minutes=20)), base.limits.cutoff)
    rt.run_exits(now + timedelta(seconds=5), {sym: cap.books[sym]})
    out.append(DrillResult("cutoff_liquidation", opened and sym not in rt.ledger.positions
                           and any(t.exit_reason == "LIQUIDATION" for t in rt.ledger.trades),
                           {"opened": opened, "flat": sym not in rt.ledger.positions, "exits": [t.exit_reason for t in rt.ledger.trades]}))
    return out
