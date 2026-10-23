import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from wallex_agent.market.bars import Bar, WallexPayloadError, aggregate
from wallex_agent.market.dataset import DatasetIntegrityError, load_dataset, write_dataset
from wallex_agent.market.historical_pack import PublicArchive, audit_payload, profile_bars, split_bars
from wallex_agent.market.wallex_public import WallexPublicClient

START = datetime(2026, 10, 1, tzinfo=UTC)


def payload(minutes: list[int]) -> dict[str, Any]:
    return {"s": "ok", "t": [int(START.timestamp()) + minute * 60 for minute in minutes],
            "o": ["100"] * len(minutes), "h": ["102"] * len(minutes),
            "l": ["99"] * len(minutes), "c": ["101"] * len(minutes), "v": ["0.5"] * len(minutes)}


def test_payload_audit_preserves_raw_defects_and_drops_unclosed_bar() -> None:
    result = audit_payload(payload([2, 0, 1, 1, 4]), 900, now=START + timedelta(minutes=4, seconds=30))
    assert [bar.ts_open for bar in result.bars] == [START, START + timedelta(minutes=1), START + timedelta(minutes=2)]
    assert result.evidence["raw_rows"] == 5
    assert result.evidence["duplicate_rows"] == 1
    assert result.evidence["out_of_order_pairs"] == 1
    assert result.evidence["incomplete_rows_dropped"] == 1
    assert result.evidence["actual_interval_seconds"] == 60
    assert "RESOLUTION_MISMATCH" in result.evidence["flags"]


@pytest.mark.parametrize(("field", "value"), [("o", "0"), ("l", "-1"), ("h", "NaN"),
                                                   ("c", "Infinity"), ("v", "-0.01"), ("h", "99")])
def test_invalid_price_volume_or_geometry_is_not_exported(field: str, value: str) -> None:
    raw = payload([0, 1])
    raw[field][0] = value
    with pytest.raises((ValueError, WallexPayloadError)):
        audit_payload(raw, 60, now=START + timedelta(days=1))


@pytest.mark.parametrize("timestamp", [True, 1.5, -60, "123.5"])
def test_invalid_epoch_timestamp_is_rejected(timestamp: Any) -> None:
    raw = payload([0, 1])
    raw["t"][0] = timestamp
    with pytest.raises(ValueError):
        audit_payload(raw, 60, now=START + timedelta(days=1))


def test_conflicting_duplicate_is_not_silently_overwritten() -> None:
    raw = payload([0, 0, 1])
    raw["c"][1] = "100"
    with pytest.raises(WallexPayloadError, match="duplicate"):
        audit_payload(raw, 60, now=START + timedelta(days=1))


def test_coverage_counts_leading_internal_and_trailing_missing_minutes() -> None:
    bars = audit_payload(payload([1, 2, 4]), 60, now=START + timedelta(days=1)).bars
    report = profile_bars(bars, 60, start=START, end=START + timedelta(minutes=6))
    assert report["expected_bars"] == 6
    assert report["missing_bars"] == 3
    assert report["gap_count"] == 3
    assert report["observed_spacing_counts_seconds"] == {"60": 1, "120": 1}
    assert report["gaps"][0]["start_utc"] == START.isoformat()
    assert report["quality_status"] == "PARTIAL"


def test_sparse_series_reports_interval_ambiguity_without_filling() -> None:
    bars = audit_payload(payload([0, 2, 4]), 60, now=START + timedelta(days=1)).bars
    report = profile_bars(bars, 60, start=START, end=START + timedelta(minutes=6))
    assert report["actual_interval_seconds"] == 120
    assert report["missing_bars"] == 3
    assert "RESOLUTION_MISMATCH" in report["flags"]


def test_split_uses_bar_close_and_never_leaks_a_straddling_bar() -> None:
    bars = audit_payload(payload([0, 1, 2, 3, 4]), 60, now=START + timedelta(days=1)).bars
    parts = split_bars(bars, 60, [START, START + timedelta(minutes=2, seconds=30), START + timedelta(minutes=5)])
    assert [[int((bar.ts_open - START).total_seconds()) for bar in part] for part in parts] == [[0, 60], [180, 240]]
    with pytest.raises(ValueError):
        split_bars(bars, 60, [START, START])


def test_existing_aggregator_keeps_only_complete_buckets_after_validation() -> None:
    bars = audit_payload(payload(list(range(15)) + list(range(16, 30))), 60, now=START + timedelta(days=1)).bars
    report = profile_bars(bars, 60, start=START, end=START + timedelta(minutes=30))
    assert report["missing_bars"] == 1
    combined = aggregate(bars, 900)
    assert len(combined) == 1
    assert combined[0].volume == Decimal("7.5")


def test_hourly_dataset_requires_explicit_interval_and_keeps_correct_data_ref(tmp_path: Path) -> None:
    bars = audit_payload(payload([0, 60]), 3600, now=START + timedelta(days=1)).bars
    first = write_dataset(tmp_path / "a", "hourly", {"BTCTMN": bars}, extra={}, resolution_seconds=3600)
    second = write_dataset(tmp_path / "b", "hourly", {"BTCTMN": bars}, extra={}, resolution_seconds=3600)
    assert first["symbols"]["BTCTMN"]["sha256"] == second["symbols"]["BTCTMN"]["sha256"]
    with pytest.raises(DatasetIntegrityError, match="resolution"):
        load_dataset(tmp_path / "a")
    loaded = load_dataset(tmp_path / "a", expected_resolution_seconds=3600)
    assert loaded.refs["BTCTMN"].resolution_seconds == 3600
    assert loaded.bars["BTCTMN"] == bars


def test_public_archive_bounds_read_retries_and_resumes_without_network(tmp_path: Path) -> None:
    attempts: list[str] = []
    waits: list[float] = []
    raw = json.dumps(payload([0, 1])).encode()

    def get(url: str, timeout: float) -> bytes:
        attempts.append(url)
        if len(attempts) < 3:
            raise TimeoutError("public read timeout")
        return raw

    archive = PublicArchive(tmp_path, get=get, sleep=waits.append, min_interval_s=1)
    client = WallexPublicClient(get=archive, min_interval_s=0)
    first = client.candles("BTCTMN", "1", int(START.timestamp()), int(START.timestamp()) + 119)
    assert first["t"] == payload([0, 1])["t"]
    assert len(attempts) == 3
    assert sum(waits) >= 6
    assert archive.receipts[0]["sha256"] == hashlib.sha256(raw).hexdigest()

    def forbidden_network(url: str, timeout: float) -> bytes:
        raise AssertionError("offline resume attempted network")

    resumed = PublicArchive(tmp_path, get=forbidden_network, offline=True)
    assert WallexPublicClient(get=resumed, min_interval_s=0).candles(
        "BTCTMN", "1", int(START.timestamp()), int(START.timestamp()) + 119) == first
    assert resumed.receipts == archive.receipts
    Path(tmp_path / archive.receipts[0]["file"]).write_bytes(b'{}')
    with pytest.raises(DatasetIntegrityError, match="hash"):
        WallexPublicClient(get=resumed, min_interval_s=0).candles(
            "BTCTMN", "1", int(START.timestamp()), int(START.timestamp()) + 119)


@pytest.mark.parametrize("url", ["https://api.wallex.ir/v1/account/balances",
    "https://api.wallex.ir/v1/account/orders", "https://other.example/v1/markets",
    "http://api.wallex.ir/v1/markets", "https://user:password@api.wallex.ir/v1/markets",
    "https://api.wallex.ir/v1/markets?api_key=placeholder", "https://api.wallex.ir/v1/udf/history?symbol=../bad"])
def test_archive_denies_unknown_or_private_urls_before_io(tmp_path: Path, url: str) -> None:
    def forbidden_network(url: str, timeout: float) -> bytes:
        raise AssertionError("unauthorized I/O")

    archive = PublicArchive(tmp_path, get=forbidden_network)
    with pytest.raises(ValueError):
        archive(url, 1)


def test_failed_payload_is_not_cached_and_read_attempts_are_bounded(tmp_path: Path) -> None:
    attempts: list[str] = []

    def get(url: str, timeout: float) -> bytes:
        attempts.append(url)
        return b'{"success":false,"message":"unavailable"}'

    archive = PublicArchive(tmp_path, get=get, sleep=lambda _: None)
    with pytest.raises(WallexPayloadError):
        archive("https://api.wallex.ir/v1/markets", 1)
    assert len(attempts) == 3
    assert not archive.receipts
    assert not list(tmp_path.glob("*.json"))


def test_chunk_merge_checks_conflicting_overlaps_and_keeps_source_gaps() -> None:
    from wallex_agent.market.historical_pack import collect_series

    responses = iter([payload([0, 1]), payload([1, 2, 4])])
    client = WallexPublicClient(get=lambda url, timeout: json.dumps(next(responses)).encode(), min_interval_s=0)
    bars, evidence = collect_series(client, "BTCTMN", "1", 60, start=START,
                                    end=START + timedelta(minutes=6), chunk_seconds=180)
    assert len(bars) == 4
    assert evidence["cross_chunk_duplicates"] == 1
    assert evidence["out_of_request_rows"] == 2
    assert evidence["quality"]["missing_bars"] == 2

    conflicting = payload([1, 2, 4])
    conflicting["c"][0] = "100"
    responses = iter([payload([0, 1]), conflicting])
    with pytest.raises(WallexPayloadError, match="conflicting"):
        collect_series(client, "BTCTMN", "1", 60, start=START,
                       end=START + timedelta(minutes=6), chunk_seconds=180)


def test_pack_offline_regeneration_reproduces_manifest_and_views(tmp_path: Path) -> None:
    from wallex_agent.market.historical_pack import build_pack

    def get(url: str, timeout: float) -> bytes:
        if "/udf/history?" in url:
            return json.dumps(payload(list(range(30)))).encode()
        if "/hector/" in url:
            return b'{"success":true,"result":{"markets":[{"symbol":"BTCTMN","is_spot":true,"quote_asset":"TMN","price_precision":0,"amount_precision":8,"is_market_type_enable":true}]}}'
        if "/prop/" in url:
            return b'{"success":true,"result":{"unavailableMarkets":[]}}'
        if "/udf/config" in url:
            return b'{"supported_resolutions":["1","60","1D"]}'
        return b'{"success":true,"result":{"symbols":{"BTCTMN":{"minQty":"0.000001","minNotional":"0","isMarketTypeEnable":true}}}}'

    plan: dict[str, Any] = {"schema_version": "1", "name": "fixture", "symbols": ["BTCTMN"],
        "end_utc": (START + timedelta(minutes=30)).isoformat(),
        "datasets": [{"id": "1m", "resolution": "1", "seconds": 60, "start_utc": START.isoformat(),
                      "chunk_seconds": 1800}],
        "views": [{"id": "15m", "source": "1m", "seconds": 900}]}
    archive = PublicArchive(tmp_path / "http", get=get, sleep=lambda _: None)
    first = build_pack(plan, archive, tmp_path / "a")
    second = build_pack(plan, PublicArchive(tmp_path / "http", offline=True), tmp_path / "b")
    assert first == second
    third = build_pack(plan, archive, tmp_path / "c")
    assert third == first
    assert len(first["series"]) == 2
    assert first["series"][0]["row_count"] == 30
    assert first["series"][1]["row_count"] == 2
    assert load_dataset(tmp_path / "a" / "datasets" / "1m").bars["BTCTMN"][0].open == Decimal("100")
    assert first["metadata"]["markets"]["BTCTMN"]["min_notional"] == "0"
    assert first["metadata"]["account_fee"] is None
    for series in first["series"]:
        assert hashlib.sha256((tmp_path / "a" / series["file"]).read_bytes()).hexdigest() == series["sha256"]


@pytest.mark.parametrize("defect", ["unknown_key", "unknown_dataset_key", "wrong_interval", "naive_start", "future_end"])
def test_invalid_plan_is_denied_before_any_public_io(tmp_path: Path, defect: str) -> None:
    from wallex_agent.market.historical_pack import build_pack

    plan: dict[str, Any] = {"schema_version": "1", "name": "fixture", "symbols": ["BTCTMN"],
        "end_utc": (START + timedelta(minutes=30)).isoformat(),
        "datasets": [{"id": "1m", "resolution": "1", "seconds": 60,
                      "start_utc": START.isoformat(), "chunk_seconds": 1800}]}
    if defect == "unknown_key":
        plan["autonomy"] = "AUTONOMOUS"
    elif defect == "unknown_dataset_key":
        plan["datasets"][0]["secret"] = "placeholder"
    elif defect == "wrong_interval":
        plan["datasets"][0]["seconds"] = 3600
    elif defect == "naive_start":
        plan["datasets"][0]["start_utc"] = "2026-10-01T00:00:00"
    else:
        plan["end_utc"] = "2099-01-01T00:00:00+00:00"

    def forbidden_network(url: str, timeout: float) -> bytes:
        raise AssertionError("invalid plan reached the network")

    with pytest.raises(ValueError):
        build_pack(plan, PublicArchive(tmp_path / "http", get=forbidden_network, sleep=lambda _: None), tmp_path / "out")


@pytest.mark.parametrize("offset", ["duplicate", "unsorted", "off_grid", "non_utc"])
def test_split_rejects_invalid_input_instead_of_hiding_defects(offset: str) -> None:
    bars = audit_payload(payload([0, 1]), 60, now=START + timedelta(days=1)).bars
    if offset == "duplicate":
        bars.append(bars[0])
    elif offset == "unsorted":
        bars.reverse()
    else:
        stamp = START + timedelta(seconds=1) if offset == "off_grid" else START.replace(tzinfo=None)
        bars[0] = Bar(stamp, Decimal(100), Decimal(102), Decimal(99), Decimal(101), Decimal(1))
    with pytest.raises(ValueError):
        split_bars(bars, 60, [START, START + timedelta(days=1)])


def test_archive_does_not_retry_permanent_http_errors(tmp_path: Path) -> None:
    import urllib.error
    from email.message import Message

    calls: list[str] = []

    def get(url: str, timeout: float) -> bytes:
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "not found", Message(), None)

    with pytest.raises(urllib.error.HTTPError):
        PublicArchive(tmp_path, get=get, sleep=lambda _: None)("https://api.wallex.ir/v1/markets", 1)
    assert len(calls) == 1


def test_prefetch_bounds_concurrency_and_offline_export_needs_no_http(tmp_path: Path) -> None:
    import threading
    import time

    from wallex_agent.market.historical_pack import prefetch

    lock = threading.Lock()
    active = 0
    maximum = 0
    calls: list[str] = []

    def get(url: str, timeout: float) -> bytes:
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            calls.append(url)
        time.sleep(0.02)
        with lock:
            active -= 1
        return json.dumps(payload([0, 1]) if "/history?" in url else {"success": True}).encode()

    plan: dict[str, Any] = {"schema_version": "1", "name": "fixture", "symbols": ["BTCTMN"],
        "end_utc": (START + timedelta(minutes=4)).isoformat(),
        "datasets": [{"id": "1m", "resolution": "1", "seconds": 60,
                      "start_utc": START.isoformat(), "chunk_seconds": 120}]}
    archive = PublicArchive(tmp_path, get=get, min_interval_s=0)
    assert prefetch(plan, archive, workers=2) == 6
    assert maximum == 2
    assert prefetch(plan, PublicArchive(tmp_path, offline=True), workers=2) == 0
    assert len(calls) == 6
    with pytest.raises(ValueError):
        prefetch(plan, archive, workers=3)


def test_collection_quarantines_source_geometry_defect_without_repairing_prices() -> None:
    from wallex_agent.market.historical_pack import collect_series

    raw = payload([0, 1, 2, 3])
    raw["c"][1] = "103"
    client = WallexPublicClient(get=lambda url, timeout: json.dumps(raw).encode(), min_interval_s=0)
    bars, evidence = collect_series(client, "BTCTMN", "1", 60, start=START,
                                    end=START + timedelta(minutes=4), chunk_seconds=240)
    assert [bar.ts_open for bar in bars] == [START, START + timedelta(minutes=2), START + timedelta(minutes=3)]
    assert evidence["invalid_source_rows"] == 1
    assert evidence["quality"]["missing_bars"] == 1
    assert evidence["quality"]["quality_status"] == "PARTIAL"
    rejected = evidence["chunks"][0]["invalid_rows"][0]
    assert rejected["t"] == int(START.timestamp()) + 60
    assert rejected["values"]["c"] == "103"
    assert "INVALID_SOURCE_BAR_QUARANTINED" in evidence["quality"]["flags"]


def test_quarantine_does_not_hide_conflicting_valid_duplicates() -> None:
    raw = payload([0, 1, 1, 2])
    raw["h"][0] = "98"
    raw["c"][2] = "100"
    with pytest.raises(WallexPayloadError, match="duplicate"):
        audit_payload(raw, 60, now=START + timedelta(days=1), quarantine_invalid_rows=True)


def test_native_daily_shifted_opens_are_flagged_without_timestamp_repair() -> None:
    from wallex_agent.market.historical_pack import collect_series

    raw = payload([0, 1440, 2940, 4380, 5760])
    client = WallexPublicClient(get=lambda url, timeout: json.dumps(raw).encode(), min_interval_s=0)
    bars, evidence = collect_series(client, "BTCTMN", "1D", 86400, start=START,
                                    end=START + timedelta(days=5), chunk_seconds=432000)
    assert [bar.ts_open for bar in bars] == [START, START + timedelta(days=1), START + timedelta(days=4)]
    assert evidence["invalid_source_rows"] == 2
    assert evidence["quality"]["missing_bars"] == 2
    assert evidence["chunks"][0]["invalid_rows"][0]["t"] == int(START.timestamp()) + 2940 * 60
    assert evidence["quality"]["quality_status"] == "BLOCKED"


@pytest.mark.parametrize("missing_member", ["receipt", "raw"])
def test_partial_cache_is_preserved_without_silent_refetch(tmp_path: Path, missing_member: str) -> None:
    calls: list[str] = []

    def get(url: str, timeout: float) -> bytes:
        calls.append(url)
        return json.dumps(payload([0, 1]) if len(calls) == 1 else payload([2, 3])).encode()

    url = "https://api.wallex.ir/v1/udf/history?symbol=BTCTMN&resolution=1&from=1&to=9999999999"
    archive = PublicArchive(tmp_path, get=get, sleep=lambda _: None)
    archive(url, 1)
    raw_path = tmp_path / archive.receipts[0]["file"]
    receipt_path = raw_path.with_name(raw_path.stem + ".receipt.json")
    missing, retained = (receipt_path, raw_path) if missing_member == "receipt" else (raw_path, receipt_path)
    missing.unlink()
    retained_bytes = retained.read_bytes()
    with pytest.raises(DatasetIntegrityError, match="partial"):
        archive(url, 1)
    assert calls == [url]
    assert retained.read_bytes() == retained_bytes
    assert not missing.exists()


def test_conflicting_cached_receipt_cannot_leave_stale_in_memory_provenance(tmp_path: Path) -> None:
    url = "https://api.wallex.ir/v1/udf/history?symbol=BTCTMN&resolution=1&from=1&to=9999999999"
    archive = PublicArchive(tmp_path, get=lambda url, timeout: json.dumps(payload([0, 1])).encode(), sleep=lambda _: None)
    archive(url, 1)
    raw_path = tmp_path / archive.receipts[0]["file"]
    receipt_path = raw_path.with_name(raw_path.stem + ".receipt.json")
    changed = json.dumps(payload([2, 3])).encode()
    receipt = json.loads(receipt_path.read_text())
    receipt["sha256"] = hashlib.sha256(changed).hexdigest()
    receipt["bytes"] = len(changed)
    raw_path.write_bytes(changed)
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(DatasetIntegrityError, match="receipt"):
        archive(url, 1)


@pytest.mark.parametrize("minutes", [[0, 60, 120], [0, 2, 4], [0]])
def test_coarser_or_unverified_chunk_never_becomes_executable_minute_data(minutes: list[int]) -> None:
    from wallex_agent.market.historical_pack import collect_series

    client = WallexPublicClient(get=lambda url, timeout: json.dumps(payload(minutes)).encode(), min_interval_s=0)
    with pytest.raises(WallexPayloadError, match="interval"):
        collect_series(client, "BTCTMN", "1", 60, start=START,
                       end=START + timedelta(hours=3), chunk_seconds=10800)
