"""TradingRuntime: the decision cycle of docs/AGENT_WORKFLOW.md, identical for BACKTEST / SHADOW / LIVE.

Only the venue (fills), the decision provider (LLM / replay / baseline) and the market views passed in differ.
Every cycle that reaches the decision stage writes exactly one DecisionRecord, including WAIT, blocked and failed ones.
Every mutation of trade truth is appended to the run's state log; a crashed or stopped run is resumed only through
`wallex_agent.recovery.RecoveryCoordinator` (constructed with `resume=True`), never by starting over.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from wallex_agent.agent.context import DecisionContext, DecisionProvider, PositionView
from wallex_agent.agent.validator import validate
from wallex_agent.approval.service import ApprovalError, ApprovalService
from wallex_agent.contracts import (
    AgentAction,
    ApprovalStatus,
    ApprovalTicket,
    CandidateSummary,
    CodeVersion,
    CycleStatus,
    DataRef,
    DecisionRecord,
    Environment,
    ExecutionEvent,
    MarketMeta,
    MarketSnapshot,
    OrderIntent,
    OrderPurpose,
    OrderState,
    PolicyOutcome,
    QualityFlag,
    RiskOutcome,
    RiskVerdict,
    RunManifest,
    SignalCandidate,
    ValidatedDecision,
    ValidationStatus,
)
from wallex_agent.contracts.hashing import content_id
from wallex_agent.execution.engine import ExecutionEngine, OrderRecord
from wallex_agent.execution.exits import ExitAction, ExitCoordinator
from wallex_agent.execution.venue import Venue
from wallex_agent.features.engine import compute_features
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import Bar
from wallex_agent.market.book import OrderBook
from wallex_agent.market.snapshot import build_snapshot
from wallex_agent.policy import ActionKind, PolicyContext, decide
from wallex_agent.portfolio.ledger import ExitPlan, Ledger
from wallex_agent.recovery.rebuild import apply_risk_state, ledger_digest, risk_state
from wallex_agent.recovery.state_log import StateLog
from wallex_agent.risk.engine import IntentDraft, RiskContext, RiskEngine
from wallex_agent.risk.protections import Protections
from wallex_agent.strategies import BriefingStrategy, ExitSignalStrategy, build_strategy, strategy_history, strategy_lookback

from .calendar import TradingCalendar, WindowStatus
from .config import CompetitionConfig, RunConfig

if TYPE_CHECKING:
    from wallex_agent.recovery.coordinator import RecoveryReport

Reporter = Callable[[str, dict[str, Any]], None]
TERMINAL_STATUS = {OrderState.FILLED: CycleStatus.FILLED, OrderState.PARTIAL: CycleStatus.PARTIALLY_FILLED,
                   OrderState.UNKNOWN: CycleStatus.EXECUTION_UNKNOWN, OrderState.REJECTED: CycleStatus.EXECUTION_FAILED}


class RecoveryRequired(RuntimeError):
    """Persisted state exists for this run: it must be resumed through restart recovery, never silently reset."""


@dataclass
class SymbolState:
    meta: MarketMeta
    snapshot: MarketSnapshot | None = None
    book: OrderBook | None = None
    mark: Decimal | None = None
    candidates: dict[str, SignalCandidate] = field(default_factory=lambda: {})


class TradingRuntime:
    def __init__(self, cfg: RunConfig, *, store: JournalStore, venue: Venue, decider: DecisionProvider,
                 metas: dict[str, MarketMeta], eligible: frozenset[str], universe_version: str, run_id: str,
                 code: CodeVersion, dataset_id: str, data_source: Literal["WALLEX_REST", "WALLEX_MCP", "FIXTURE"],
                 competition_override: CompetitionConfig | None = None, reporter: Reporter | None = None,
                 scripted_approval: Literal["APPROVE_ALL", "REJECT_ALL"] | None = None,
                 model_meta: dict[str, str | None] | None = None, clock: Callable[[], datetime] | None = None,
                 resume: bool = False) -> None:
        if cfg.features is None or cfg.strategy is None:
            raise ValueError("features and strategy config are required to run")
        exists = store.manifest(run_id) is not None
        if exists and not resume:
            raise RecoveryRequired(f"run {run_id} already exists; resume it through restart recovery or use a new run id")
        if resume and not exists:
            raise RecoveryRequired(f"run {run_id} has no persisted state to resume")
        self.cfg = cfg
        self.env = cfg.run.environment
        self.autonomy = cfg.run.autonomy
        if venue.environment is not self.env:
            raise ValueError(f"venue {venue.environment} does not match run environment {self.env}")
        self.store, self.venue, self.decider = store, venue, decider
        self.run_id, self.code = run_id, code
        self.dataset_id = dataset_id
        self.data_source: Literal["WALLEX_REST", "WALLEX_MCP", "FIXTURE"] = data_source
        self.universe_version, self.eligible = universe_version, eligible
        self.reporter = reporter
        self.scripted_approval = scripted_approval
        self.model_meta = model_meta or {}
        self.clock = clock
        self.symbols: dict[str, SymbolState] = {s: SymbolState(metas[s]) for s in cfg.run.symbols if s in metas}
        self.ledger = Ledger(cfg.run.starting_capital_tmn)
        self.kill = store.kill_switch()
        self.prot = Protections(cfg.limits.risk)
        self.risk = RiskEngine(cfg.limits, cfg.costs, cfg.execution, self.ledger, self.kill, self.prot,
                               starting_capital_tmn=cfg.run.starting_capital_tmn)
        self.execution = ExecutionEngine(venue, self.ledger, self.prot, self.kill, metas=metas,
                                         max_consecutive_errors=cfg.limits.kill.max_consecutive_execution_errors,
                                         on_event=self._on_exec_event)
        self.exits = ExitCoordinator(self.risk, self.execution, pricing="BAR" if self.env is Environment.BACKTEST else "BOOK",
                                     exit_slippage_bps=cfg.execution.exit_limit_slippage_bps,
                                     live_autonomous_enabled=cfg.run.live_autonomous_enabled,
                                     exit_order_ttl_s=cfg.execution.exit_order_ttl_s)
        self.calendar = TradingCalendar(cfg.trading_window, competition_override or cfg.competition, cfg.limits.cutoff)
        self.approvals = ApprovalService(store, ttl_s=cfg.limits.approval.ticket_ttl_s or 0)
        self.strategy = build_strategy(cfg.strategy, cfg.costs, cfg.execution, cfg.features)
        self.lookback_bars = strategy_lookback(cfg.strategy, cfg.features)
        self.history_window = max(4 * max(cfg.features.atr_bars, cfg.features.trend_bars, cfg.features.volume_bars,
                                          self.lookback_bars) + 4, strategy_history(cfg.strategy, cfg.features) + 4)
        self._recorded_trades = 0
        self._reconcile_reported: set[str | None] = set()
        self._ai_invalid_streak = 0
        self._last_window_reason: str | None = None
        self.counters: dict[str, int] = {}
        self.last_decision: DecisionRecord | None = None
        self.started_at: datetime | None = None
        self.state_log = StateLog(store, run_id)
        self._log_attached = False
        self._last_risk_state: dict[str, Any] | None = None
        self.entry_gate: dict[str, str] = {}
        """Reasons new entries are blocked after a restart (e.g. an order still UNKNOWN); exits keep running."""
        self.recovery: RecoveryReport | None = None
        """The RecoveryReport of a resumed run."""
        self.balance_verifier: Callable[[datetime], bool] | None = None
        """Deferred exchange-balance reconciliation installed by recovery; must pass before entries reopen."""
        if not resume:
            self.attach_state_log()

    # ------------------------------------------------------------------------------------------------ state log
    def attach_state_log(self) -> None:
        """From now on every ledger/order mutation is persisted. A resumed run attaches only after its rebuild."""
        self.ledger.on_reservation = self.state_log.reservation
        self.execution.recorder = self.state_log
        self._log_attached = True
        self._last_risk_state = risk_state(self.ledger, self.prot, self._ai_invalid_streak)

    def install_recovered(self, orders: Iterable[OrderRecord], risk: dict[str, Any] | None, *, started_at: datetime) -> None:
        """Called once by the recovery coordinator after it replayed the state log into `ledger`/`prot`."""
        if self._log_attached:
            raise RuntimeError("recovered state must be installed before the runtime journals anything")
        for rec in orders:
            self.execution.adopt(rec)
        if risk is not None:
            self._ai_invalid_streak = apply_risk_state(risk, self.ledger, self.prot)
        # Trades rebuilt from fills persisted just before a crash may never have reached the trades table.
        self._recorded_trades = min(len(self.store.trades(self.run_id)), len(self.ledger.trades))
        self.started_at = started_at
        self.attach_state_log()

    def _checkpoint_risk(self, now: datetime) -> None:
        if not self._log_attached:
            return
        state = risk_state(self.ledger, self.prot, self._ai_invalid_streak)
        if state != self._last_risk_state:
            self.state_log.append("RISK", state, now)
            self._last_risk_state = state

    def checkpoint(self, reason: str, now: datetime) -> None:
        """Ledger digest for recovery to verify against (e.g. on a clean shutdown)."""
        if self._log_attached:
            self._checkpoint_risk(now)
            self.state_log.append("CHECKPOINT", {"reason": reason, "digest": ledger_digest(self.ledger)}, now)

    def _refresh_entry_gate(self, now: datetime) -> None:
        cleared = [k for k in self.entry_gate if k.startswith("order:")
                   and self.execution.order(k.removeprefix("order:")).state is not OrderState.UNKNOWN]
        for k in cleared:
            del self.entry_gate[k]
            self._risk_event(now, "RECOVERY_ORDER_RECONCILED", {"client_order_id": k.removeprefix("order:"),
                                                                "state": self.execution.order(k.removeprefix("order:")).state.value})
        if (cleared and "BALANCE_RECONCILIATION_PENDING" in self.entry_gate and self.balance_verifier is not None
                and not any(k.startswith("order:") for k in self.entry_gate)):
            verifier, self.balance_verifier = self.balance_verifier, None  # one attempt; a failure needs the operator
            if verifier(now):
                del self.entry_gate["BALANCE_RECONCILIATION_PENDING"]
        if cleared and not self.entry_gate:
            self._risk_event(now, "RECOVERY_RECONCILED", {"effect": "new entries allowed again (subject to risk)"})

    # ------------------------------------------------------------------------------------------------ plumbing
    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        if self.reporter:
            self.reporter(kind, data)

    def _on_exec_event(self, ev: ExecutionEvent, rec: OrderRecord) -> None:
        self.store.record_event(self.run_id, ev, {"symbol": rec.intent.symbol, "side": rec.intent.side,
                                                  "purpose": rec.intent.purpose.value, "qty": str(rec.intent.qty),
                                                  "limit_price": str(rec.intent.limit_price)})

    def _flush_trades(self) -> None:
        for trade in self.ledger.trades[self._recorded_trades:]:
            self.store.record_trade(self.run_id, trade)
            self._emit("TRADE_CLOSED", {"symbol": trade.symbol, "pnl": str(trade.net_pnl_tmn.quantize(Decimal(1))),
                                        "reason": trade.exit_reason, "at": trade.closed_at.isoformat()})
        self._recorded_trades = len(self.ledger.trades)

    def _session_key(self, now: datetime) -> str:
        tz = ZoneInfo(self.cfg.trading_window.timezone) if self.cfg.trading_window else None
        return (now.astimezone(tz) if tz else now).date().isoformat()

    def marks(self) -> dict[str, Decimal]:
        return {s: st.mark for s, st in self.symbols.items() if st.mark is not None}

    def risk_ctx(self, symbol: str, now: datetime, window: WindowStatus) -> RiskContext:
        st = self.symbols[symbol]
        assert st.snapshot is not None
        return RiskContext(now=now, snapshot=st.snapshot, book=st.book, marks=self.marks(), eligible=self.eligible, window=window,
                           require_book=self.env is not Environment.BACKTEST, environment=self.env,
                           session_key=self._session_key(now))

    def _risk_event(self, now: datetime, kind: str, data: dict[str, Any]) -> None:
        self.store.record_risk_event(self.run_id, now, kind, data)
        self._emit(kind, data)

    def _reject_event(self, now: datetime, symbol: str, verdict: RiskVerdict, what: str) -> None:
        self._risk_event(now, "RISK_REJECTED", {"symbol": symbol, "what": what,
                                                 "failed_checks": [f"{c.name}: {c.detail}" for c in verdict.checks if not c.passed]})

    # ------------------------------------------------------------------------------------------------ lifecycle
    def start(self, now: datetime, *, dependency_lock_sha256: str, dataset_refs: tuple[DataRef, ...] = ()) -> RunManifest:
        self.started_at = now
        tw = self.cfg.trading_window
        manifest = RunManifest(run_id=self.run_id, started_at=now, environment=self.env, autonomy=self.autonomy, code=self.code,
                               config_sha256=self.cfg.config_sha256, limits_version=self.cfg.limits.version,
                               prompt_bundle_sha256=self.model_meta.get("prompt_sha256"), model_id=self.model_meta.get("model_id"),
                               model_provider=self.model_meta.get("provider"), model_endpoint=self.model_meta.get("endpoint"),
                               model_settings_sha256=self.model_meta.get("settings_sha256"),
                               dependency_lock_sha256=dependency_lock_sha256, trading_window_version=tw.version if tw else None,
                               starting_capital_tmn=str(self.cfg.run.starting_capital_tmn), universe_version=self.universe_version,
                               dataset_refs=dataset_refs, clock="VIRTUAL" if self.env is Environment.BACKTEST else "WALL",
                               seed=self.cfg.run.seed, strategy_versions=(self.strategy.version,))
        self.store.record_run(manifest)
        self.store.put_ref(self.run_id, "run_config", self.cfg.model_dump(mode="json"))
        self._emit("RUN_STARTED", {"run_id": self.run_id, "environment": self.env.value, "autonomy": self.autonomy.value,
                                   "decider": self.decider.source_name, "symbols": list(self.symbols)})
        return manifest

    def update_market(self, symbol: str, *, mark: Decimal | None, book: OrderBook | None) -> None:
        st = self.symbols[symbol]
        if mark is not None:
            st.mark = mark
        if book is not None:
            st.book = book
            if book.bids:
                st.mark = book.bids[0][0]

    def mark_equity(self, now: datetime, *, record: bool) -> Decimal:
        eq = self.ledger.mark(self.marks(), now, fee_rate=self.cfg.costs.fee_rate)
        if record:
            self.store.record_equity(self.run_id, now, str(eq.quantize(Decimal(1))))
        self._checkpoint_risk(now)
        return eq

    # ------------------------------------------------------------------------------------------------ exits
    def run_exits(self, now: datetime, views: dict[str, Bar | OrderBook]) -> list[ExitAction]:
        window = self.calendar.status(now)
        if window.competition_state == "BEFORE":
            return []
        views = {s: v for s, v in views.items() if self.symbols[s].snapshot is not None}
        actions = self.exits.run(now=now, autonomy=self.autonomy, views=views, ctx_for=lambda s: self.risk_ctx(s, now, window),
                                 metas={s: st.meta for s, st in self.symbols.items()}, must_flatten=window.must_flatten)
        for a in actions:
            if a.outcome == "RISK_REJECTED" and a.verdict is not None:
                self._reject_event(now, a.symbol, a.verdict, f"exit {a.purpose.value}")
            elif a.outcome == "EXECUTED":
                self._emit("EXIT_SUBMITTED", {"symbol": a.symbol, "purpose": a.purpose.value,
                                              "state": a.events[-1].state.value if a.events else None})
            elif a.outcome == "CANCEL_REQUESTED":
                self._risk_event(now, "EXIT_ORDER_CANCEL_REQUESTED", {
                    "symbol": a.symbol, "purpose": a.purpose.value, "reason": a.events[0].exchange_status_raw if a.events else None,
                    "state": a.events[-1].state.value if a.events else None})
            elif a.outcome == "INVARIANT_BROKEN":
                self._risk_event(now, "EXIT_STATE_INVARIANT_BROKEN", {"symbol": a.symbol, "kill_latched": self.kill.latched})
            elif a.outcome == "RECONCILE_REQUIRED":
                pos = self.ledger.positions.get(a.symbol)
                cid = pos.exit_order_id if pos is not None else None
                if cid not in self._reconcile_reported:  # once per order: protection is degraded until reconciled
                    self._reconcile_reported.add(cid)
                    self._risk_event(now, "EXIT_RECONCILE_REQUIRED", {"symbol": a.symbol, "client_order_id": cid,
                                                                      "purpose": a.purpose.value})
            else:
                self._emit("EXIT_PROPOSED", {"symbol": a.symbol, "purpose": a.purpose.value})
        self._flush_trades()
        return actions

    def poll(self, now: datetime) -> None:
        self.execution.poll(now=now)
        self._flush_trades()
        if self.entry_gate:
            self._refresh_entry_gate(now)
        for t in self.approvals.expire_stale(now, run_id=self.run_id):
            self._release_ticket(t)

    def _release_ticket(self, ticket: ApprovalTicket) -> None:
        found = self.store.ticket(ticket.ticket_id, run_id=self.run_id)
        if found:
            intent = OrderIntent.model_validate(found[1]["intent"])
            self.risk.release_entry(intent)

    # ------------------------------------------------------------------------------------------------ the cycle
    def _record(self, **kw: Any) -> DecisionRecord:
        rec = DecisionRecord(run_id=self.run_id, environment=self.env, autonomy=self.autonomy, config_sha256=self.cfg.config_sha256,
                             code=self.code, **kw)
        self.store.record_decision(rec)
        self.last_decision = rec
        self._checkpoint_risk(rec.recorded_at)
        self._emit("DECISION", {"symbol": rec.symbol, "status": rec.status.value, "action": rec.effective_action.value,
                                "block_reason": rec.block_reason, "decision_id": rec.decision_id,
                                "rationale": rec.validated.decision.brief_rationale if rec.validated and rec.validated.decision else None,
                                "at": rec.recorded_at.isoformat(),
                                "next_entry_at": rec.next_entry_at.isoformat() if rec.next_entry_at else None})
        return rec

    def _positions_view(self) -> tuple[PositionView, ...]:
        out: list[PositionView] = []
        marks = self.marks()
        for s, p in self.ledger.positions.items():
            mark = marks.get(s)
            unreal = (p.qty * mark * (1 - self.cfg.costs.fee_rate) - p.cost_tmn) if mark is not None else Decimal(0)
            out.append(PositionView(p.position_id, s, str(p.qty), str((p.cost_tmn / p.qty).quantize(Decimal(1))) if p.qty else "0",
                                    str(p.plan.stop_price) if p.plan else "", str(p.plan.take_profit_price) if p.plan else None,
                                    p.plan.deadline.isoformat() if p.plan else "", str(unreal.quantize(Decimal(1)))))
        return tuple(out)

    def risk_status(self, now: datetime) -> dict[str, Any]:
        fee = self.cfg.costs.fee_rate
        eq = self.ledger.equity(self.marks(), fee)
        peak = max(self.ledger.peak_equity_tmn, eq)
        w = self.calendar.status(now)
        return {"equity_tmn": str(eq.quantize(Decimal(1))), "cash_tmn": str(self.ledger.cash_tmn.quantize(Decimal(1))),
                "realized_pnl_tmn": str(self.ledger.realized_pnl_tmn.quantize(Decimal(1))),
                "drawdown_pct": str(((peak - eq) / peak * 100).quantize(Decimal("0.01"))) if peak else "0",
                "max_drawdown_pct": str((self.ledger.max_drawdown_fraction * 100).quantize(Decimal("0.01"))),
                "exposure_tmn": str(self.ledger.exposure_tmn(self.marks()).quantize(Decimal(1))),
                "open_positions": len(self.ledger.positions), "kill_switch": self.kill.latched,
                "entries_allowed": w.entries_allowed, "window_reason": w.reason,
                "next_open": w.next_open.isoformat() if w.next_open else None,
                "window_close": w.window_close.isoformat() if w.window_close else None}

    def status_summary(self, now: datetime, *, active: bool, ninerouter: str | None = None) -> dict[str, Any]:
        """What the owner sees while the bot runs (`wallex-agent status` and periodic SUMMARY lines)."""
        rs = self.risk_status(now)
        eq = Decimal(rs["equity_tmn"])
        cap = self.cfg.run.starting_capital_tmn
        w = self.calendar.status(now)
        last = self.last_decision
        boundary = w.window_close or w.next_open
        return {**rs, "active": active, "run_id": self.run_id, "environment": self.env.value, "autonomy": self.autonomy.value,
                "decider": self.decider.source_name, "updated_at": now.isoformat(),
                "return_pct": str(((eq - cap) / cap * 100).quantize(Decimal("0.01"))),
                "realized_return_pct": str((self.ledger.realized_pnl_tmn / cap * 100).quantize(Decimal("0.01"))),
                "fees_tmn": str(self.ledger.fees_paid_tmn.quantize(Decimal(1))), "trades": len(self.ledger.trades),
                "positions": [p.__dict__ for p in self._positions_view()],
                "pending_approvals": [t.ticket_id for t in self.store.tickets(ApprovalStatus.PENDING, run_id=self.run_id)],
                "ninerouter": ninerouter, "next_boundary": boundary.isoformat() if boundary else None,
                "competition_state": w.competition_state,
                "last_decision": None if last is None else {
                    "at": last.recorded_at.isoformat(), "symbol": last.symbol, "status": last.status.value,
                    "action": last.effective_action.value, "block_reason": last.block_reason,
                    "rationale": last.validated.decision.brief_rationale if last.validated and last.validated.decision else None,
                    "reasons": [r.value for r in last.validated.decision.reason_codes] if last.validated and last.validated.decision else [],
                    "risk_failed": [c.name for c in last.risk_verdict.checks if not c.passed] if last.risk_verdict else []},
                "execution_errors": self.execution.error_count, "unresolved_orders": self.execution.unresolved(),
                "entry_gate": dict(self.entry_gate),
                "recovery_outcome": self.recovery.outcome.value if self.recovery is not None else None}

    def _recent(self, symbol: str) -> tuple[dict[str, Any], ...]:
        recs = [r for r in self.store.decisions(self.run_id, limit=12, newest_first=True) if r.symbol == symbol][:5]
        return tuple({"at": r.recorded_at.isoformat(), "status": r.status.value, "action": r.effective_action.value,
                      "reasons": [c.value for c in r.validated.decision.reason_codes] if r.validated and r.validated.decision else []}
                     for r in recs)

    def _snapshot(self, symbol: str, as_of: datetime, hist: list[Bar], flags: frozenset[QualityFlag]) -> MarketSnapshot:
        st = self.symbols[symbol]
        book_top = st.book.top() if st.book is not None and st.book.bids and st.book.asks else None
        all_flags: set[QualityFlag] = set(flags) | (set(st.book.flags) if st.book is not None else set[QualityFlag]())
        if symbol not in self.eligible:
            all_flags.add(QualityFlag.SYMBOL_NOT_ELIGIBLE)
        return build_snapshot(symbol=symbol, as_of=as_of, bar_interval_seconds=self.cfg.run.bar_interval_seconds, bars=hist,
                              book=book_top, meta=st.meta, universe_version=self.universe_version, dataset_id=self.dataset_id,
                              source=self.data_source, flags=frozenset(all_flags))

    def prime_snapshot(self, symbol: str, as_of: datetime, bars: list[Bar], *, flags: frozenset[QualityFlag]) -> bool:
        """Give the exit watcher a market view without running a decision (after a restart, before the next bar).
        No candidates are produced, so no entry can follow from it."""
        hist = bars[-self.history_window:]
        if not hist:
            return False
        self.symbols[symbol].snapshot = self._snapshot(symbol, as_of, hist, flags)
        self.symbols[symbol].candidates = {}
        return True

    def cycle(self, symbol: str, as_of: datetime, bars: list[Bar], *, flags: frozenset[QualityFlag]) -> DecisionRecord:
        st = self.symbols[symbol]
        now = self.clock() if self.clock is not None else as_of
        did = "dec-" + content_id({"r": self.run_id, "s": symbol, "t": as_of})[:20]
        base: dict[str, Any] = {"decision_id": did, "cycle_id": f"{symbol}@{as_of.isoformat()}", "recorded_at": now,
                                "symbol": symbol}
        window = self.calendar.status(now)
        hist = bars[-self.history_window:]
        if not hist:
            return self._record(**base, snapshot_id=None, data_refs=(), features_ref=None, deterministic_regime=None, candidates=(),
                                provenance=None, validated=None, effective_action=AgentAction.WAIT, policy_outcome=None,
                                risk_verdict=None, approval=None, order_intent=None, status=CycleStatus.DATA_UNUSABLE,
                                block_reason="NO_CLOSED_BARS")
        snap = self._snapshot(symbol, as_of, hist, flags)
        assert self.cfg.features is not None and self.cfg.strategy is not None
        fs = compute_features(symbol, snap.snapshot_id, hist, as_of, self.cfg.run.bar_interval_seconds, st.book, self.cfg.features,
                              self.lookback_bars)
        st.snapshot = snap
        cands = self.strategy.candidates(snap, fs) if not snap.quality_flags else []
        pos = self.ledger.positions.get(symbol)
        if (pos is not None and pos.plan is not None and not pos.plan.signal_exit and not snap.quality_flags
                and isinstance(self.strategy, ExitSignalStrategy) and self.strategy.exit_signal(snap, fs)):
            # Deterministic strategy exit: flags the plan; the exit coordinator sells via policy + risk (EXIT_SIGNAL).
            pos.plan = replace(pos.plan, signal_exit=True)
            self._risk_event(now, "EXIT_SIGNAL", {"symbol": symbol, "position_id": pos.position_id, "decision_id": did,
                                                  "strategy_version": self.strategy.version, "at": as_of.isoformat()})
        st.candidates = {c.candidate_id: c for c in cands}
        features_ref = self.store.put_ref(self.run_id, f"ctx:{did}", {"snapshot": snap.model_dump(mode="json"),
                                                                       "features": fs.model_dump(mode="json"),
                                                                       "candidates": [c.model_dump(mode="json") for c in cands]})
        common: dict[str, Any] = {**base, "snapshot_id": snap.snapshot_id, "data_refs": (snap.candles,), "features_ref": features_ref,
                                  "deterministic_regime": fs.deterministic_regime,
                                  "candidates": tuple(CandidateSummary(candidate_id=c.candidate_id, strategy_id=c.strategy_id,
                                                                       strategy_version=c.strategy_version, symbol=c.symbol) for c in cands)}
        empty: dict[str, Any] = {"provenance": None, "validated": None, "policy_outcome": None, "risk_verdict": None, "approval": None,
                                 "order_intent": None}

        if snap.quality_flags:
            return self._record(**common, **empty, effective_action=AgentAction.WAIT, status=CycleStatus.DATA_UNUSABLE,
                                block_reason="DATA_QUALITY:" + ",".join(sorted(f.value for f in snap.quality_flags)))
        if not window.entries_allowed:
            if window.reason != self._last_window_reason:
                self._risk_event(as_of, "ENTRIES_NOT_ALLOWED", {"reason": window.reason,
                                                                "next_open": window.next_open.isoformat() if window.next_open else None})
            self._last_window_reason = window.reason
            return self._record(**common, **empty, effective_action=AgentAction.WAIT, status=CycleStatus.BLOCKED,
                                block_reason=window.reason, next_entry_at=window.next_open)
        self._last_window_reason = None
        if self.entry_gate:
            return self._record(**common, **empty, effective_action=AgentAction.WAIT, status=CycleStatus.BLOCKED,
                                block_reason="RECOVERY_RECONCILIATION_PENDING:" + ",".join(sorted(self.entry_gate)))
        if not cands:
            return self._record(**common, **empty, effective_action=AgentAction.WAIT, status=CycleStatus.NO_CANDIDATES)

        deadline = now + timedelta(seconds=self.cfg.agent.decision_deadline_s)
        dctx = DecisionContext(snapshot=snap, features=fs, candidates=tuple(cands), positions=self._positions_view(),
                               risk_status=self.risk_status(now), recent_decisions=self._recent(symbol), now=now,
                               deadline=deadline, run_id=self.run_id, decision_key=f"{symbol}@{as_of.isoformat()}",
                               strategy_brief=(self.strategy.brief(symbol, self.cfg.run.bar_interval_seconds)
                                               if isinstance(self.strategy, BriefingStrategy) else None))
        validated, prov = self.decider.decide(dctx)
        now = self.clock() if self.clock is not None else as_of
        common["recorded_at"] = now
        if self.clock is not None and validated.status.answered:
            again = validate(validated.decision, dctx, received_at=now, exit_enabled=self.cfg.agent.allow_exit_position,
                             role=self.cfg.agent.role)
            if validated.status is ValidationStatus.VALID or not again.status.answered:
                validated = again
        self._track_ai(now, validated)
        common |= {"provenance": prov, "validated": validated}
        if validated.effective_action is AgentAction.WAIT:
            return self._record(**common, effective_action=AgentAction.WAIT, policy_outcome=None, risk_verdict=None, approval=None,
                                order_intent=None, status=CycleStatus.WAITED)
        window = self.calendar.status(now)
        if validated.effective_action is AgentAction.EXIT_POSITION:
            return self._ai_exit(common, validated, now, window)
        if not window.entries_allowed:
            return self._record(**common, effective_action=AgentAction.WAIT, policy_outcome=PolicyOutcome.BLOCKED,
                                risk_verdict=None, approval=None, order_intent=None, status=CycleStatus.BLOCKED,
                                block_reason=window.reason, next_entry_at=window.next_open)
        return self._enter(common, validated, now, window)

    def _track_ai(self, now: datetime, validated: ValidatedDecision) -> None:
        if validated.status.answered:
            self._ai_invalid_streak = 0
            if validated.status is ValidationStatus.VETO_UNSUPPORTED:
                self._risk_event(now, "AI_VETO_UNSUPPORTED", {"detail": validated.detail})
            return
        self._ai_invalid_streak += 1
        self._risk_event(now, "AI_DECISION_INVALID", {"status": validated.status.value, "detail": validated.detail})
        if self._ai_invalid_streak == self.cfg.agent.invalid_streak_breaker:
            self._risk_event(now, "AI_BREAKER", {"streak": self._ai_invalid_streak,
                                                 "effect": "entries keep WAITing; exits unaffected"})

    def _enter(self, common: dict[str, Any], validated: ValidatedDecision, now: datetime, window: WindowStatus) -> DecisionRecord:
        assert validated.decision is not None and validated.decision.selected_candidate_id is not None
        symbol = common["symbol"]
        cand = self.symbols[symbol].candidates[validated.decision.selected_candidate_id]
        pol = decide(ActionKind.AI_ENTRY, PolicyContext(self.autonomy, self.env, self.kill.latched, self.cfg.run.live_autonomous_enabled))
        draft = IntentDraft(decision_id=common["decision_id"], candidate=cand, autonomy=self.autonomy, environment=self.env,
                            posture=validated.decision.risk_posture)
        rec: dict[str, Any] = {**common, "effective_action": AgentAction.ENTER, "policy_outcome": pol.outcome, "approval": None}
        if pol.outcome is PolicyOutcome.BLOCKED:
            return self._record(**rec, risk_verdict=None, order_intent=None, status=CycleStatus.BLOCKED, block_reason=pol.reason)
        verdict, intent = self.risk.evaluate_entry(draft, self.risk_ctx(symbol, now, window))
        if intent is None or verdict.outcome is RiskOutcome.REJECT:
            self._reject_event(now, symbol, verdict, "entry")
            return self._record(**rec, risk_verdict=verdict, order_intent=None, status=CycleStatus.RISK_REJECTED)
        plan = ExitPlan(cand.candidate_id, cand.stop_price, cand.take_profit_price, now + timedelta(seconds=cand.max_holding_seconds),
                        cand.trail_distance)
        if pol.outcome is PolicyOutcome.PROPOSE_ONLY:
            self.risk.release_entry(intent)
            self._emit("PROPOSAL", {"symbol": symbol, "qty": str(intent.qty), "limit": str(intent.limit_price),
                                    "stop": str(cand.stop_price), "tp": str(cand.take_profit_price),
                                    "trail": None if cand.trail_distance is None else str(cand.trail_distance)})
            return self._record(**rec, risk_verdict=verdict, order_intent=intent, status=CycleStatus.PROPOSED)
        if pol.outcome is PolicyOutcome.REQUIRE_APPROVAL:
            bundle = {"intent": intent.model_dump(mode="json"), "candidate": cand.model_dump(mode="json"),
                      "plan": {"stop": str(plan.stop_price), "tp": str(plan.take_profit_price), "deadline": plan.deadline.isoformat(),
                               **({} if plan.trail_distance is None else {"trail": str(plan.trail_distance)})},
                      "decision_id": common["decision_id"], "strategy_version": cand.strategy_version}
            ticket = self.approvals.open(self.run_id, intent, bundle, now)
            self._emit("APPROVAL_REQUESTED", {"ticket_id": ticket.ticket_id, "symbol": symbol, "qty": str(intent.qty),
                                              "limit": str(intent.limit_price), "stop": str(cand.stop_price),
                                              "tp": str(cand.take_profit_price), "expires_at": ticket.expires_at.isoformat(),
                                              "trail": None if cand.trail_distance is None else str(cand.trail_distance)})
            out = self._record(**{**rec, "approval": ticket}, risk_verdict=verdict, order_intent=intent,
                               status=CycleStatus.AWAITING_APPROVAL)
            if self.scripted_approval is not None:
                self.approvals.decide(ticket.ticket_id, approve=self.scripted_approval == "APPROVE_ALL", operator="backtest-script", now=now)
                self.process_approvals(now)
            return out
        events = self.execution.submit(intent, verdict, now=now, plan=plan, strategy_version=cand.strategy_version)
        self._flush_trades()
        final = events[-1].state if events else OrderState.SUBMITTING
        status = TERMINAL_STATUS.get(final, CycleStatus.SUBMITTED)
        if final in (OrderState.CANCELED, OrderState.EXPIRED) and self.execution.order(intent.client_order_id).filled_qty == 0:
            status = CycleStatus.EXECUTION_FAILED
        self._emit("ENTRY_SUBMITTED", {"symbol": symbol, "qty": str(intent.qty), "limit": str(intent.limit_price), "state": final.value})
        return self._record(**rec, risk_verdict=verdict, order_intent=intent, execution_events=tuple(events), status=status)

    def _ai_exit(self, common: dict[str, Any], validated: ValidatedDecision, now: datetime, window: WindowStatus) -> DecisionRecord:
        assert validated.decision is not None
        symbol = common["symbol"]
        pos = next((p for p in self.ledger.positions.values() if p.position_id == validated.decision.position_id), None)
        rec: dict[str, Any] = {**common, "effective_action": AgentAction.EXIT_POSITION, "approval": None, "order_intent": None}
        st = self.symbols.get(pos.symbol if pos else symbol)
        if pos is None or st is None or st.mark is None:
            return self._record(**rec, policy_outcome=None, risk_verdict=None, status=CycleStatus.BLOCKED, block_reason="POSITION_GONE")
        action = self.exits.exit_now(pos.symbol, OrderPurpose.EXIT_AI, st.mark, self.risk_ctx(pos.symbol, now, window), st.meta,
                                     self.autonomy)
        if action is None:
            return self._record(**rec, policy_outcome=None, risk_verdict=None, status=CycleStatus.BLOCKED, block_reason="EXIT_PENDING")
        status = {"EXECUTED": CycleStatus.SUBMITTED, "PROPOSED": CycleStatus.PROPOSED, "RISK_REJECTED": CycleStatus.RISK_REJECTED}[action.outcome]
        self._flush_trades()
        return self._record(**rec, policy_outcome=None, risk_verdict=action.verdict, execution_events=tuple(action.events), status=status)

    # ------------------------------------------------------------------------------------------------ approvals
    def process_approvals(self, now: datetime) -> list[str]:
        """Execute tickets the operator APPROVED: consume once, re-run risk on fresh state, then submit."""
        done: list[str] = []
        for t in self.store.tickets(ApprovalStatus.REJECTED, run_id=self.run_id):
            self._release_ticket(t)  # idempotent
        for t in self.store.tickets(ApprovalStatus.APPROVED, run_id=self.run_id):
            found = self.store.ticket(t.ticket_id, run_id=self.run_id)
            if found is None:
                continue
            bundle = found[1]
            intent = OrderIntent.model_validate(bundle["intent"])
            cand = SignalCandidate.model_validate(bundle["candidate"])
            try:
                self.approvals.consume(t.ticket_id, intent_hash=intent.intent_hash, environment=self.env, now=now,
                                       run_id=self.run_id)
            except ApprovalError as exc:
                self._risk_event(now, "APPROVAL_REFUSED", {"ticket_id": t.ticket_id, "reason": str(exc)})
                self.risk.release_entry(intent)
                continue
            window = self.calendar.status(now)
            verdict = self.risk.recheck(intent, cand, self.risk_ctx(intent.symbol, now, window))
            if verdict.outcome is RiskOutcome.REJECT:
                self._reject_event(now, intent.symbol, verdict, f"approved ticket {t.ticket_id} (recheck)")
                continue
            plan = ExitPlan(cand.candidate_id, cand.stop_price, cand.take_profit_price,
                            now + timedelta(seconds=cand.max_holding_seconds), cand.trail_distance)
            events = self.execution.submit(intent, verdict, now=now, plan=plan, strategy_version=cand.strategy_version)
            self._emit("APPROVED_ENTRY_SUBMITTED", {"ticket_id": t.ticket_id, "symbol": intent.symbol,
                                                    "state": events[-1].state.value if events else None})
            done.append(t.ticket_id)
        self._flush_trades()
        self._checkpoint_risk(now)
        return done
