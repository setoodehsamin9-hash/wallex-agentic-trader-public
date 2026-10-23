"""Frozen F2 evidence only. Offline, Decimal kernel; no production code or config changes.

uv run python scripts/challenge_windows.py run --workers 2
uv run python scripts/challenge_windows.py verify --workers 2

The kernel uses its existing hourly BACKTEST adapter, risk sizing, journal and cutoff.
Research controls reuse the Phase 04 simulator and are explicitly separate from TMN results.
TEST remains locked: evaluation ends at 2026-10-13 00:00 UTC. No model is used.
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import random
import subprocess
from collections import Counter, defaultdict
from collections.abc import Generator, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.contracts import CodeVersion, FeatureSet
from wallex_agent.features.engine import compute_features
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.bars import Bar, aggregate
from wallex_agent.market.book import OrderBook
from wallex_agent.market.dataset import Dataset, DatasetIntegrityError, load_dataset
from wallex_agent.runtime import engine
from wallex_agent.runtime.backtest import _warmup_bars, run_backtest  # pyright: ignore[reportPrivateUsage]
from wallex_agent.runtime.config import FeatureSettings, RunConfig, load_run_config
from wallex_agent.strategies.f2_tsm import QUIET_RANK, SHOCK_RANK, TREND_RANK

from phase04r2_lib import COSTS, DEV_END, PACK, Plan, SimResult, buy_hold, portfolio, random_plan, simulate
from phase04r2_run import Market, SLICES, parse

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/phase-05/challenge-window-analysis"
BASE = "751942e7d4e36c158247366651d1cdf7c47dd196"
CFG = ROOT / "config/experiments/phase04r2/base-4h-f2.toml"
START = datetime(2025, 3, 1, tzinfo=UTC)
SYMBOLS = ("BTCTMN", "ETHTMN")
BOOKS = {"BTC": ("BTCTMN",), "ETH": ("ETHTMN",), "POOLED": SYMBOLS}
DECIMAL_COSTS = {"base": (Decimal("0.0025"), Decimal(5)), "adverse": (Decimal("0.0035"), Decimal(10))}
Z = Decimal(0)
H = Decimal(100)
RANDOM_SEEDS = 20
_worker_dataset: Dataset | None = None


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1, default=str, allow_nan=False) + "\n").encode()


def rolling_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    if start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0):
        raise ValueError("UTC-aware bounds required")
    if any(int(t.timestamp()) % 14400 for t in (start, end)) or start >= end or end > DEV_END:
        raise ValueError("unaligned/reversed bounds or protected TEST")
    out: list[tuple[datetime, datetime]] = []
    while start + timedelta(hours=72) <= end:
        out.append((start, start + timedelta(hours=72)))
        start += timedelta(hours=4)
    return out


def quantile(values: Sequence[Decimal], q: Decimal) -> Decimal | None:
    if not values:
        return None
    a = sorted(values)
    at = (len(a) - 1) * q
    lo = int(at)
    hi = min(len(a) - 1, lo + 1)
    return a[lo] + (a[hi] - a[lo]) * (at - lo)


def distribution(values: Sequence[Decimal]) -> dict[str, Any]:
    n = len(values)
    return {"n": n, "positive_pct": H * sum(x > 0 for x in values) / n if n else None,
            "negative_pct": H * sum(x < 0 for x in values) / n if n else None,
            "zero_pct": H * sum(x == 0 for x in values) / n if n else None,
            "mean": sum(values, Z) / n if n else None,
            **{k: quantile(values, Decimal(q)) for k, q in (("median", ".5"), ("p10", ".1"), ("p25", ".25"),
                                                         ("p75", ".75"), ("p90", ".9"))},
            "min": min(values) if n else None, "max": max(values) if n else None}


def concentration(values: Sequence[Decimal]) -> dict[str, Any]:
    winners = sorted((x for x in values if x > 0), reverse=True)
    total, gross = sum(values, Z), sum(winners, Z)
    top: dict[str, Any] = {}
    for p in (1, 5, 10):
        n = math.ceil(len(values) * p / 100)
        value = sum(winners[:n], Z)
        top[str(p)] = {"n": min(n, len(winners)), "pnl": value,
                       "share_net_pct": value / total * H if total else None,
                       "share_gross_positive_pct": value / gross * H if gross else None}
    return {"observations": len(values), "positive_observations": len(winners), "net_sum": total,
            "gross_positive": gross, "net_sum_positive": total > 0, "top": top,
            "without_largest_winner": total - sum(winners[:1], Z),
            "without_top3_winners": total - sum(winners[:3], Z)}


def summarize_windows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    good = [r for r in rows if r["valid"]]
    return {"scheduled_windows": len(rows), "valid_windows": len(good), "invalid_windows": len(rows) - len(good),
            "return_pct": distribution([r["return_pct"] for r in good]),
            "no_trade_pct": H * sum(r["trades"] == 0 for r in good) / len(good) if good else None,
            "drawdown_pct": distribution([r["max_drawdown_pct"] for r in good]),
            "trades_per_window": distribution([Decimal(r["trades"]) for r in good]),
            "worst_window": min(good, key=lambda r: r["return_pct"]) if good else None,
            "best_window": max(good, key=lambda r: r["return_pct"]) if good else None}


def terminal_attribution(trades: Sequence[dict[str, Any]], fee: Decimal) -> dict[str, Any]:
    forced = [t for t in trades if t["exit_reason"] == "LIQUIDATION"]
    return {"forced_trades": len(forced), "forced_pnl_tmn": sum((t["net_pnl_tmn"] for t in forced), Z),
            "forced_exit_fee_tmn": sum((t["qty"] * t["avg_exit_price"] * fee for t in forced), Z),
            "forced_vs_terminal_mid_tmn": sum((t["qty"] * (t["avg_exit_price"] * (1 - fee) - t["terminal_mark"])
                                                for t in forced), Z)}


def f2_regime(fs: FeatureSet, symbol: str) -> str:
    f = {x.feature_id: x.value for x in fs.features}
    close, vol, er, lag = (f.get(f"{symbol}.{k}") for k in ("close.1", "vol_rank.14", "er_rank.42", "close_lag.42"))
    if any(x is None for x in (close, vol, er, lag)):
        return "UNKNOWN"
    assert close is not None and vol is not None and er is not None and lag is not None
    if vol >= SHOCK_RANK:
        return "SHOCK"
    if vol <= QUIET_RANK:
        return "QUIET"
    if er >= TREND_RANK:
        return "TREND_UP" if close > lag else "TREND_DOWN"
    return "RANGE"


@contextmanager
def memoized_features() -> Generator[None]:
    """Per-window content-addressed memoization. Snapshot SHA commits every bar and its full warm-up tail."""
    original = engine.compute_features
    cache: dict[tuple[Any, ...], FeatureSet] = {}

    def cached(symbol: str, snapshot_id: str, bars: Sequence[Bar], as_of: datetime, interval_s: int,
               book: OrderBook | None, settings: FeatureSettings, lookback_bars: int) -> FeatureSet:
        if book is not None:
            return original(symbol, snapshot_id, bars, as_of, interval_s, book, settings, lookback_bars)
        key = (symbol, snapshot_id, as_of, interval_s, settings.model_dump_json(), lookback_bars)
        if key not in cache:
            cache[key] = original(symbol, snapshot_id, bars, as_of, interval_s, book, settings, lookback_bars)
        return cache[key]

    engine.compute_features = cached
    try:
        yield
    finally:
        engine.compute_features = original


@lru_cache(maxsize=6)
def variant(book: str, cost: str) -> RunConfig:
    data = load_run_config(CFG).model_dump(mode="python")
    data["run"]["symbols"] = list(BOOKS[book])
    data["costs"]["fee_rate"], data["costs"]["backtest_slippage_bps"] = DECIMAL_COSTS[cost]
    return RunConfig.model_validate(data)


def kernel_window(cfg: RunConfig, dataset: Dataset, start: datetime, end: datetime, *, run_id: str,
                  memo: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if end > DEV_END or start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0):
        raise ValueError("protected TEST or non-UTC period")
    warmup = timedelta(seconds=cfg.run.bar_interval_seconds * (_warmup_bars(cfg) + 2))
    data = {s: [b for b in dataset.bars[s] if start - warmup <= b.ts_open < end] for s in cfg.run.symbols}
    sliced = Dataset(dataset.name, dataset.path, data, dataset.refs, dataset.manifest)
    invalid: list[str] = []
    for sym, bars in data.items():
        if not bars or bars[-1].ts_open + timedelta(hours=1) != end:
            invalid.append(f"MISSING_TERMINAL_BAR:{sym}")
        if not any(b.ts_open == start for b in bars):
            invalid.append(f"MISSING_INITIAL_BAR:{sym}")
    store = JournalStore(Path(":memory:"))
    try:
        with memoized_features() if memo else nullcontext():
            result = run_backtest(cfg, sliced, store, BaselineSelector(), run_id=run_id,
                                  # The analysis overlay is outside BASE: never claim a clean LIVE source tree.
                                  code=CodeVersion(git_commit=BASE, dirty=True), period=(start, end))
        summary = result.summary
        if summary.open_positions or summary.dust:
            invalid.append("NOT_TMN_FLAT")
        if summary.unresolved_orders or summary.execution_errors:
            invalid.append("EXECUTION_UNRESOLVED")
        if abs(summary.net_pnl_tmn - summary.realized_pnl_tmn) > Decimal("0.00000001"):
            invalid.append("UNREALIZED_RESIDUE")
        assert cfg.features is not None
        start_reg: dict[str, str] = {}
        for sym in data:
            agg = aggregate([b for b in data[sym] if b.ts_open < start], 14400, 3600)
            fs = compute_features(sym, "analysis-start", agg[-result.runtime.history_window:], start, 14400,
                                  None, cfg.features, result.runtime.lookback_bars)
            start_reg[sym] = f2_regime(fs, sym)
        mix: Counter[str] = Counter()
        for decision in store.decisions(run_id):
            if decision.features_ref:
                raw = store.get_payload(decision.features_ref)
                assert raw is not None
                payload = json.loads(raw)
                fs = FeatureSet.model_validate(payload["features"])
                assert decision.symbol is not None
                mix[f"{decision.symbol}:{f2_regime(fs, decision.symbol)}"] += 1
        trades: list[dict[str, Any]] = []
        for t in result.runtime.ledger.trades:
            trade = asdict(t)
            trade["opened_at"], trade["closed_at"] = t.opened_at.isoformat(), t.closed_at.isoformat()
            trade["terminal_mark"] = data[t.symbol][-1].close
            trade["holding_hours"] = Decimal(int((t.closed_at - t.opened_at).total_seconds())) / 3600
            trade["net_return_pct"] = t.net_pnl_tmn / (t.qty * t.avg_entry_price * (1 + cfg.costs.fee_rate)) * H
            trades.append(trade)
        row: dict[str, Any] = {
            "start": start.isoformat(), "end": end.isoformat(), "valid": not invalid,
            "invalid_reasons": ";".join(invalid), "starting_equity_tmn": summary.starting_capital_tmn,
            "ending_equity_tmn": summary.final_equity_tmn, "net_pnl_tmn": summary.net_pnl_tmn,
            "realized_pnl_tmn": summary.realized_pnl_tmn, "return_pct": summary.net_pnl_tmn / summary.starting_capital_tmn * H,
            "fees_tmn": summary.fees_tmn, "trades": summary.trades, "zero_trade": summary.trades == 0,
            "max_drawdown_pct": result.runtime.ledger.max_drawdown_fraction * H,
            "wins": summary.wins, "losses": summary.losses,
            "largest_trade_pnl_tmn": max((t["net_pnl_tmn"] for t in trades), default=Z),
            "without_largest_trade_tmn": summary.net_pnl_tmn - max(Z, max((t["net_pnl_tmn"] for t in trades), default=Z)),
            "regime_start": "|".join(f"{s}:{r}" for s, r in sorted(start_reg.items())),
            "regime_mix": dict(sorted(mix.items())), "utc_start_hour": start.hour,
            "tehran_start_hour": start.astimezone(ZoneInfo("Asia/Tehran")).strftime("%H:%M"),
            "exposure_pct": summary.time_in_market_pct, "risk_rejected": summary.risk_rejected,
            "gap_cycles": sum("GAP" in (d.block_reason or "") for d in store.decisions(run_id)),
            **terminal_attribution(trades, cfg.costs.fee_rate),
        }
        return row, trades
    finally:
        store.close()


def validated_dataset() -> Dataset:
    manifest_path = ROOT / "data/historical/MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    path = PACK / "datasets/1h"
    ds = load_dataset(path, expected_resolution_seconds=3600)
    for symbol in SYMBOLS:
        info = next(s for s in manifest["series"] if s["dataset"] == "1h" and s["symbol"] == symbol)
        if info["quality_status"] == "BLOCKED" or info["actual_interval_seconds"] != 3600:
            raise DatasetIntegrityError(f"BLOCKED/incorrect interval {symbol}")
        if ds.refs[symbol].content_sha256 != info["sha256"]:
            raise DatasetIntegrityError(f"committed pack hash mismatch {symbol}")
    # Do not pass TEST bars to ANY strategy, control or continuation study.
    return Dataset(ds.name, ds.path, {s: [b for b in ds.bars[s] if b.ts_open + timedelta(hours=1) <= DEV_END]
                                      for s in SYMBOLS}, ds.refs, ds.manifest)


def init_worker() -> None:
    global _worker_dataset
    _worker_dataset = validated_dataset()


def kernel_job(period: tuple[datetime, datetime]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assert _worker_dataset is not None
    start, end = period
    rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    # Across the six runs inputs are identical except host cost/universe, neither enters feature computation.
    with memoized_features():
        for book in BOOKS:
            for cost in DECIMAL_COSTS:
                rid = f"cw-{int(start.timestamp())}-{book}-{cost}"
                row, ts = kernel_window(variant(book, cost), _worker_dataset, start, end, run_id=rid)
                identity = {"book": book, "cost": cost, "window_id": int(start.timestamp())}
                rows.append({**identity, **row})
                trades.extend({**identity, **t} for t in ts)
    return rows, trades


def write_rows(path: Path, rows: Sequence[dict[str, Any]]) -> str:
    fields = sorted({k for row in rows for k in row})
    text = io.StringIO()
    writer = csv.DictWriter(text, fields, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, sort_keys=True, ensure_ascii=False, default=str) if isinstance(v, (dict, list))
                         else v for k, v in row.items()})
    raw = gzip.compress(text.getvalue().encode(), mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return sha(raw)


def read_rows(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(gzip.decompress(path.read_bytes()).decode())))


def verify_products(directory: Path, expected: dict[str, str]) -> None:
    for name, digest in expected.items():
        path = directory / name
        if not path.is_file() or sha(path.read_bytes()) != digest:
            raise DatasetIntegrityError(f"analytical product hash mismatch: {name}")


def require_valid_reference(row: dict[str, Any]) -> None:
    if not row["valid"]:
        raise DatasetIntegrityError(f"invalid reference slice: {row['invalid_reasons']}")


def group_evidence(rows: list[dict[str, Any]], trades: list[dict[str, Any]]) -> dict[str, Any]:
    out = summarize_windows(rows)
    valid = {r["window_id"] for r in rows if r["valid"]}
    good = [r for r in rows if r["valid"]]
    ts = [t for t in trades if t["window_id"] in valid]
    out["trade_concentration_tmn"] = concentration([t["net_pnl_tmn"] for t in ts])
    out["window_concentration_tmn"] = concentration([r["net_pnl_tmn"] for r in good])
    out["holding_hours"] = distribution([t["holding_hours"] for t in ts])
    out["per_trade_return_pct"] = distribution([t["net_return_pct"] for t in ts])
    out["fees_tmn"] = distribution([r["fees_tmn"] for r in good])
    out["forced_flatten"] = {"windows_pct": H * sum(r["forced_trades"] > 0 for r in good) / len(good) if good else None,
                             "trades": sum(r["forced_trades"] for r in good),
                             "pnl_tmn": sum((r["forced_pnl_tmn"] for r in good), Z),
                             "fees_tmn": sum((r["forced_exit_fee_tmn"] for r in good), Z),
                             "vs_terminal_mid_tmn": sum((r["forced_vs_terminal_mid_tmn"] for r in good), Z)}
    profitable = [r for r in good if r["net_pnl_tmn"] > 0]
    p90 = quantile([t["net_pnl_tmn"] for t in ts if t["net_pnl_tmn"] > 0], Decimal(".9"))
    out["winner_dependence"] = {"profitable_windows": len(profitable), "exceptional_threshold_tmn": p90,
                                "one_winner_pct": H * sum(r["without_largest_trade_tmn"] <= 0 for r in profitable) / len(profitable)
                                if profitable else None,
                                "exceptional_one_winner_pct": H * sum(r["without_largest_trade_tmn"] <= 0 and
                                    r["largest_trade_pnl_tmn"] >= (p90 or Z) for r in profitable) / len(profitable)
                                if profitable else None}
    out["utc_start_buckets"] = {str(h): summarize_windows([r for r in rows if r["utc_start_hour"] == h])
                                for h in range(0, 24, 4)}
    out["regime_start_buckets"] = {reg: summarize_windows([r for r in rows if r["regime_start"] == reg])
                                   for reg in sorted({r["regime_start"] for r in rows})}
    # Same 4h lattice, 18 offsets of mutually non-overlapping 72h runs; descriptive sensitivity, not a CI.
    out["nonoverlap_offsets"] = {str(offset): summarize_windows([r for r in rows if
        ((r["window_id"] - int(START.timestamp())) // 14400) % 18 == offset]) for offset in range(18)}
    return out


def annotate_outliers(rows: list[dict[str, Any]], trades: list[dict[str, Any]]) -> None:
    for book in BOOKS:
        for cost in DECIMAL_COSTS:
            selected = [r for r in rows if r["book"] == book and r["cost"] == cost and r["valid"]]
            ids = {r["window_id"] for r in selected}
            threshold = quantile([t["net_pnl_tmn"] for t in trades if t["book"] == book and t["cost"] == cost
                                  and t["window_id"] in ids and t["net_pnl_tmn"] > 0], Decimal(".9"))
            for row in selected:
                row["one_winner_dependent"] = row["net_pnl_tmn"] > 0 and row["without_largest_trade_tmn"] <= 0
                row["exceptional_trade_present"] = threshold is not None and row["largest_trade_pnl_tmn"] >= threshold
                row["exceptional_one_winner_dependent"] = row["one_winner_dependent"] and row["exceptional_trade_present"]


def summarize_controls(rows: list[dict[str, Any]]) -> dict[str, Any]:
    good = [r for r in rows if r["valid"]]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    f2: dict[tuple[str, str, int], Decimal] = {}
    for row in good:
        groups[f"{row['control']}/{row['book']}/{row['cost']}/{row['seed']}"].append(row)
        if row["control"] == "F2":
            f2[(row["book"], row["cost"], row["window_id"])] = row["return_pct"]
    results: dict[str, Any] = {}
    pairs: dict[str, Any] = {}
    activity: dict[str, Any] = {}
    random_groups: dict[str, dict[str, Any]] = defaultdict(dict)
    for key, rs in sorted(groups.items()):
        value = distribution([r["return_pct"] for r in rs])
        if key.startswith("RANDOM/"):
            name, seed = key.rsplit("/", 1)
            random_groups[name][seed] = value
        else:
            results[key] = value
        if not key.startswith("F2/"):
            pairs[key] = distribution([f2[(r["book"], r["cost"], r["window_id"])] - r["return_pct"] for r in rs])
    for name, seeds in sorted(random_groups.items()):
        results[name] = {"seed_count": len(seeds), "per_seed": seeds,
                         "mean_return_across_seeds_pct": distribution([v["mean"] for v in seeds.values()]),
                         "median_return_across_seeds_pct": distribution([v["median"] for v in seeds.values()])}
    activity_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in good:
        activity_rows[f"{row['control']}/{row['book']}/{row['cost']}"].append(row)
    for key, rs in activity_rows.items():
        activity[key] = {"observations": len(rs), "trades_per_window": distribution([Decimal(r["trades"]) for r in rs]),
                         "exposure_pct": distribution([r["exposure_pct"] for r in rs]),
                         "no_trade_pct": H * sum(r["trades"] == 0 for r in rs) / len(rs)}
    return {"results": results, "activity": activity, "paired_f2_minus_control_pct": pairs}


def research_controls(periods: list[tuple[datetime, datetime]], kernel_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Unchanged Phase 04 normalized simulator. Random entry rate fixed on TRAIN only, never VAL outcomes."""
    mk = {s: Market(s) for s in SYMBOLS}
    f2, off = parse("F2|4|48|5|1"), parse("F2|4|48|5|0")
    rates: dict[str, Decimal] = {}
    # Match ENTRY SIGNAL density on TRAIN within the same eligible regime pool, not ex-post winning trades.
    pools: dict[str, Plan] = {}
    for s, m in mk.items():
        p = m.regime_pool(f2, COSTS["base"])
        train_a, train_b = SLICES["TRAIN"][0][1], SLICES["TRAIN"][-1][2]
        eligible = [i for i in p.allowed if train_a <= m.h1.t[i] < train_b]
        n = sum(i in p.entry for i in eligible)
        rates[s] = Decimal(n) / len(eligible) if eligible else Z
        pools[s] = p
    randoms = {seed: {s: random_plan(pools[s], float(rates[s]), random.Random(7919 * seed + (0 if s == "BTCTMN" else 1)))
                      for s in SYMBOLS} for seed in range(1, RANDOM_SEEDS + 1)}
    rows: list[dict[str, Any]] = []
    valid = {(r["book"], r["cost"], r["window_id"]) for r in kernel_rows if r["valid"]}
    # Return and DD controls are normalized (%), never float TMN quantities.
    for start, end in periods:
        a, b = int(start.timestamp()), int(end.timestamp())
        for cost in DECIMAL_COSTS:
            plans = {"F2": {s: mk[s].plan(f2, COSTS[cost]) for s in SYMBOLS},
                     "F2_OFF": {s: mk[s].plan(off, COSTS[cost]) for s in SYMBOLS}}
            variants: list[tuple[str, int, dict[str, Plan]]] = [(name, 0, p) for name, p in plans.items()]
            variants += [("RANDOM", seed, p) for seed, p in randoms.items()]
            for name, seed, ps in variants:
                rs: dict[str, SimResult] = {s: simulate(mk[s].h1, ps[s], f2.rule(), COSTS[cost], a, b) for s in SYMBOLS}
                for book, syms in BOOKS.items():
                    selected = [rs[s] for s in syms]
                    net, dd = portfolio(selected)
                    ntr = sum(len(r.trades) for r in selected)
                    row: dict[str, Any] = {"window_id": a, "start": start.isoformat(), "cost": cost, "book": book,
                           "valid": (book, cost, a) in valid,
                           "control": name, "seed": seed, "return_pct": Decimal(str(net)) * H,
                           "max_drawdown_pct": Decimal(str(dd)) * H, "trades": ntr,
                           "exposure_pct": Decimal(sum(r.exposure_hours for r in selected)) /
                           max(1, sum(r.hours for r in selected)) * H}
                    rows.append(row)
            bh = {s: buy_hold(mk[s].h1, a, b, COSTS[cost]) for s in SYMBOLS}
            for book, syms in BOOKS.items():
                vals = [v for s in syms if (v := bh[s]) is not None]
                if len(vals) != len(syms):
                    raise RuntimeError("missing buy-hold endpoints")
                pct = Decimal(str(sum(vals) / len(vals))) * H
                rows.append({"window_id": a, "start": start.isoformat(), "cost": cost, "book": book,
                             "valid": (book, cost, a) in valid,
                             "control": "BUY_HOLD", "seed": 0, "return_pct": pct, "max_drawdown_pct": "",
                             "trades": len(syms), "exposure_pct": H})
    return rows, {"train_entry_signal_rates": rates, **summarize_controls(rows)}


def provenance() -> dict[str, Any]:
    paths = ["scripts/challenge_windows.py", "scripts/phase04r2_lib.py", "scripts/phase04r2_run.py",
             "src/wallex_agent/runtime/backtest.py", "src/wallex_agent/strategies/f2_tsm.py",
             "src/wallex_agent/features/engine.py", "uv.lock", "config/experiments/phase04r2/base-4h-f2.toml",
             "config/experiments/phase04r2/base-4h-f2-off.toml", "data/historical/MANIFEST.json"]
    manifest = json.loads((ROOT / "data/historical/MANIFEST.json").read_text())
    return {"frozen_code_commit": BASE, "analysis_code_identity": "content SHA-256 of scripts/challenge_windows.py; commit in git history",
            "f2_version": "f2-0.1.0", "f2_key": "F2|4|48|5|1", "regime_version": "regime-r2-0.1.0",
            "files_sha256": {p: sha((ROOT / p).read_bytes()) for p in paths},
            "datasets": [s for s in manifest["series"] if s["dataset"] == "1h" and s["symbol"] in SYMBOLS],
            "costs_per_side": {name: {"fee_rate": fee, "slippage_bps": slip} for name, (fee, slip) in DECIMAL_COSTS.items()},
            "starting_capital_tmn": Decimal(5_000_000), "evaluation_start": START, "evaluation_end_exclusive": DEV_END,
            "width_hours": 72, "step_hours": 4, "kernel_base_seconds": 3600, "random_seeds": RANDOM_SEEDS,
            "protected_test_evaluated": False, "network_calls": 0, "model_calls": 0,
            "raw_file_scope": "Complete source bytes are hash-verified; TEST tail is removed before any strategy/control evaluation",
            "backtest_manifest_dirty": True,
            "limits_status": "frozen simulation placeholders, approved_by_owner=false",
            "overlap": "Adjacent windows share 68/72 hours; reset hypothetical accounts, not independent samples or compound returns",
            "random_method": "TRAIN entry-signal density, same regime pool, ATR5 trail/stop, EMA signal exit and 168h cap; 20 fixed seeds",
            "controls_scope": "research simulator normalized fully invested, equal-weight BTC+ETH; not production risk-sized TMN",
            "forced_impact_definition": "Actual net liquidation proceeds minus same quantity at final observed gross mid; timing+sell fee+slip, not a future-hold counterfactual",
            "winner_removal_definition": "Subtract observed positive P&L only, hold all other fills/sizing fixed; diagnostic, not a resimulation",
            "exceptional_definition": "Top decile of positive trade TMN P&L within each valid book/cost distribution; retrospective diagnostic only"}


def run_analysis(output: Path, workers: int) -> dict[str, str]:
    frozen = subprocess.run(["git", "diff", "--quiet", BASE, "--", "src", "config", "uv.lock", "data/historical"],
                            cwd=ROOT, check=False)
    if frozen.returncode:
        raise DatasetIntegrityError("production/config/history tree differs from frozen checkpoint")
    periods = rolling_windows(START, DEV_END)
    kernel: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers, initializer=init_worker) as pool:
        for n, (rs, ts) in enumerate(pool.map(kernel_job, periods, chunksize=6), 1):
            kernel.extend(rs)
            trades.extend(ts)
            if n % 120 == 0 or n == len(periods):
                print(f"kernel windows {n}/{len(periods)}", flush=True)
    print("research controls", flush=True)
    controls, control_summary = research_controls(periods, kernel)
    print("non-overlapping full-slice reference trades", flush=True)
    ds = validated_dataset()
    references: list[dict[str, Any]] = []
    for cost in DECIMAL_COSTS:
        for name, a, b in SLICES["VAL"]:
            row, ts = kernel_window(variant("POOLED", cost), ds, datetime.fromtimestamp(a, UTC), datetime.fromtimestamp(b, UTC),
                                    run_id=f"ref-{cost}-{name}")
            require_valid_reference(row)
            references.extend({"cost": cost, "slice": name, **t} for t in ts)
    annotate_outliers(kernel, trades)
    products = {"WINDOWS.csv.gz": write_rows(output / "WINDOWS.csv.gz", kernel),
                "TRADES.csv.gz": write_rows(output / "TRADES.csv.gz", trades),
                "CONTROLS.csv.gz": write_rows(output / "CONTROLS.csv.gz", controls),
                "REFERENCE_TRADES.csv.gz": write_rows(output / "REFERENCE_TRADES.csv.gz", references)}
    summary: dict[str, Any] = {"schema_version": 1, "status": "ANALYSIS_ONLY", "provenance": provenance(),
        "products_sha256": dict(products), "unique_rolling_starts": len(periods), "kernel_runs": len(kernel),
        "kernel": {}, "research_controls": control_summary, "reference_trades": {}}
    for book in BOOKS:
        for cost in DECIMAL_COSTS:
            rs = [r for r in kernel if r["book"] == book and r["cost"] == cost]
            ts = [t for t in trades if t["book"] == book and t["cost"] == cost]
            summary["kernel"][f"{book}/{cost}"] = group_evidence(rs, ts)
    for cost in DECIMAL_COSTS:
        ts = [t for t in references if t["cost"] == cost]
        summary["reference_trades"][cost] = {
            "scope": "seven disjoint VAL slices, pooled kernel; each starts flat with 5,000,000 TMN",
            "concentration_tmn": concentration([t["net_pnl_tmn"] for t in ts]),
            "holding_hours": distribution([t["holding_hours"] for t in ts]),
            "per_trade_return_pct": distribution([t["net_return_pct"] for t in ts])}
    raw = canonical(summary)
    (output / "SUMMARY.json").write_bytes(raw)
    products["SUMMARY.json"] = sha(raw)
    (output / "HASHES.json").write_bytes(canonical(products))
    return products


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "verify"])
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    output = OUT if args.action == "run" else ROOT / "var/challenge-window-rerun"
    output.mkdir(parents=True, exist_ok=True)
    expected: dict[str, str] = {}
    if args.action == "verify":
        expected = json.loads((OUT / "HASHES.json").read_text())
        verify_products(OUT, expected)
    hashes = run_analysis(output, args.workers)
    if args.action == "verify":
        if hashes != expected:
            raise RuntimeError(f"deterministic rerun mismatch: {[k for k in hashes if hashes[k] != expected.get(k)]}")
        print("deterministic full rerun: all five product hashes identical", flush=True)
    print(json.dumps(hashes, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
