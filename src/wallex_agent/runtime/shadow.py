"""SHADOW runner: live PUBLIC Wallex data, real decision provider (NineRouter when configured), real strategy, risk
and orchestrator; ShadowVenue simulated fills only. There is no code path to a Wallex write here: the public client
has no write method and the ShadowVenue never calls the network.

Loop every poll interval: refresh depth (exit watcher + marks), process approvals, run exits, and when a strategy
bar has closed, refresh 1m candles and run the decision cycle. Status is written to the journal for `status`.
An unfinished session is never restarted from scratch: `resume_shadow` runs restart recovery, then the same loop.

Warm-up (Phase 05): a strategy needing more history than the 2-day minute window (F2: ~187 days of 4h bars) is
bootstrapped from native 1h candles (validated cache / seed, then public reads for the missing tail; market/warmup.py)
BEFORE the session starts; an insufficient warm-up raises WarmupUnavailable and no session is created. Each decision
window is classified by the shared rule `window-quality-1` (market/quality.py); non-OK statuses are journaled.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from wallex_agent.agent.context import DecisionProvider
from wallex_agent.contracts import Environment, QualityFlag
from wallex_agent.contracts.hashing import content_id
from wallex_agent.execution.sim_shadow import ShadowVenue
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import Bar, WallexPayloadError, aggregate, parse_udf_history
from wallex_agent.market.book import OrderBook, parse_depth
from wallex_agent.market.meta import eligible_symbols, parse_market_meta, unavailable_markets
from wallex_agent.market.quality import assess_window, quality_flags
from wallex_agent.market.warmup import HourlyCache, WarmupReport, WarmupUnavailable, bootstrap_hourly, combine_hours, fetch_hourly
from wallex_agent.market.wallex_public import WallexPublicClient
from wallex_agent.recovery.coordinator import RecoveryBlocked, RecoveryCoordinator, RecoveryOutcome

from .backtest import contiguity_bars
from .config import RunConfig
from .decision_wait import ResponsiveDecisionProvider
from .engine import RecoveryRequired, Reporter, TradingRuntime
from .provenance import current_code_version, lockfile_sha256


@dataclass
class LiveFeed:
    client: WallexPublicClient
    history_minutes: int
    bars: dict[str, dict[datetime, Bar]] = field(default_factory=lambda: {})
    flags: dict[str, set[QualityFlag]] = field(default_factory=lambda: {})
    books: dict[str, OrderBook] = field(default_factory=lambda: {})
    hourly: dict[str, dict[datetime, Bar]] = field(default_factory=lambda: {})
    """Native closed 1h candles (warm-up + periodic refresh); only used for intervals that are whole hours."""

    def refresh_candles(self, symbol: str, now: datetime) -> None:
        have = self.bars.setdefault(symbol, {})
        start = now - timedelta(minutes=self.history_minutes if not have else 90)
        flags: set[QualityFlag] = set()
        cursor = start
        while cursor < now:
            chunk_end = min(now, cursor + timedelta(days=1) - timedelta(seconds=1))
            try:
                bars, fl = parse_udf_history(self.client.candles(symbol, "1", int(cursor.timestamp()), int(chunk_end.timestamp())), 60, now=now)
                flags |= fl
                for b in bars:
                    have[b.ts_open] = b
            except (WallexPayloadError, OSError) as exc:
                flags.add(QualityFlag.STALE)
                _ = exc
            cursor = chunk_end + timedelta(seconds=1)
        self.flags[symbol] = flags - {QualityFlag.GAP}  # contiguity is checked on the decision window (window-quality-1)

    def refresh_hourly(self, symbol: str, now: datetime) -> None:
        """Re-read the last 6 native hours (fills minute-feed gaps with exchange candles; never interpolates)."""
        if symbol not in self.hourly:
            return
        bars, notes, _ = fetch_hourly(self.client, symbol, now - timedelta(hours=6), now)
        for b in bars:
            self.hourly[symbol][b.ts_open] = b
        if "RESOLUTION_MISMATCH" in notes:
            self.flags.setdefault(symbol, set()).add(QualityFlag.RESOLUTION_MISMATCH)

    def refresh_book(self, symbol: str, now: datetime) -> OrderBook | None:
        try:
            book = parse_depth(self.client.depth(symbol), received_at=now)
        except (WallexPayloadError, OSError):
            return self.books.get(symbol)
        self.books[symbol] = book
        return book

    def strategy_bars(self, symbol: str, interval: int, as_of: datetime) -> list[Bar]:
        one = [b for ts, b in sorted(self.bars.get(symbol, {}).items()) if ts + timedelta(seconds=60) <= as_of]
        if symbol in self.hourly and interval % 3600 == 0:
            native = [b for ts, b in sorted(self.hourly[symbol].items()) if ts + timedelta(hours=1) <= as_of]
            agg = aggregate(combine_hours(one, native), interval, 3600)
        else:
            agg = aggregate(one, interval)
        return [b for b in agg if b.ts_open + timedelta(seconds=interval) <= as_of]


def warm_up(cfg: RunConfig, feed: LiveFeed, symbols: list[str], now: datetime, history_window: int) -> list[WarmupReport]:
    """Bootstrap native 1h history when the strategy needs more than the minute window. Empty list = not needed."""
    assert cfg.shadow is not None
    interval = cfg.run.bar_interval_seconds
    required_hours = -(-history_window * interval // 3600)
    if required_hours * 60 <= feed.history_minutes:
        return []
    if interval % 3600:
        raise WarmupUnavailable(f"UNSUPPORTED_INTERVAL: {interval}s needs {required_hours}h of history")
    cache = HourlyCache(Path(cfg.shadow.warmup_cache_dir))
    seed = Path(cfg.shadow.warmup_seed_dir) if cfg.shadow.warmup_seed_dir else None
    reports: list[WarmupReport] = []
    for s in symbols:
        bars, rep = bootstrap_hourly(s, client=feed.client, cache=cache, seed=seed, now=now, required_hours=required_hours)
        feed.hourly[s] = {b.ts_open: b for b in bars}
        reports.append(rep)
    return reports


def live_universe(client: WallexPublicClient) -> tuple[dict[str, Any], frozenset[str], str]:
    v1, hector, package = client.markets_v1(), client.markets_hector(), client.package(183)
    version = "u-live-" + content_id({"p": sorted(unavailable_markets(package))})[:12]
    metas = parse_market_meta(v1, hector, metadata_version=version)
    spot = {m["symbol"] for m in hector["result"]["markets"] if m.get("is_spot")}
    return metas, eligible_symbols(spot, set(v1["result"]["symbols"]), unavailable_markets(package), set()), version


def run_shadow(cfg: RunConfig, store: JournalStore, decider: DecisionProvider, *, run_id: str, duration_s: int,
               reporter: Reporter | None = None, model_meta: dict[str, str | None] | None = None,
               client: WallexPublicClient | None = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
               sleep: Callable[[float], None] = time.sleep, summary_every_s: int = 300,
               on_runtime: Callable[[TradingRuntime], None] | None = None) -> TradingRuntime:
    if cfg.run.environment is not Environment.SHADOW or cfg.shadow is None:
        raise ValueError("SHADOW runner requires a SHADOW config with shadow settings")
    if any(m.environment is Environment.SHADOW for m in store.unfinished_runs()):
        raise RecoveryRequired("unfinished SHADOW session requires restart recovery: inspect it with `wallex-agent recover RUN_ID`, "
                               "then `shadow --resume RUN_ID` (or retire it with `abandon`); a new session never starts over it")
    client = client or WallexPublicClient()
    metas, eligible, universe = live_universe(client)
    interval = cfg.run.bar_interval_seconds
    feed = LiveFeed(client, history_minutes=min(cfg.shadow.history_bars * interval // 60, 2 * 1440))
    venue = ShadowVenue(fee_rate=cfg.costs.fee_rate, book=lambda s: feed.books.get(s))
    rt = TradingRuntime(cfg, store=store, venue=venue, decider=decider, metas=metas, eligible=eligible, universe_version=universe,
                        run_id=run_id, code=current_code_version(), dataset_id="wallex-live-public", data_source="WALLEX_REST",
                        reporter=reporter, model_meta=model_meta, clock=clock)
    start = clock()
    reports = warm_up(cfg, feed, list(rt.symbols), start, rt.history_window)
    failed = [f"{r.symbol}: {r.reason}" for r in reports if not r.ok]
    if failed:
        raise WarmupUnavailable("; ".join(failed))  # fail closed BEFORE any session/journal run exists
    if on_runtime:
        on_runtime(rt)
    rt.start(start, dependency_lock_sha256=lockfile_sha256())
    _journal_warmup(store, run_id, reports, reporter)
    for s in rt.symbols:
        feed.refresh_candles(s, start)
    return _drive(rt, cfg, store, feed, run_id=run_id, start=start, duration_s=duration_s, reporter=reporter,
                  model_meta=model_meta, clock=clock, sleep=sleep, summary_every_s=summary_every_s)


def resume_shadow(cfg: RunConfig, store: JournalStore, decider: DecisionProvider, *, run_id: str, duration_s: int,
                  reporter: Reporter | None = None, model_meta: dict[str, str | None] | None = None,
                  client: WallexPublicClient | None = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                  sleep: Callable[[float], None] = time.sleep, summary_every_s: int = 300,
                  on_runtime: Callable[[TradingRuntime], None] | None = None) -> TradingRuntime:
    """Resume an unfinished SHADOW run after a crash or stop. Restart recovery runs first (rebuild from the state log,
    settle lost simulated outcomes, invalidate approvals, re-arm exits); BLOCKED raises before the loop starts.
    The venue is a fresh ShadowVenue: there is still no code path to a Wallex write."""
    if cfg.run.environment is not Environment.SHADOW or cfg.shadow is None:
        raise ValueError("SHADOW resume requires a SHADOW config with shadow settings")
    client = client or WallexPublicClient()
    metas, eligible, universe = live_universe(client)
    interval = cfg.run.bar_interval_seconds
    feed = LiveFeed(client, history_minutes=min(cfg.shadow.history_bars * interval // 60, 2 * 1440))
    venue = ShadowVenue(fee_rate=cfg.costs.fee_rate, book=lambda s: feed.books.get(s))
    rt = TradingRuntime(cfg, store=store, venue=venue, decider=decider, metas=metas, eligible=eligible, universe_version=universe,
                        run_id=run_id, code=current_code_version(), dataset_id="wallex-live-public", data_source="WALLEX_REST",
                        reporter=reporter, model_meta=model_meta, clock=clock, resume=True)
    start = clock()
    warm = warm_up(cfg, feed, list(rt.symbols), start, rt.history_window)
    report = RecoveryCoordinator(rt).recover(start)
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
    return _drive(rt, cfg, store, feed, run_id=run_id, start=start, duration_s=duration_s, reporter=reporter,
                  model_meta=model_meta, clock=clock, sleep=sleep, summary_every_s=summary_every_s)


def _journal_warmup(store: JournalStore, run_id: str, reports: list[WarmupReport], reporter: Reporter | None) -> None:
    if not reports:
        return
    store.put_ref(run_id, "warmup", [r.model_dump(mode="json") for r in reports])
    for r in reports:
        if not r.ok:
            store.record_risk_event(run_id, r.as_of, "WARMUP_INSUFFICIENT", {"symbol": r.symbol, "reason": r.reason})
    if reporter:
        reporter("WARMUP", {"symbols": [{"symbol": r.symbol, "ok": r.ok, "reason": r.reason, "bars": r.bars,
                                         "sources": r.sources, "sha256": r.sha256[:16]} for r in reports]})


def _drive(rt: TradingRuntime, cfg: RunConfig, store: JournalStore, feed: LiveFeed, *, run_id: str, start: datetime,
           duration_s: int, reporter: Reporter | None, model_meta: dict[str, str | None] | None,
           clock: Callable[[], datetime], sleep: Callable[[float], None], summary_every_s: int,
           on_maintain: Callable[[datetime], None] | None = None, seal_only_flat: bool = False) -> TradingRuntime:
    assert cfg.shadow is not None
    interval = cfg.run.bar_interval_seconds
    needed = contiguity_bars(cfg)
    last_bar = int(start.timestamp()) // interval
    pending: datetime | None = None
    last_summary = start
    nr_health = (model_meta or {}).get("provider") or "—"
    nr_latency_ms: int | None = None
    ai_calls = ai_errors = 0

    def summary(now: datetime, *, active: bool) -> dict[str, Any]:
        return {**rt.status_summary(now, active=active, ninerouter=nr_health),
                "ninerouter_latency_ms": nr_latency_ms, "ai_calls": ai_calls, "ai_errors": ai_errors}

    def maintain() -> None:
        now = clock()
        for s in rt.symbols:
            rt.update_market(s, mark=None, book=feed.refresh_book(s, now))
        rt.poll(now)
        rt.process_approvals(now)
        rt.run_exits(now, {s: b for s, b in feed.books.items() if s in rt.symbols})
        rt.mark_equity(now, record=False)
        if on_maintain is not None:  # LIVE: exchange balance reconciliation (runtime/live.py BalanceWatch)
            on_maintain(now)
        store.set_status(run_id, now, summary(now, active=True))

    waiter = ResponsiveDecisionProvider(rt.decider, maintain=maintain, clock=clock, interval_s=cfg.shadow.poll_interval_s)
    rt.decider = waiter
    try:
        while True:
            maintain()
            now = clock()
            bar_idx = int(now.timestamp()) // interval
            if bar_idx > last_bar:
                last_bar = bar_idx
                pending = datetime.fromtimestamp(bar_idx * interval, UTC)
            if pending is not None:
                hists: dict[str, list[Bar]] = {}
                complete = True
                for s in rt.symbols:
                    feed.refresh_candles(s, now)
                    feed.refresh_hourly(s, now)
                    hists[s] = feed.strategy_bars(s, interval, pending)
                    complete &= bool(hists[s]) and hists[s][-1].ts_open + timedelta(seconds=interval) == pending
                if complete or (now - pending).total_seconds() >= cfg.shadow.bar_settle_max_s:
                    for s in rt.symbols:
                        hist = hists[s]
                        flags = set(feed.flags.get(s, set()))
                        q = assess_window(hist, pending, interval, needed=needed)
                        flags |= quality_flags(q)
                        if q.status != "OK":
                            store.record_risk_event(run_id, now, "DATA_QUALITY", {
                                "symbol": s, "as_of": pending.isoformat(), "status": q.status, "detail": q.detail,
                                "missing_buckets": q.missing_buckets, "rule": q.version})
                        rec = rt.cycle(s, pending, hist[-rt.history_window:], flags=frozenset(flags))
                        if rec.provenance is not None and rec.provenance.source.value == "LLM":
                            ai_calls += 1
                            nr_latency_ms = rec.provenance.latency_ms
                            ai_errors += int(rec.validated is None or not rec.validated.status.answered)
                            nr_health = "OK" if rec.validated and rec.validated.status.answered else (
                                f"ERROR:{rec.validated.status.value if rec.validated else '?'}")
                    pending = None
            now = clock()
            status = summary(now, active=True)
            store.set_status(run_id, now, status)
            if reporter and (now - last_summary).total_seconds() >= summary_every_s:
                reporter("SUMMARY", status)
                last_summary = now
            if (now - start).total_seconds() >= duration_s:
                break
            try:
                sleep(cfg.shadow.poll_interval_s)
            except KeyboardInterrupt:
                rt.checkpoint("INTERRUPTED", clock())  # between cycles: memory and state log agree; run stays resumable
                raise
    finally:
        waiter.close()
    end = clock()
    rt.mark_equity(end, record=True)
    store.set_status(run_id, end, summary(end, active=False))
    if seal_only_flat and (rt.ledger.positions or rt.execution.open_orders()):
        # LIVE: never seal over real positions/orders; the run stays unfinished so `--resume` can manage and flatten them
        store.record_risk_event(run_id, end, "LIVE_ENDED_NOT_FLAT", {"positions": sorted(rt.ledger.positions),
                                                                     "open_orders": len(rt.execution.open_orders())})
        rt.checkpoint("INTERRUPTED", end)
        return rt
    rt.checkpoint("SHUTDOWN", end)
    store.seal_run(run_id, end)
    return rt

