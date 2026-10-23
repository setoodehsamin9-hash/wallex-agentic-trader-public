"""Restart recovery coordinator: one deterministic state machine that turns persisted state into a runnable runtime.

    LOAD -> VALIDATE -> REBUILD -> CLASSIFY -> RECONCILE -> APPROVALS -> RESERVATIONS -> REARM -> SCHEDULE -> VERIFY
         -> READY | EXITS_ONLY | BLOCKED

- LOAD: the run exists, is not sealed, and its hash-chained state log is intact (else BLOCKED, kill latched).
- VALIDATE: schema, environment, autonomy, config/limits/window hashes, capital, code commit, strategy and model
  identity equal the persisted manifest (else BLOCKED: start a new run; nothing is migrated or guessed).
- REBUILD: replay the state log into the runtime's fresh ledger/protections; adopt orders (duplicate refusal keeps
  covering them); restore risk memory (peak equity, pauses, session baselines, AI breaker streak). Only then is the
  state log re-attached, so recovery's own actions are appended, never rewritten.
- CLASSIFY: every non-terminal order is ambiguous: its venue outcome after the last persisted fact is unknown, so it
  is marked UNKNOWN (no "no response -> submit again", ever).
- RECONCILE: simulated venues keep no state outside this process, so the journal is complete: settle FILLED /
  CANCELED from persisted fills. A real exchange is queried by client_order_id; anything not found stays UNKNOWN and
  gates new entries until a later poll resolves it. Exchange balances must then match the ledger within
  `kill.reconciliation_tolerance_tmn` (mismatch -> BLOCKED + kill latch, ADR-002); without a balance source new
  entries stay gated.
- APPROVALS: unconsumed tickets (PENDING/APPROVED) are invalidated (EXPIRED with a note). A human approval is never
  carried across a restart; the algorithm must propose again and risk re-evaluates on fresh state.
- RESERVATIONS: keep exactly the reservations of non-terminal entry orders; release the rest (journaled).
- REARM: every open position must have its exit plan and a watched symbol; otherwise the kill switch latches.
  A pending (incl. UNKNOWN) exit stays the position's single exit: no second sell is sent.
- SCHEDULE / VERIFY: report the trading-window/cutoff state; check ledger invariants.

No model, no agent, no network beyond the venue's own lookup. Each step appends RECOVERY_* risk events; the full
report is stored as a payload ref `recovery:<time>` and on `runtime.recovery`.
"""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from wallex_agent.contracts import (
    SCHEMA_VERSION,
    TERMINAL_ORDER_STATES,
    ApprovalStatus,
    AutonomyMode,
    Environment,
    OrderPurpose,
    OrderState,
    RunManifest,
)
from wallex_agent.contracts.base import Contract, UtcDatetime
from wallex_agent.execution.engine import SIMULATED_SOURCES, awaiting_cancel_confirmation
from wallex_agent.journal.store import JournalStore
from wallex_agent.portfolio.ledger import Ledger, reservation_id
from wallex_agent.risk.protections import Protections
from wallex_agent.runtime.config import RunConfig
from wallex_agent.runtime.engine import TradingRuntime

from .rebuild import RebuildError, Rebuilt, apply_risk_state, ledger_digest, rebuild
from .state_log import StateLog, StateLogCorrupt

Balances = Callable[[], dict[str, Decimal]]
"""asset -> total (available + locked) on the exchange account; LIVE only."""


class RecoveryOutcome(StrEnum):
    READY = "READY"
    EXITS_ONLY = "EXITS_ONLY"
    """Runtime may run: exits, reconciliation and liquidation continue; NEW ENTRIES are gated until resolved."""
    BLOCKED = "BLOCKED"
    """Do not run this state. A new run (or an explicit, reviewed migration) is required."""


class RecoveryPhase(StrEnum):
    LOAD = "LOAD"
    VALIDATE = "VALIDATE"
    REBUILD = "REBUILD"
    CLASSIFY = "CLASSIFY"
    RECONCILE = "RECONCILE"
    APPROVALS = "APPROVALS"
    RESERVATIONS = "RESERVATIONS"
    REARM = "REARM"
    SCHEDULE = "SCHEDULE"
    VERIFY = "VERIFY"
    DONE = "DONE"


class RearmedExit(Contract):
    symbol: str
    position_id: str
    qty: str
    stop_price: str
    take_profit_price: str | None
    deadline: UtcDatetime
    overdue: bool
    pending_exit: str | None
    """client_order_id of the position's single pending exit (possibly UNKNOWN); no other sell is sent meanwhile."""


class RecoveryReport(Contract):
    run_id: str
    at: UtcDatetime
    environment: Environment
    autonomy: AutonomyMode
    outcome: RecoveryOutcome
    phase_reached: RecoveryPhase
    state_records: int = 0
    checkpoints_verified: int = 0
    cash_tmn: str | None = None
    known_orders: int = 0
    ambiguous_orders: tuple[str, ...] = ()
    settled_orders: tuple[str, ...] = ()
    """Simulated venue: outcome settled from persisted fills (FILLED / CANCELED remainder), never resubmitted."""
    reconciled_orders: tuple[str, ...] = ()
    """Exchange: state and fills taken from the venue's lookup by client_order_id."""
    unresolved_orders: tuple[str, ...] = ()
    reservations: dict[str, str] = {}
    released_reservations: tuple[str, ...] = ()
    invalidated_approvals: tuple[str, ...] = ()
    rearmed_exits: tuple[RearmedExit, ...] = ()
    kill_switch_latched: bool = False
    kill_reason: str | None = None
    schedule: dict[str, str | bool | None] = {}
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def summary_fa(self) -> str:
        """Owner-facing Persian summary (CLI)."""
        verdict = {RecoveryOutcome.READY: "آماده (ادامهٔ عادی)",
                   RecoveryOutcome.EXITS_ONLY: "فقط خروج/محافظت؛ ورود جدید تا روشن‌شدن وضعیت سفارش‌ها/موجودی ممنوع است",
                   RecoveryOutcome.BLOCKED: "متوقف (این وضعیت قابل ادامه نیست؛ اجرای جدید لازم است)"}[self.outcome]
        lines = [f"بازیابی اجرای {self.run_id} ({self.environment.value}/{self.autonomy.value}) در {self.at.isoformat()}: {verdict}",
                 f"رکوردهای وضعیت بازخوانی‌شده: {self.state_records} · نقطه‌های کنترل تأییدشده: {self.checkpoints_verified}"
                 f" · نقد: {self.cash_tmn or '—'} تومان",
                 f"سفارش‌های شناخته‌شده: {self.known_orders} · مبهم پس از ری‌استارت: {len(self.ambiguous_orders)}"
                 f" · تسویه از روی ژورنال (شبیه‌ساز): {len(self.settled_orders)} · تطبیق با صرافی: {len(self.reconciled_orders)}"
                 f" · هنوز نامعلوم (UNKNOWN، بدون ارسال دوباره): {list(self.unresolved_orders) or '—'}",
                 f"موقعیت‌های باز با حد ضرر/سود/مهلت دوباره مسلح‌شده: "
                 + (", ".join(f"{e.symbol} qty={e.qty} stop={e.stop_price} tp={e.take_profit_price or '—'}"
                              f"{' (خروج در انتظار ' + e.pending_exit + ')' if e.pending_exit else ''}" for e in self.rearmed_exits) or "—"),
                 f"رزروهای باقی‌مانده: {len(self.reservations)} · رزروهای آزادشده: {len(self.released_reservations)}"
                 f" · تأییدهای باطل‌شده: {list(self.invalidated_approvals) or '—'}",
                 f"کلید قطع اضطراری: {'فعال — ' + (self.kill_reason or '') if self.kill_switch_latched else 'غیرفعال'}"]
        if self.blockers:
            lines.append("موانع: " + " | ".join(self.blockers))
        if self.warnings:
            lines.append("هشدارها: " + " | ".join(self.warnings))
        return "\n".join(lines)


class RecoveryBlocked(RuntimeError):
    """Raised by runners when recovery ended BLOCKED; the trading loop must not start."""

    def __init__(self, report: RecoveryReport) -> None:
        super().__init__(f"restart recovery BLOCKED: {' | '.join(report.blockers)}")
        self.report = report


class _Hard(Exception):
    def __init__(self, reason: str, *, latch: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.latch = latch


def compatibility_problems(m: RunManifest, rt: TradingRuntime) -> list[str]:
    tw = rt.cfg.trading_window
    pairs: list[tuple[str, object, object]] = [
        ("schema_version", m.schema_version, SCHEMA_VERSION), ("environment", m.environment, rt.env),
        ("autonomy", m.autonomy, rt.autonomy), ("config_sha256", m.config_sha256, rt.cfg.config_sha256),
        ("limits_version", m.limits_version, rt.cfg.limits.version),
        ("trading_window_version", m.trading_window_version, tw.version if tw else None),
        ("starting_capital_tmn", m.starting_capital_tmn, str(rt.cfg.run.starting_capital_tmn)),
        ("code.git_commit", m.code.git_commit, rt.code.git_commit),
        ("strategy_versions", tuple(m.strategy_versions), (rt.strategy.version,)),
        ("model_id", m.model_id, rt.model_meta.get("model_id")),
        ("prompt_bundle_sha256", m.prompt_bundle_sha256, rt.model_meta.get("prompt_sha256"))]
    out = [f"{name} persisted={a} current={b}" for name, a, b in pairs if a != b]
    if rt.env is Environment.LIVE and (rt.code.dirty or m.code.dirty):
        out.append("code.dirty: LIVE resumes only clean, tagged code")
    return out


class RecoveryCoordinator:
    def __init__(self, rt: TradingRuntime, *, balances: Balances | None = None) -> None:
        self.rt = rt
        self.store: JournalStore = rt.store
        self.balances = balances
        self._phase = RecoveryPhase.LOAD
        self._f: dict[str, Any] = {}
        self._blockers: list[str] = []
        self._warnings: list[str] = []

    def _event(self, now: datetime, kind: str, data: dict[str, Any]) -> None:
        self.store.record_risk_event(self.rt.run_id, now, kind, data)

    def _enter(self, phase: RecoveryPhase) -> None:
        self._phase = phase

    def recover(self, now: datetime) -> RecoveryReport:
        rt = self.rt
        self._event(now, "RECOVERY_STARTED", {"run_id": rt.run_id, "environment": rt.env.value, "autonomy": rt.autonomy.value,
                                              "venue_source": rt.venue.fill_source.value})
        hard = False
        try:
            self._run(now)
            self._enter(RecoveryPhase.DONE)
        except _Hard as h:
            hard = True
            self._blockers.insert(0, h.reason)
            if h.latch:
                rt.kill.latch(f"restart recovery: {h.reason}"[:500], at=now, by="recovery")
        except Exception as exc:  # noqa: BLE001 - an unexpected failure is journaled and fails closed, never half-resumed
            hard = True
            self._blockers.insert(0, f"RECOVERY_ERROR in {self._phase.value}: {type(exc).__name__}: {exc}"[:500])
            rt.kill.latch(f"restart recovery: {self._blockers[0]}"[:500], at=now, by="recovery")
        outcome = RecoveryOutcome.BLOCKED if hard else RecoveryOutcome.EXITS_ONLY if rt.entry_gate else RecoveryOutcome.READY
        if outcome is RecoveryOutcome.BLOCKED:
            rt.entry_gate["RECOVERY_BLOCKED"] = self._blockers[0]
        kill = rt.kill.state
        report = RecoveryReport(run_id=rt.run_id, at=now, environment=rt.env, autonomy=rt.autonomy, outcome=outcome,
                                phase_reached=self._phase, kill_switch_latched=kill.latched, kill_reason=kill.reason,
                                blockers=tuple(self._blockers), warnings=tuple(self._warnings), **self._f)
        if outcome is not RecoveryOutcome.BLOCKED:
            rt.state_log.append("CHECKPOINT", {"reason": "RECOVERED", "outcome": outcome.value,
                                               "digest": ledger_digest(rt.ledger)}, now)
        self.store.put_ref(rt.run_id, f"recovery:{now.isoformat()}", report.model_dump(mode="json"))
        self._event(now, f"RECOVERY_{outcome.value}", {"phase": self._phase.value, "blockers": list(self._blockers),
                                                       "warnings": list(self._warnings), "entry_gate": dict(rt.entry_gate)})
        rt.recovery = report
        return report

    # ------------------------------------------------------------------------------------------------ phases
    def _run(self, now: datetime) -> None:
        rt, store = self.rt, self.store
        self._enter(RecoveryPhase.LOAD)
        manifest = store.manifest(rt.run_id)
        if manifest is None:
            raise _Hard(f"NO_RUN: {rt.run_id}", latch=False)
        ended = store.run_ended_at(rt.run_id)
        if ended is not None:
            raise _Hard(f"RUN_SEALED: run ended at {ended.isoformat()}; start a new run instead of reopening it", latch=False)
        try:
            records = rt.state_log.records()
        except StateLogCorrupt as exc:
            raise _Hard(f"STATE_LOG_CORRUPT: {exc}", latch=True) from exc
        if not records and store.events(rt.run_id):
            raise _Hard("NO_STATE_LOG: execution events exist but no state log; trade truth cannot be rebuilt", latch=True)

        self._enter(RecoveryPhase.VALIDATE)
        problems = compatibility_problems(manifest, rt)
        if problems:
            # Real positions/orders would be left unmanaged in LIVE: latch there. SHADOW/BACKTEST: operator choice
            # (start a new run after `wallex-agent abandon RUN_ID`).
            raise _Hard("INCOMPATIBLE: " + "; ".join(problems), latch=rt.env is Environment.LIVE)

        self._enter(RecoveryPhase.REBUILD)
        try:
            rebuilt = rebuild(records, ledger=rt.ledger, protections=rt.prot)
        except RebuildError as exc:
            raise _Hard(f"STATE_REBUILD_FAILED: {exc}", latch=True) from exc
        rt.install_recovered(rebuilt.orders.values(), rebuilt.risk_state, started_at=manifest.started_at)
        self._f |= {"state_records": rebuilt.records, "checkpoints_verified": rebuilt.checkpoints_verified,
                    "known_orders": len(rebuilt.orders)}
        self._event(now, "RECOVERY_STATE_RECONSTRUCTED", {
            "records": rebuilt.records, "orders": len(rebuilt.orders), "positions": sorted(rt.ledger.positions),
            "trades": len(rt.ledger.trades), "cash_tmn": str(rt.ledger.cash_tmn), "reserved_tmn": str(rt.ledger.reserved_tmn),
            "checkpoints_verified": rebuilt.checkpoints_verified})

        self._enter(RecoveryPhase.CLASSIFY)
        ambiguous = self._classify(rebuilt, now)

        self._enter(RecoveryPhase.RECONCILE)
        self._reconcile(ambiguous, now)

        self._enter(RecoveryPhase.APPROVALS)
        self._invalidate_approvals(now)

        self._enter(RecoveryPhase.RESERVATIONS)
        self._reservations()

        self._enter(RecoveryPhase.REARM)
        self._rearm(now)

        self._enter(RecoveryPhase.SCHEDULE)
        self._schedule(now)

        self._enter(RecoveryPhase.VERIFY)
        self._verify()

    def _classify(self, rebuilt: Rebuilt, now: datetime) -> tuple[str, ...]:
        rt = self.rt
        ambiguous = tuple(cid for cid, r in rebuilt.orders.items() if r.state not in TERMINAL_ORDER_STATES)
        details: list[dict[str, Any]] = []
        for cid in ambiguous:
            rec = rt.execution.order(cid)
            details.append({"client_order_id": cid, "symbol": rec.intent.symbol, "side": rec.intent.side,
                            "purpose": rec.intent.purpose.value, "last_state": rec.state.value, "filled": str(rec.filled_qty)})
            if rec.state is not OrderState.UNKNOWN:
                rt.execution.mark_unknown(cid, now=now, reason="RECOVERY: venue outcome after the last persisted fact is unknown")
        self._f["ambiguous_orders"] = ambiguous
        if ambiguous:
            self._event(now, "RECOVERY_AMBIGUITY_DETECTED", {"orders": details})
        return ambiguous

    def _reconcile(self, ambiguous: tuple[str, ...], now: datetime) -> None:
        rt = self.rt
        if rt.venue.fill_source in SIMULATED_SOURCES:
            held = tuple(r.intent.client_order_id for r in rt.execution.orders() if awaiting_cancel_confirmation(r))
            for cid in ambiguous + held:
                rt.execution.settle_simulated(cid, now=now)
            self._f["settled_orders"] = ambiguous
            rt.poll(now)
            return
        try:
            rt.poll(now)  # lookups by client_order_id; found fills are applied once (fill ids), nothing is resubmitted
        except Exception as exc:  # noqa: BLE001 - any lookup failure leaves the orders UNKNOWN (fail closed)
            self._warnings.append(f"RECONCILIATION_QUERY_FAILED: {type(exc).__name__}: {exc}")
        unresolved = tuple(cid for cid in ambiguous if rt.execution.order(cid).state is OrderState.UNKNOWN)
        self._f["reconciled_orders"] = tuple(cid for cid in ambiguous if cid not in unresolved)
        self._f["unresolved_orders"] = unresolved
        for cid in unresolved:
            rt.entry_gate[f"order:{cid}"] = "UNKNOWN after restart; resolve by client_order_id before new entries"
            self._blockers.append(f"ORDER_UNKNOWN:{cid}")
        if unresolved:
            self._event(now, "RECOVERY_RECONCILIATION_REQUIRED", {
                "orders": list(unresolved), "effect": "no new entries; never resubmitted; a pending exit stays the only exit"})
            # Balances can only be compared once every order is known; the runtime runs this check when its
            # last order gate clears, and only a passing check reopens entries.
            rt.entry_gate["BALANCE_RECONCILIATION_PENDING"] = "exchange balances are compared once every order is resolved"
            rt.balance_verifier = self.verify_balances
            self._blockers.append("BALANCE_RECONCILIATION_PENDING: orders unresolved")
            return
        self._check_balances(now)

    def verify_balances(self, now: datetime) -> bool:
        """Deferred balance reconciliation (called by the runtime). False keeps entries gated; a mismatch latches kill."""
        try:
            problem = self._balance_problem()
        except Exception as exc:  # noqa: BLE001 - an unreadable balance source keeps entries gated
            problem = (f"BALANCE_SOURCE_ERROR: {type(exc).__name__}: {exc}", False)
        if problem is None:
            self._event(now, "RECOVERY_BALANCES_RECONCILED", {"deferred": True})
            return True
        reason, latch = problem
        if latch:
            self.rt.kill.latch(f"restart recovery: {reason}"[:500], at=now, by="recovery")
        self.rt.entry_gate["BALANCE_RECONCILIATION_PENDING"] = reason
        self._event(now, "RECOVERY_RECONCILIATION_REQUIRED", {"balances": reason})
        return False

    def _check_balances(self, now: datetime) -> None:
        problem = self._balance_problem()
        if problem is None:
            self._event(now, "RECOVERY_BALANCES_RECONCILED", {"deferred": False})
            return
        reason, latch = problem
        if latch:
            raise _Hard(reason, latch=True)
        self.rt.entry_gate["BALANCE_RECONCILIATION_UNAVAILABLE"] = reason
        self._blockers.append(f"BALANCE_RECONCILIATION_UNAVAILABLE: {reason}")
        self._event(now, "RECOVERY_RECONCILIATION_REQUIRED", {"balances": reason})

    def _balance_problem(self) -> tuple[str, bool] | None:
        """None when exchange balances match the ledger; else (reason, latch_kill)."""
        rt = self.rt
        tol = rt.cfg.limits.kill.reconciliation_tolerance_tmn
        if self.balances is None or tol is None:
            return ("no exchange balance source" if self.balances is None else "kill.reconciliation_tolerance_tmn not configured",
                    False)
        held = self.balances()
        led = rt.ledger
        expected: dict[str, Decimal] = {"TMN": led.cash_tmn}
        prices: dict[str, Decimal] = {"TMN": Decimal(1)}
        marks = rt.marks()
        for sym in {*led.positions, *led.dust}:
            st = rt.symbols.get(sym)
            quote = st.meta.quote_asset if st is not None else "TMN"
            if quote != "TMN":
                return (f"BALANCE_MISMATCH: position {sym} is not quoted in TMN", True)
            asset = sym.removesuffix(quote)
            pos = led.positions.get(sym)
            expected[asset] = expected.get(asset, Decimal(0)) + (pos.qty if pos else Decimal(0)) + led.dust.get(sym, Decimal(0))
            prices[asset] = marks.get(sym) or (pos.cost_tmn / pos.qty if pos and pos.qty else Decimal(0))
        diffs: dict[str, Decimal] = {}
        for asset in {*expected, *(a for a, v in held.items() if v != 0)}:
            gap = abs(held.get(asset, Decimal(0)) - expected.get(asset, Decimal(0)))
            if gap:
                price = prices.get(asset)
                if price is None or price == 0:
                    return (f"BALANCE_MISMATCH: untracked asset {asset} held={held.get(asset)}", True)
                diffs[asset] = gap * price
        total = sum(diffs.values(), Decimal(0))
        if total > tol:
            return (f"BALANCE_MISMATCH: {total:.0f} TMN > tolerance {tol} "
                    f"({ {a: str(v.quantize(Decimal(1))) for a, v in diffs.items()} })", True)
        return None

    def _invalidate_approvals(self, now: datetime) -> None:
        done: list[str] = []
        for status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED):
            for t in self.store.tickets(status, run_id=self.rt.run_id):
                note = "invalidated by restart recovery: approvals are never carried across a process restart"
                new = t.model_copy(update={"status": ApprovalStatus.EXPIRED, "note": f"{t.note} | {note}" if t.note else note})
                if self.store.transition_ticket(t.ticket_id, status, new):
                    done.append(t.ticket_id)
        self._f["invalidated_approvals"] = tuple(done)
        if done:
            self._event(now, "RECOVERY_APPROVALS_INVALIDATED", {"tickets": done})

    def _reservations(self) -> None:
        led = self.rt.ledger
        keep = {reservation_id(r.intent.symbol, r.intent.intent_id) for r in self.rt.execution.orders()
                if r.intent.purpose is OrderPurpose.ENTRY and r.state not in TERMINAL_ORDER_STATES}
        released = [rid for rid in list(led.reservations) if rid not in keep]
        for rid in released:
            led.release(rid)  # journaled through the ledger hook
        missing = keep - set(led.reservations)
        if missing:
            raise _Hard(f"ENTRY_ORDER_WITHOUT_RESERVATION: {sorted(missing)}", latch=True)
        self._f |= {"released_reservations": tuple(released),
                    "reservations": {k: str(v) for k, v in sorted(led.reservations.items())}}

    def _rearm(self, now: datetime) -> None:
        rt = self.rt
        armed: list[RearmedExit] = []
        for sym, pos in sorted(rt.ledger.positions.items()):
            if pos.plan is None or sym not in rt.symbols:
                why = "no exit plan" if pos.plan is None else "symbol not watched by this runtime"
                reason = f"POSITION_UNPROTECTED:{sym}: {why}"
                self._blockers.append(reason)
                rt.entry_gate[f"position:{sym}"] = why
                rt.kill.latch(f"restart recovery: {reason}", at=now, by="recovery")
                continue
            exit_ = RearmedExit(symbol=sym, position_id=pos.position_id, qty=str(pos.qty), stop_price=str(pos.plan.stop_price),
                                take_profit_price=None if pos.plan.take_profit_price is None else str(pos.plan.take_profit_price),
                                deadline=pos.plan.deadline, overdue=now >= pos.plan.deadline, pending_exit=pos.exit_order_id)
            armed.append(exit_)
            self._event(now, "RECOVERY_EXIT_REARMED", exit_.model_dump(mode="json"))
        self._f["rearmed_exits"] = tuple(armed)

    def _schedule(self, now: datetime) -> None:
        rt = self.rt
        w = rt.calendar.status(now)
        self._f["schedule"] = {"competition_state": w.competition_state, "entries_allowed": w.entries_allowed,
                               "orders_allowed": w.orders_allowed, "must_flatten": w.must_flatten, "reason": w.reason,
                               "window_close": w.window_close.isoformat() if w.window_close else None,
                               "next_open": w.next_open.isoformat() if w.next_open else None, "window_version": w.window_version}
        if rt.ledger.positions and w.must_flatten:
            self._warnings.append(f"MUST_FLATTEN ({w.reason}): open positions are liquidated on the first maintenance pass")
        elif rt.ledger.positions and not w.orders_allowed:
            self._warnings.append(f"ORDERS_NOT_ALLOWED ({w.reason}): positions held until orders are allowed again")

    def _verify(self) -> None:
        led = self.rt.ledger
        problems = [p for p, bad in (("negative cash", led.cash_tmn < 0), ("negative spendable", led.spendable_tmn < 0),
                                     ("non-positive position", any(p.qty <= 0 for p in led.positions.values())),
                                     ("non-positive reservation", any(v <= 0 for v in led.reservations.values())))
                    if bad]
        if problems:
            raise _Hard("INVARIANT_VIOLATION: " + ", ".join(problems), latch=True)
        self._f["cash_tmn"] = str(led.cash_tmn.quantize(Decimal(1)))


def inspect_run(store: JournalStore, run_id: str, cfg: RunConfig) -> tuple[Ledger, Rebuilt]:
    """Read-only: verify the chain and rebuild a run's ledger without touching the journal (any run, sealed or not)."""
    manifest = store.manifest(run_id)
    capital = Decimal(manifest.starting_capital_tmn) if manifest and manifest.starting_capital_tmn else cfg.run.starting_capital_tmn
    ledger, prot = Ledger(capital), Protections(cfg.limits.risk)
    rebuilt = rebuild(StateLog(store, run_id).records(), ledger=ledger, protections=prot)
    if rebuilt.risk_state is not None:
        apply_risk_state(rebuilt.risk_state, ledger, prot)
    return ledger, rebuilt
