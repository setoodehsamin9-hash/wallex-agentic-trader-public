"""LIVE runner (Phase 05): the SAME kernel and loop as SHADOW (`shadow._drive`). Only the venue differs: the Wallex
REST venue (`execution/wallex_live.py`) replaces the simulator. Only the owner starts it, on the run host.

Start order. Each step fails closed, and nothing is sent to Wallex before all of them pass:
 1. the config is LIVE, every LIVE requirement is set, and the U04 gate passes (`check_run_allowed`);
 2. no unfinished run of any environment is in the journal (a crashed LIVE run continues only via `resume_live`);
 3. read-only preflight with the private account reader:
    - spendable TMN equals the starting capital, and no TMN is locked;
    - no other assets and no open orders;
    - the account fee is at or below the assumed fee;
    - the markets are eligible and have metadata;
    - the competition clock is valid;
    - the code is committed and tagged;
 4. warm-up (same as SHADOW). Only then does the session start.

During the session `BalanceWatch` reconciles the exchange with the ledger. It runs whenever the engine has no order in
flight and checks three things:
 - TMN (available + locked) against the ledger cash;
 - the base quantity of every held symbol;
 - that no order is open on the exchange that the engine does not know about.
A confirmed mismatch, or repeated read failures, latches the kill switch: no new entries, while exits and liquidation
continue.

A session never seals while positions or orders are open. That run stays unfinished, and `resume_live` restores it
through restart recovery, with exchange balances as the reconciliation source.

The API key is read by the transport from `WALLEX_API_KEY`. It is never stored or logged.
"""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from wallex_agent.agent.context import DecisionProvider
from wallex_agent.contracts import CodeVersion, Environment, MarketMeta
from wallex_agent.execution.wallex_live import AccountReadError, Transport, WallexAccountReader, WallexLiveVenue
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.book import parse_depth
from wallex_agent.market.warmup import WarmupUnavailable
from wallex_agent.market.wallex_public import WallexPublicClient
from wallex_agent.policy import check_run_allowed
from wallex_agent.portfolio.ledger import ZERO, Ledger
from wallex_agent.recovery.coordinator import RecoveryBlocked, RecoveryCoordinator, RecoveryOutcome
from wallex_agent.risk.kill_switch import KillSwitch

from .config import RunConfig, missing_live_requirements
from .engine import RecoveryRequired, Reporter, TradingRuntime
from .preflight import PreflightReport, run_preflight
from .provenance import current_code_version, live_release_problems, lockfile_sha256
from .readiness import AccountEvidence
from .shadow import LiveFeed, _drive, _journal_warmup, live_universe, warm_up  # pyright: ignore[reportPrivateUsage]

BASE_TOLERANCE = Decimal("0.001")
"""Relative tolerance on a held base quantity (a 0.25% base-asset fee the mapping missed would exceed it)."""


class LivePreflightFailed(RuntimeError):
    def __init__(self, report: PreflightReport) -> None:
        self.report = report
        super().__init__("LIVE start refused: " + "; ".join(f"{n}: {d}" for n, ok, d in report.checks if not ok))


class LiveEndedNotFlat(RuntimeError):
    """The session ended with positions/orders still open; the run is NOT sealed: resume it to manage and flatten."""


def _marks(client: WallexPublicClient, assets: Sequence[str], now: datetime) -> dict[str, Decimal]:
    """Public mid prices for held non-TMN assets (to value them in preflight). Unpriced assets stay out (fail closed)."""
    marks: dict[str, Decimal] = {}
    for a in assets:
        try:
            top = parse_depth(client.depth(f"{a}TMN"), received_at=now).top()
        except (OSError, ValueError, KeyError):
            continue
        marks[f"{a}TMN"] = (top.bid + top.ask) / 2
    return marks


def _check_live(cfg: RunConfig) -> None:
    if cfg.run.environment is not Environment.LIVE or cfg.shadow is None:
        raise ValueError("LIVE runner requires a LIVE config with [shadow] loop settings")
    missing = missing_live_requirements(cfg)
    if missing:
        rep = PreflightReport()
        rep.add("config_complete", False, ", ".join(missing))
        raise LivePreflightFailed(rep)
    check_run_allowed(cfg.run.autonomy, cfg.run.environment, live_autonomous_enabled=cfg.run.live_autonomous_enabled)


def live_preflight(cfg: RunConfig, *, account: WallexAccountReader | None, client: WallexPublicClient, now: datetime,
                   require_clean_tree: bool = True) -> tuple[PreflightReport, dict[str, MarketMeta], frozenset[str], str]:
    """Read-only. Public metadata + the private account reader; an unreadable account is a failed check, never zero."""
    metas, eligible, universe = live_universe(client)
    try:
        held = [a for a, (v, lk) in (account.balances() if account else {}).items() if a != "TMN" and v + lk > 0]
        rep = run_preflight(cfg, now=now, account=account, metas=metas, eligible=eligible, marks=_marks(client, held, now),
                            require_clean_tree=require_clean_tree)
    except AccountReadError as exc:
        rep = run_preflight(cfg, now=now, account=None, metas=metas, eligible=eligible, marks={}, require_clean_tree=require_clean_tree)
        rep.checks = [c for c in rep.checks if c[0] != "account_access"]
        rep.add("account_access", False, f"private read failed: {exc}")
    return rep, metas, eligible, universe


def account_evidence(account: WallexAccountReader, cfg: RunConfig, *, now: datetime) -> AccountEvidence:
    """Readiness-gate input (runtime/readiness.py) from one read-only pass. Holds no secret or account identifier."""
    bal = account.balances()
    tmn_avail, tmn_locked = bal.get("TMN", (ZERO, ZERO))
    return AccountEvidence(read_at=now, fee_rates={s: f for s in cfg.run.symbols if (f := account.fee_rate(s)) is not None},
                           spendable_tmn=tmn_avail, locked_tmn=tmn_locked, open_orders=len(account.open_orders()),
                           non_tmn_assets=tuple(sorted(a for a, (v, lk) in bal.items() if a != "TMN" and v + lk > 0)))


@dataclass
class BalanceWatch:
    """Exchange vs ledger whenever the engine has no order in flight; a mismatch must be seen on two reads in a row."""

    ledger: Ledger
    kill: KillSwitch
    open_orders: Callable[[], Sequence[Any]]
    account: WallexAccountReader
    store: JournalStore
    run_id: str
    every_s: int
    tolerance: Decimal
    max_failures: int = 3
    confirmations: int = 2
    _last: datetime | None = None
    _failures: int = 0
    _mismatches: int = 0

    def __call__(self, now: datetime) -> None:
        if self._last is not None and (now - self._last).total_seconds() < self.every_s:
            return
        self._last = now
        if self.open_orders():
            return  # balances move while an order is working; check when quiet
        try:
            problem = self._problem(self.account.balances(), len(self.account.open_orders()))
        except (AccountReadError, OSError, TimeoutError) as exc:
            self._failures += 1
            if self._failures >= self.max_failures:
                self._latch(now, "BALANCE_UNREADABLE", {"failures": self._failures, "error": type(exc).__name__})
            return
        self._failures = 0
        if problem is None:
            self._mismatches = 0
            return
        self._mismatches += 1
        self.store.record_risk_event(self.run_id, now, "BALANCE_MISMATCH_SEEN", {**problem, "seen": self._mismatches})
        if self._mismatches >= self.confirmations:
            self._latch(now, "BALANCE_MISMATCH", problem)

    def _problem(self, bal: Mapping[str, tuple[Decimal, Decimal]], exchange_open: int) -> dict[str, str] | None:
        if exchange_open:
            return {"what": "orders open on the exchange that the engine does not track", "count": str(exchange_open)}
        avail, locked = bal.get("TMN", (ZERO, ZERO))
        if abs(avail + locked - self.ledger.cash_tmn) > self.tolerance:
            return {"what": "TMN", "exchange": str(avail + locked), "ledger": str(self.ledger.cash_tmn), "tolerance": str(self.tolerance)}
        for symbol, pos in self.ledger.positions.items():
            v, lk = bal.get(symbol[:-3], (ZERO, ZERO))
            expect = pos.qty + self.ledger.dust.get(symbol, ZERO)
            if abs(v + lk - expect) > expect * BASE_TOLERANCE:
                return {"what": symbol[:-3], "exchange": str(v + lk), "ledger": str(expect)}
        return None

    def _latch(self, now: datetime, kind: str, data: dict[str, Any]) -> None:
        self.store.record_risk_event(self.run_id, now, kind, data)
        if not self.kill.latched:
            self.kill.latch(f"{kind}: exchange does not reconcile with the ledger", at=now, by="balance-watch")


def _watch(rt: TradingRuntime, account: WallexAccountReader, store: JournalStore, run_id: str, cfg: RunConfig,
           every_s: int) -> BalanceWatch:
    return BalanceWatch(ledger=rt.ledger, kill=rt.kill, open_orders=rt.execution.open_orders, account=account, store=store,
                        run_id=run_id, every_s=every_s, tolerance=cfg.limits.kill.reconciliation_tolerance_tmn or ZERO)


def _finish(rt: TradingRuntime, store: JournalStore, run_id: str) -> TradingRuntime:
    if store.run_ended_at(run_id) is None:
        raise LiveEndedNotFlat(f"{run_id}: positions={sorted(rt.ledger.positions)} open_orders={len(rt.execution.open_orders())}; "
                               f"run left unfinished: `wallex-agent run --env LIVE --resume {run_id}`")
    return rt


def run_live(cfg: RunConfig, store: JournalStore, decider: DecisionProvider, *, run_id: str, duration_s: int, transport: Transport,
             client: WallexPublicClient | None = None, reporter: Reporter | None = None,
             model_meta: dict[str, str | None] | None = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
             sleep: Callable[[float], None] = time.sleep, summary_every_s: int = 300, require_clean_tree: bool = True,
             balance_check_s: int = 300, code: CodeVersion | None = None) -> TradingRuntime:
    _check_live(cfg)
    assert cfg.shadow is not None
    if store.unfinished_runs():
        raise RecoveryRequired("an unfinished run exists: inspect it with `wallex-agent recover RUN_ID` and continue a LIVE run "
                               "with `run --env LIVE --resume RUN_ID`; a new session never starts over possibly open real orders")
    client = client or WallexPublicClient()
    account = WallexAccountReader(transport)
    start = clock()
    report, metas, eligible, universe = live_preflight(cfg, account=account, client=client, now=start,
                                                       require_clean_tree=require_clean_tree)
    if not report.ok:
        raise LivePreflightFailed(report)
    interval = cfg.run.bar_interval_seconds
    feed = LiveFeed(client, history_minutes=min(cfg.shadow.history_bars * interval // 60, 2 * 1440))
    rt = TradingRuntime(cfg, store=store, venue=WallexLiveVenue(transport), decider=decider, metas=metas, eligible=eligible,
                        universe_version=universe, run_id=run_id, code=code or current_code_version(), dataset_id="wallex-live",
                        data_source="WALLEX_REST", reporter=reporter, model_meta=model_meta, clock=clock)
    warm = warm_up(cfg, feed, list(rt.symbols), start, rt.history_window)
    failed = [f"{r.symbol}: {r.reason}" for r in warm if not r.ok]
    if failed:
        raise WarmupUnavailable("; ".join(failed))  # before any session exists
    rt.start(start, dependency_lock_sha256=lockfile_sha256())
    store.put_ref(run_id, "live_preflight", [{"check": n, "ok": ok, "detail": d} for n, ok, d in report.checks])
    _journal_warmup(store, run_id, warm, reporter)
    for s in rt.symbols:
        feed.refresh_candles(s, start)
    rt = _drive(rt, cfg, store, feed, run_id=run_id, start=start, duration_s=duration_s, reporter=reporter, model_meta=model_meta,
                clock=clock, sleep=sleep, summary_every_s=summary_every_s,
                on_maintain=_watch(rt, account, store, run_id, cfg, balance_check_s), seal_only_flat=True)
    return _finish(rt, store, run_id)


def resume_live(cfg: RunConfig, store: JournalStore, decider: DecisionProvider, *, run_id: str, duration_s: int, transport: Transport,
                client: WallexPublicClient | None = None, reporter: Reporter | None = None,
                model_meta: dict[str, str | None] | None = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                sleep: Callable[[float], None] = time.sleep, summary_every_s: int = 300, balance_check_s: int = 300,
                on_runtime: Callable[[TradingRuntime], None] | None = None, code: CodeVersion | None = None) -> TradingRuntime:
    """Continue an unfinished LIVE run: rebuild from the state log, look every order up by client id, compare exchange
    balances with the rebuilt ledger (mismatch -> BLOCKED + kill latch), re-arm exits, then the same loop."""
    _check_live(cfg)
    assert cfg.shadow is not None
    manifest = store.manifest(run_id)
    if manifest is None or manifest.environment is not Environment.LIVE or store.run_ended_at(run_id) is not None:
        raise ValueError(f"{run_id} is not an unfinished LIVE run")
    problems = [] if code is not None else live_release_problems()  # an injected code version is for tests only
    if problems:
        rep = PreflightReport()
        rep.add("clean_tagged_tree", False, "; ".join(problems))
        raise LivePreflightFailed(rep)
    client = client or WallexPublicClient()
    account = WallexAccountReader(transport)
    metas, eligible, universe = live_universe(client)
    interval = cfg.run.bar_interval_seconds
    feed = LiveFeed(client, history_minutes=min(cfg.shadow.history_bars * interval // 60, 2 * 1440))
    rt = TradingRuntime(cfg, store=store, venue=WallexLiveVenue(transport), decider=decider, metas=metas, eligible=eligible,
                        universe_version=universe, run_id=run_id, code=code or current_code_version(), dataset_id="wallex-live",
                        data_source="WALLEX_REST", reporter=reporter, model_meta=model_meta, clock=clock, resume=True)
    start = clock()
    warm = warm_up(cfg, feed, list(rt.symbols), start, rt.history_window)

    def totals() -> dict[str, Decimal]:
        return {a: v + lk for a, (v, lk) in account.balances().items()}

    report = RecoveryCoordinator(rt, balances=totals).recover(start)
    if reporter:
        reporter("RECOVERY", {"outcome": report.outcome.value, "blockers": list(report.blockers),
                              "positions": [e.symbol for e in report.rearmed_exits], "unresolved": list(report.unresolved_orders)})
    if report.outcome is RecoveryOutcome.BLOCKED:
        raise RecoveryBlocked(report)
    _journal_warmup(store, run_id, warm, reporter)
    if on_runtime:
        on_runtime(rt)
    as_of = datetime.fromtimestamp(int(start.timestamp()) // interval * interval, UTC)
    for s in rt.symbols:
        feed.refresh_candles(s, start)
        rt.update_market(s, mark=None, book=feed.refresh_book(s, start))
        rt.prime_snapshot(s, as_of, feed.strategy_bars(s, interval, as_of), flags=frozenset(feed.flags.get(s, set())))
    rt = _drive(rt, cfg, store, feed, run_id=run_id, start=start, duration_s=duration_s, reporter=reporter, model_meta=model_meta,
                clock=clock, sleep=sleep, summary_every_s=summary_every_s,
                on_maintain=_watch(rt, account, store, run_id, cfg, balance_check_s), seal_only_flat=True)
    return _finish(rt, store, run_id)
