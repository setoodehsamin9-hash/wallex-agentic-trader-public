"""BACKTEST driver on a hash-pinned dataset with a virtual clock.

Timeline per base bar t (the dataset's resolution: 1-minute by default, 1-hour for long research histories): (1) the venue fills pending orders against bar t (entries at its open),
(2) marks update to bar t close, (3) the exit coordinator checks bar t high/low for stop/TP/time and forced
flattening, (4) equity is marked, (5) at each strategy-bar close the decision cycle runs on CLOSED aggregated bars.
The dataset end (or the configured split end) is treated as the competition end, so the no-new-entry and
liquidation buffers produce a TMN-flat finish, as required for realized-TMN scoring.
"""

import bisect
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from wallex_agent.strategies import strategy_history, strategy_lookback
from wallex_agent.agent.context import DecisionProvider
from wallex_agent.contracts import CodeVersion, MarketMeta, QualityFlag
from wallex_agent.contracts.hashing import content_id
from wallex_agent.execution.sim_backtest import BacktestVenue
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import Bar, aggregate
from wallex_agent.market.dataset import Dataset
from wallex_agent.market.quality import QUALITY_VERSION, assess_window, quality_flags
from wallex_agent.market.meta import eligible_symbols, parse_market_meta, unavailable_markets
from wallex_agent.reporting.performance import PerformanceSummary, compute_summary

from .config import CompetitionConfig, RunConfig
from .engine import Reporter, TradingRuntime
from .provenance import current_code_version, lockfile_sha256

SPLITS = {"TRAIN": (0.0, 0.6), "VALIDATION": (0.6, 0.8), "TEST": (0.8, 1.0), "ALL": (0.0, 1.0)}


def universe_from_metadata(meta_dir: Path) -> tuple[dict[str, MarketMeta], frozenset[str], str]:
    v1 = json.loads((meta_dir / "v1_markets.json").read_text())
    hector = json.loads((meta_dir / "hector_markets.json").read_text())
    package = json.loads((meta_dir / "package_183.json").read_text())
    version = "u-" + content_id({"v1": v1, "h": hector, "p": package})[:12]
    metas = parse_market_meta(v1, hector, metadata_version=version)
    spot = {m["symbol"] for m in hector["result"]["markets"] if m.get("is_spot")}
    eligible = eligible_symbols(spot, set(v1["result"]["symbols"]), unavailable_markets(package), set())
    return metas, eligible, version


def split_period(first: datetime, last_exclusive: datetime, split: str) -> tuple[datetime, datetime]:
    a, b = SPLITS[split]
    span = (last_exclusive - first).total_seconds()
    start = first + timedelta(seconds=int(span * a) // 3600 * 3600)
    end = first + timedelta(seconds=int(span * b) // 3600 * 3600) if b < 1.0 else last_exclusive
    return start, end


@dataclass
class BacktestResult:
    run_id: str
    runtime: TradingRuntime
    summary: PerformanceSummary
    period: tuple[datetime, datetime]
    context: dict[str, Any]


def contiguity_bars(cfg: RunConfig) -> int:
    """Decision buckets whose coverage the data-quality rule checks (same window in BACKTEST, SHADOW and LIVE)."""
    return _needed_bars(cfg)


def _needed_bars(cfg: RunConfig) -> int:
    assert cfg.features is not None and cfg.strategy is not None
    return max(cfg.features.atr_bars + 1, cfg.features.trend_bars, cfg.features.volume_bars + 1,
               strategy_lookback(cfg.strategy, cfg.features) + 1)


def _warmup_bars(cfg: RunConfig) -> int:
    assert cfg.features is not None and cfg.strategy is not None
    return max(_needed_bars(cfg), strategy_history(cfg.strategy, cfg.features))


def _flags(hist: list[Bar], as_of: datetime, interval: int, needed: int,
           tally: dict[str, int] | None = None) -> frozenset[QualityFlag]:
    """Shared decision-window rule `window-quality-1` (market/quality.py); statuses are tallied for the report."""
    q = assess_window(hist, as_of, interval, needed=needed)
    if tally is not None:
        tally[q.status] = tally.get(q.status, 0) + 1
    return quality_flags(q)


def run_backtest(cfg: RunConfig, dataset: Dataset, store: JournalStore, decider: DecisionProvider, *, run_id: str,
                 reporter: Reporter | None = None, code: CodeVersion | None = None,
                 period: tuple[datetime, datetime] | None = None, model_meta: dict[str, str | None] | None = None,
                 extra_context: dict[str, Any] | None = None) -> BacktestResult:
    assert cfg.backtest is not None
    interval = cfg.run.bar_interval_seconds
    base_s = int(dataset.manifest.get("resolution_seconds", 60))
    if interval % base_s:
        raise ValueError("strategy bar interval must be a multiple of the dataset resolution")
    cut = cfg.limits.cutoff
    if (cut.liquidation_start_before_end_s or 0) < 2 * base_s or (cut.no_new_entry_before_end_s or 0) < interval:
        # exits run once per base bar: a buffer under two base bars cannot guarantee a TMN-flat finish (AGENTS 3)
        raise ValueError("cutoff buffers too short for the dataset resolution / strategy interval")
    step = timedelta(seconds=base_s)
    metas, eligible, universe = universe_from_metadata(dataset.path / "metadata")
    symbols = [s for s in cfg.run.symbols if s in dataset.bars]
    first = max(dataset.bars[s][0].ts_open for s in symbols)
    last = min(dataset.bars[s][-1].ts_open for s in symbols) + step
    start, end = period or split_period(first, last, cfg.backtest.split)
    if cfg.backtest.start_utc:
        start = max(start, cfg.backtest.start_utc)
    if cfg.backtest.end_utc:
        end = min(end, cfg.backtest.end_utc)
    warmup = timedelta(seconds=interval * (_warmup_bars(cfg) + 2))

    venue = BacktestVenue(fee_rate=cfg.costs.fee_rate, slippage_bps=cfg.costs.backtest_slippage_bps, entry_ttl_s=cfg.execution.entry_ttl_s)
    competition = cfg.competition if cfg.competition.end_utc else CompetitionConfig(start_utc=start, end_utc=end)
    rt = TradingRuntime(cfg, store=store, venue=venue, decider=decider, metas=metas, eligible=eligible, universe_version=universe,
                        run_id=run_id, code=code or current_code_version(), dataset_id=dataset.name, data_source="WALLEX_REST",
                        competition_override=competition, reporter=reporter, scripted_approval=cfg.backtest.approval_policy,
                        model_meta=model_meta)
    rt.start(start, dependency_lock_sha256=lockfile_sha256(), dataset_refs=tuple(dataset.refs[s] for s in symbols))

    agg = {s: aggregate([b for b in dataset.bars[s] if start - warmup <= b.ts_open < end], interval, base_s) for s in symbols}
    agg_close = {s: [int((b.ts_open + timedelta(seconds=interval)).timestamp()) for b in agg[s]] for s in symbols}
    steps: dict[datetime, list[tuple[str, Bar]]] = defaultdict(list)
    for s in symbols:
        for b in dataset.bars[s]:
            if start <= b.ts_open < end:
                steps[b.ts_open].append((s, b))
    needed = _needed_bars(cfg)
    quality: dict[str, int] = {}

    for ts in sorted(steps):
        group = steps[ts]
        for s, bar in group:
            venue.on_bar(s, bar)
        rt.poll(ts)
        close = ts + step
        for s, bar in group:
            rt.update_market(s, mark=bar.close, book=None)
        rt.run_exits(close, {s: bar for s, bar in group})
        rt.mark_equity(close, record=int(close.timestamp()) % 900 == 0)
        if int(close.timestamp()) % interval == 0:
            for s in symbols:
                idx = bisect.bisect_right(agg_close[s], int(close.timestamp()))
                hist = agg[s][max(0, idx - rt.history_window):idx]
                rt.cycle(s, close, hist, flags=_flags(hist, close, interval, needed, quality))

    rt.poll(end)
    rt.mark_equity(end, record=True)
    rt.store.seal_run(run_id, end)
    context = {
        "strategy": rt.strategy.version,
        "model_route": (model_meta or {}).get("route") or "بدون مدل زبانی: انتخاب‌گر قاعده‌مند B0 (همان فرصت‌ها، انتخاب بیشترین لبه پس از هزینه)",
        "caveats": ([
            f"پایهٔ زمانی {base_s // 60} دقیقه: خروج‌ها فقط در پایان هر کندل پایه بررسی می‌شوند؛ سفارش ورود/فروش غیرحدضرر ممکن است تا دو کندل پایه باز بماند و خروج سیگنال یک کندل پایه دیرتر از کندل تصمیم پر می‌شود.",
        ] if base_s > 60 else []) + [
            f"داده: {dataset.name}، کندل {base_s // 60} دقیقه‌ای عمومی والکس، بازه {start.isoformat()} تا {end.isoformat()} (UTC)؛ پوشش ~۹۹٫۸٪ و دقیقه‌های گمشده پر نشده‌اند.",
            f"کارمزد فرضی {cfg.costs.fee_rate} هر طرف (نرخ واقعی حساب هنوز خوانده نشده) و لغزش قیمت فرضی {cfg.costs.backtest_slippage_bps} واحد در ده‌هزار.",
            "ورود هرگز در قیمت بسته‌شدن همان کندل سیگنال نیست؛ در کندل بعدی پر می‌شود. حد ضرر در صورت شکاف قیمتی با قیمت بدتر پر می‌شود.",
            "اگر حد ضرر و حد سود در یک کندل لمس شوند، فرض بدتر (حد ضرر) گرفته می‌شود.",
            "دفتر سفارش تاریخی وجود ندارد؛ نقدشوندگی و صف سفارش مدل نشده است. نتیجه Backtest تضمین نتیجه واقعی نیست.",
            "اعداد ریسک مقادیر آزمایشی شبیه‌سازی هستند و هنوز توسط صاحب پروژه تأیید نشده‌اند.",
        ],
        "data_quality": {"rule": QUALITY_VERSION, "statuses": dict(sorted(quality.items()))},
        **(extra_context or {}),
    }
    summary = compute_summary(run_id=run_id, environment=cfg.run.environment.value, autonomy=cfg.run.autonomy.value,
                              label=cfg.run.label, starting_capital_tmn=cfg.run.starting_capital_tmn, ledger=rt.ledger,
                              marks=rt.marks(), fee_rate=cfg.costs.fee_rate, decisions=store.decisions(run_id),
                              execution_errors=rt.execution.error_count, unresolved_orders=len(rt.execution.unresolved()),
                              kill_latched=rt.kill.latched, model_id=(model_meta or {}).get("model_id"),
                              model_provider=(model_meta or {}).get("provider"), strategy_versions=[rt.strategy.version],
                              period=(start.isoformat(), end.isoformat()))
    return BacktestResult(run_id, rt, summary, (start, end), context)

