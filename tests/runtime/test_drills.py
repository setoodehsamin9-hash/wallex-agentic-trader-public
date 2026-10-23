from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from support.synthetic import REPO, metadata_dir, synthetic_bars

from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import aggregate
from wallex_agent.market.book import OrderBook
from wallex_agent.runtime.backtest import universe_from_metadata
from wallex_agent.runtime.config import RunConfig, load_run_config
from wallex_agent.runtime.drills import MarketCapture, run_drills


def _book(px: Decimal, at: object) -> OrderBook:
    asks = tuple((px * (1 + Decimal(i) / 10000), Decimal(50)) for i in range(1, 6))
    bids = tuple((px * (1 - Decimal(i) / 10000), Decimal(50)) for i in range(1, 6))
    return OrderBook(bids=bids, asks=asks, received_at=at)  # type: ignore[arg-type]


def test_all_shadow_drills_pass_on_synthetic_capture(tmp_path: Path) -> None:
    cfg: RunConfig = load_run_config(REPO / "config" / "shadow.toml")
    raw = {"BTCTMN": synthetic_bars(2, 1e8, 0.0), "ETHTMN": synthetic_bars(2, 5e6, 1.3)}
    bars = {s: aggregate(b, cfg.run.bar_interval_seconds) for s, b in raw.items()}
    at = bars["BTCTMN"][-1].ts_open + __import__("datetime").timedelta(seconds=cfg.run.bar_interval_seconds)
    books = {s: _book(b[-1].close, at) for s, b in bars.items()}
    metadata_dir(tmp_path / "meta")
    metas, eligible, _ = universe_from_metadata(tmp_path / "meta")
    factory: Callable[[str], JournalStore] = lambda name: JournalStore(tmp_path / f"{name}.sqlite")  # noqa: E731
    results = run_drills(cfg, MarketCapture(bars, books, at, "synthetic"), metas=metas, eligible=eligible, store_factory=factory)
    failed = {r.name: r.evidence for r in results if not r.passed}
    assert not failed, failed
    assert {r.name for r in results} == {"stale_data", "ninerouter_timeout", "invalid_ai_output", "execution_simulator_timeout",
                                         "duplicate_submit", "double_spend", "trading_window_closure", "kill_switch",
                                         "cutoff_liquidation"}
