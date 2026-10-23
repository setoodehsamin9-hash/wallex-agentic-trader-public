"""Analysis-only arithmetic and boundary tests; no market or model network access."""

import importlib
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from support.synthetic import metadata_dir

from wallex_agent.contracts import FeatureSet, FeatureValue, RegimeLabel
from wallex_agent.market.bars import Bar
from wallex_agent.market.dataset import Dataset, DatasetIntegrityError, load_dataset, write_dataset
from wallex_agent.runtime.config import RunConfig, load_run_config

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
D = Decimal
T0 = datetime(2026, 8, 1, tzinfo=UTC)


def analysis() -> ModuleType:
    return importlib.import_module("challenge_windows")


def test_rolling_windows_keep_72h_and_include_last_exact_boundary() -> None:
    out = analysis().rolling_windows(T0, T0 + timedelta(hours=80))
    assert out == [(T0 + timedelta(hours=h), T0 + timedelta(hours=h + 72)) for h in (0, 4, 8)]


@pytest.mark.parametrize("start,end", [
    (T0.replace(tzinfo=None), T0 + timedelta(days=4)),
    (T0 + timedelta(hours=1), T0 + timedelta(days=4)),
    (T0, datetime(2026, 10, 14, tzinfo=UTC)),
])
def test_windows_refuse_naive_off_grid_or_protected_test(start: datetime, end: datetime) -> None:
    with pytest.raises(ValueError):
        analysis().rolling_windows(start, end)


def test_distribution_preserves_zero_and_uses_linear_decimal_quantiles() -> None:
    out = analysis().distribution([D(-4), D(-2), D(0), D(2), D(9)])
    assert out["positive_pct"] == D(40)
    assert out["negative_pct"] == D(40)
    assert out["zero_pct"] == D(20)
    assert out["median"] == 0 and out["mean"] == 1
    assert out["p10"] == D("-3.2") and out["p25"] == -2
    assert out["p75"] == 2 and out["p90"] == D("6.2")


def test_concentration_uses_gross_winners_and_removes_only_positive_winners() -> None:
    out = analysis().concentration([D(100), D(10), D(-50), D(-20), D(0)])
    assert out["net_sum"] == 40 and out["gross_positive"] == 110
    assert out["without_largest_winner"] == -60
    assert out["without_top3_winners"] == -70
    assert out["top"]["10"]["n"] == 1
    assert out["top"]["10"]["share_net_pct"] == 250
    assert out["top"]["10"]["share_gross_positive_pct"] == D(100) / 110 * 100
    losses = analysis().concentration([D(-7), D(-3)])
    assert losses["without_largest_winner"] == -10
    assert losses["top"]["10"]["share_gross_positive_pct"] is None


def test_invalid_nonflat_windows_are_visible_but_excluded_from_return_distribution() -> None:
    out = analysis().summarize_windows([
        {"valid": True, "return_pct": D(2), "max_drawdown_pct": D(1), "trades": 1, "start": "a"},
        {"valid": True, "return_pct": D(0), "max_drawdown_pct": D(0), "trades": 0, "start": "b"},
        {"valid": False, "return_pct": D(100), "max_drawdown_pct": D(0), "trades": 0, "start": "c"},
    ])
    assert out["scheduled_windows"] == 3 and out["valid_windows"] == 2 and out["invalid_windows"] == 1
    assert out["return_pct"]["mean"] == 1 and out["no_trade_pct"] == 50
    assert out["worst_window"]["start"] == "b"


def test_terminal_attribution_separates_realized_pnl_from_forced_exit_cost() -> None:
    out = analysis().terminal_attribution([
        {"exit_reason": "LIQUIDATION", "net_pnl_tmn": D(90), "qty": D(2),
         "avg_exit_price": D(110), "terminal_mark": D(112)},
        {"exit_reason": "EXIT_STOP", "net_pnl_tmn": D(-20), "qty": D(1),
         "avg_exit_price": D(99), "terminal_mark": D(112)},
    ], D("0.01"))
    assert out["forced_trades"] == 1 and out["forced_pnl_tmn"] == 90
    assert out["forced_exit_fee_tmn"] == D("2.2")
    # 2 * (110 * .99 - 112) = -6.2: final raw close vs actual net proceeds.
    assert out["forced_vs_terminal_mid_tmn"] == D("-6.2")


def test_regime_is_the_f2_rank_regime_including_priority_and_ties() -> None:
    def fs(vol: str, er: str, lag: str) -> FeatureSet:
        vals = {"close.1": D(110), "vol_rank.14": D(vol), "er_rank.42": D(er), "close_lag.42": D(lag)}
        return FeatureSet(snapshot_id="s", feature_engine_version="features-1.1.0",
                          deterministic_regime=RegimeLabel.RANGE,
                          features=tuple(FeatureValue(feature_id=f"BTCTMN.{k}", name=k.split(".")[0],
                                                      value=v, unit="", window_bars=0) for k, v in vals.items()))
    f = analysis().f2_regime
    assert f(fs(".95", ".9", "100"), "BTCTMN") == "SHOCK"
    assert f(fs(".20", ".9", "100"), "BTCTMN") == "QUIET"
    assert f(fs(".4", ".5", "100"), "BTCTMN") == "TREND_UP"
    assert f(fs(".4", ".5", "110"), "BTCTMN") == "TREND_DOWN"
    assert f(fs(".4", ".49", "120"), "BTCTMN") == "RANGE"


@pytest.fixture
def kernel_case(tmp_path: Path) -> tuple[RunConfig, Dataset]:
    start = T0 - timedelta(hours=4600)
    bars: list[Bar] = []
    for i in range(4600 + 73):
        p = D(100_000_000 + i * 30_000)
        bars.append(Bar(start + timedelta(hours=i), p, p + 70_000, p - 50_000, p + 10_000, D("1.5")))
    p = tmp_path / "hourly"
    write_dataset(p, "fixture-hourly", {"BTCTMN": bars}, extra={}, resolution_seconds=3600)
    metadata_dir(p / "metadata")
    # The pre-existing unfiltered twin supplies an active synthetic path; F2 itself is untouched.
    cfg = load_run_config(ROOT / "config/experiments/phase04r2/base-4h-f2-off.toml")
    data = cfg.model_dump(mode="python")
    data["run"]["symbols"] = ["BTCTMN"]
    return RunConfig.model_validate(data), load_dataset(p, expected_resolution_seconds=3600)


def test_kernel_flatten_cash_identity_cache_parity_and_no_future_reads(kernel_case: tuple[RunConfig, Dataset]) -> None:
    cfg, ds = kernel_case
    mod = analysis()
    end = T0 + timedelta(hours=72)
    direct, direct_trades = mod.kernel_window(cfg, ds, T0, end, run_id="fixture", memo=False)
    cached, cached_trades = mod.kernel_window(cfg, ds, T0, end, run_id="fixture", memo=True)
    assert direct == cached and direct_trades == cached_trades
    assert direct["valid"] and direct["trades"] > 0 and direct["forced_trades"] > 0
    assert direct["ending_equity_tmn"] == direct["starting_equity_tmn"] + direct["realized_pnl_tmn"]
    assert all(t["opened_at"] >= T0.isoformat() and t["closed_at"] <= end.isoformat() for t in direct_trades)
    trimmed = Dataset(ds.name, ds.path, {s: [b for b in bs if b.ts_open < end] for s, bs in ds.bars.items()}, ds.refs, ds.manifest)
    assert mod.kernel_window(cfg, trimmed, T0, end, run_id="fixture", memo=False) == (direct, direct_trades)


def test_missing_terminal_bar_cannot_be_a_valid_challenge_result(kernel_case: tuple[RunConfig, Dataset]) -> None:
    cfg, ds = kernel_case
    end = T0 + timedelta(hours=72)
    shortened = Dataset(ds.name, ds.path, {s: [b for b in bs if b.ts_open < end - timedelta(hours=1)]
                                         for s, bs in ds.bars.items()}, ds.refs, ds.manifest)
    row, _ = analysis().kernel_window(cfg, shortened, T0, end, run_id="missing", memo=False)
    assert not row["valid"] and "MISSING_TERMINAL_BAR" in row["invalid_reasons"]


def test_compact_export_is_byte_deterministic_and_roundtrips_decimals(tmp_path: Path) -> None:
    rows: list[dict[str, Any]] = [{"a": D("1.234567890123456789"), "b": "سلام", "c": 7}]
    mod = analysis()
    a, b = tmp_path / "a.csv.gz", tmp_path / "b.csv.gz"
    assert mod.write_rows(a, rows) == mod.write_rows(b, rows)
    assert a.read_bytes() == b.read_bytes()
    assert mod.read_rows(a) == [{"a": "1.234567890123456789", "b": "سلام", "c": "7"}]


def test_controls_pair_only_valid_same_windows_and_expose_activity_mismatch() -> None:
    rows: list[dict[str, Any]] = []
    for name, seed, values, count in (("F2", 0, [2, -1, 90], 1), ("BUY_HOLD", 0, [4, -4, 99], 1),
                                       ("RANDOM", 1, [1, -2, 100], 2)):
        for i, value in enumerate(values):
            rows.append({"control": name, "seed": seed, "book": "BTC", "cost": "base", "window_id": i,
                         "valid": i < 2, "return_pct": D(value), "trades": count, "exposure_pct": D(50)})
    out = analysis().summarize_controls(rows)
    assert out["results"]["F2/BTC/base/0"]["mean"] == D(".5")
    pair = out["paired_f2_minus_control_pct"]["BUY_HOLD/BTC/base/0"]
    assert pair["n"] == 2 and pair["mean"] == D(".5")
    assert out["activity"]["RANDOM/BTC/base"]["trades_per_window"]["mean"] == 2
    assert out["activity"]["F2/BTC/base"]["trades_per_window"]["mean"] == 1


def test_exceptional_flag_is_retrospective_positive_decile_and_one_winner_dependency() -> None:
    rows = [{"book": "BTC", "cost": "base", "window_id": i, "valid": True,
             "net_pnl_tmn": D(5), "largest_trade_pnl_tmn": D(v), "without_largest_trade_tmn": D(5 - v)}
            for i, v in enumerate([1, 100])]
    trades = [{"book": "BTC", "cost": "base", "window_id": i, "net_pnl_tmn": D(v)} for i, v in enumerate([1, 100])]
    analysis().annotate_outliers(rows, trades)
    assert not rows[0]["exceptional_one_winner_dependent"]
    assert rows[1]["exceptional_one_winner_dependent"]


def test_product_hash_verification_checks_actual_files_and_rejects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "product"
    path.write_bytes(b"good")
    # SHA-256('good'), independently fixed here.
    expected = {"product": "770e607624d689265ca6c44884d0807d9b054d23c473c106c72be9de08b7376c"}
    analysis().verify_products(tmp_path, expected)
    path.write_bytes(b"bad")
    with pytest.raises(DatasetIntegrityError, match="product"):
        analysis().verify_products(tmp_path, expected)


def test_reference_slice_cannot_silently_contribute_nonflat_trades() -> None:
    with pytest.raises(DatasetIntegrityError, match="reference"):
        analysis().require_valid_reference({"valid": False, "invalid_reasons": "NOT_TMN_FLAT"})
    analysis().require_valid_reference({"valid": True, "invalid_reasons": ""})
