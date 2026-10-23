"""Offline-verifiable public research data; never used by trading decisions."""

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from .bars import Bar, WallexPayloadError, aggregate, parse_udf_history
from .dataset import DatasetIntegrityError, write_dataset
from .meta import parse_market_meta
from .wallex_public import Getter, WallexPublicClient, urllib_get


@dataclass(frozen=True)
class PayloadAudit:
    bars: list[Bar]
    evidence: dict[str, Any]


def audit_payload(payload: dict[str, Any], requested_seconds: int, *, now: datetime,
                  quarantine_invalid_rows: bool = False) -> PayloadAudit:
    if quarantine_invalid_rows:
        return _audit_quarantined(payload, requested_seconds, now=now)
    _utc(now)
    if requested_seconds <= 0:
        raise ValueError("positive resolution required")
    timestamp_value: Any = payload.get("t", [])
    if not isinstance(timestamp_value, list):
        raise ValueError("timestamps must be an array")
    untyped_stamps = cast(list[Any], timestamp_value)
    if any(type(stamp) is not int or stamp < 0 for stamp in untyped_stamps):
        raise ValueError("timestamps must be non-negative integer epoch seconds")
    timestamps = cast(list[int], untyped_stamps)
    unique = sorted(set(timestamps))
    spacings = Counter(right - left for left, right in zip(unique, unique[1:]))
    observed = min(spacings) if spacings else None
    interval = observed or requested_seconds
    if any(stamp % min(interval, requested_seconds) for stamp in unique):
        raise ValueError("timestamps do not align to the candle grid")
    if payload.get("s") == "ok":
        for column in ("o", "h", "l", "c", "v"):
            values = payload.get(column)
            if not isinstance(values, list) or len(cast(list[Any], values)) != len(timestamps):
                raise ValueError("candle arrays have different lengths")
            for value in cast(list[Any], values):
                try:
                    number = Decimal(str(value))
                except InvalidOperation as exc:
                    raise ValueError("invalid numeric candle value") from exc
                if not number.is_finite() or (number < 0 if column == "v" else number <= 0):
                    raise ValueError("non-finite or non-positive price / negative volume")
    bars, parser_flags = parse_udf_history(payload, requested_seconds, now=now)
    flags = {flag.value for flag in parser_flags}
    if observed is None:
        flags.add("INTERVAL_UNVERIFIED")
    duplicates = len(timestamps) - len(unique)
    inversions = sum(right < left for left, right in zip(timestamps, timestamps[1:]))
    if duplicates:
        flags.add("RAW_DUPLICATES")
    if inversions:
        flags.add("RAW_OUT_OF_ORDER")
    incomplete = sum(stamp + interval > now.timestamp() for stamp in unique)
    if incomplete:
        flags.add("INCOMPLETE_FINAL_BAR_DROPPED")
    return PayloadAudit(bars, {
        "raw_rows": len(timestamps), "duplicate_rows": duplicates, "out_of_order_pairs": inversions,
        "incomplete_rows_dropped": incomplete, "actual_interval_seconds": observed,
        "observed_spacing_counts_seconds": {str(key): value for key, value in sorted(spacings.items())},
        "flags": sorted(flags), "invalid_source_rows": 0, "invalid_rows": [],
    })


def _audit_quarantined(payload: dict[str, Any], requested_seconds: int, *, now: datetime) -> PayloadAudit:
    try:
        return audit_payload(payload, requested_seconds, now=now)
    except (ValueError, WallexPayloadError) as exc:
        message = str(exc)
        if not (message.startswith("inconsistent OHLC at ") or message in {
            "non-finite or non-positive price / negative volume", "invalid numeric candle value",
            "timestamps do not align to the candle grid"}):
            raise
    stamps = cast(list[int], payload["t"])
    unique = sorted(set(stamps))
    spacings = Counter(right - left for left, right in zip(unique, unique[1:]))
    observed = min(spacings) if spacings else None
    interval = requested_seconds
    accepted: dict[datetime, Bar] = {}
    rejected: list[dict[str, Any]] = []
    for index, stamp in enumerate(stamps):
        row: dict[str, Any] = {"s": "ok", "t": [stamp], **{
            column: [payload[column][index]] for column in ("o", "h", "l", "c", "v")}}
        try:
            audit = audit_payload(row, interval, now=now)
        except (ValueError, WallexPayloadError) as exc:
            rejected.append({"index": index, "t": stamp, "reason": str(exc), "values": {
                column: str(payload[column][index]) for column in ("o", "h", "l", "c", "v")}})
            continue
        for bar in audit.bars:
            if bar.ts_open in accepted and accepted[bar.ts_open] != bar:
                raise WallexPayloadError("duplicate candle timestamps with different values")
            accepted[bar.ts_open] = bar
    duplicates = len(stamps) - len(unique)
    inversions = sum(right < left for left, right in zip(stamps, stamps[1:]))
    incomplete = sum(stamp + interval > now.timestamp() for stamp in unique)
    flags = {"INVALID_SOURCE_BAR_QUARANTINED"}
    if observed is None:
        flags.add("INTERVAL_UNVERIFIED")
    elif observed != requested_seconds:
        flags.add("RESOLUTION_MISMATCH")
    if any(spacing > interval for spacing in spacings):
        flags.add("GAP")
    if duplicates:
        flags.add("RAW_DUPLICATES")
    if inversions:
        flags.add("RAW_OUT_OF_ORDER")
    if incomplete:
        flags.add("INCOMPLETE_FINAL_BAR_DROPPED")
    return PayloadAudit([accepted[stamp] for stamp in sorted(accepted)], {
        "raw_rows": len(stamps), "duplicate_rows": duplicates, "out_of_order_pairs": inversions,
        "incomplete_rows_dropped": incomplete, "actual_interval_seconds": observed,
        "observed_spacing_counts_seconds": {str(key): value for key, value in sorted(spacings.items())},
        "flags": sorted(flags), "invalid_source_rows": len(rejected), "invalid_rows": rejected,
    })


def _utc(value: datetime) -> None:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timezone-aware UTC required")


def _validate_bars(bars: Sequence[Bar], interval_s: int) -> None:
    if interval_s <= 0:
        raise ValueError("positive resolution required")
    previous: datetime | None = None
    for bar in bars:
        _utc(bar.ts_open)
        if (bar.ts_open.microsecond or int(bar.ts_open.timestamp()) % interval_s
                or (previous is not None and bar.ts_open <= previous)):
            raise ValueError("bars must be unique, chronological and on the UTC grid")
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if (not all(value.is_finite() for value in values) or min(values[:4]) <= 0 or bar.volume < 0
                or bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close, bar.low)):
            raise ValueError("invalid OHLCV bar")
        previous = bar.ts_open


def profile_bars(bars: Sequence[Bar], interval_s: int, *, start: datetime, end: datetime) -> dict[str, Any]:
    _utc(start)
    _utc(end)
    _validate_bars(bars, interval_s)
    if start >= end or start.timestamp() % interval_s or end.timestamp() % interval_s:
        raise ValueError("coverage boundaries must increase and align to the interval")
    if any(bar.ts_open < start or bar.ts_open + timedelta(seconds=interval_s) > end for bar in bars):
        raise ValueError("bar outside closed coverage range")
    gaps: list[dict[str, Any]] = []
    cursor = start
    for stamp in [*(bar.ts_open for bar in bars), end]:
        if stamp > cursor:
            gaps.append({"start_utc": cursor.isoformat(), "end_exclusive_utc": stamp.isoformat(),
                         "missing_bars": int((stamp - cursor).total_seconds()) // interval_s})
        cursor = stamp + timedelta(seconds=interval_s)
    spacings = Counter(int((right.ts_open - left.ts_open).total_seconds()) for left, right in zip(bars, bars[1:]))
    observed = min(spacings) if spacings else None
    jumps = sorted(({
        "from_utc": left.ts_open.isoformat(), "to_utc": right.ts_open.isoformat(),
        "close_change_pct": str((right.close / left.close - 1) * 100),
        "spacing_seconds": int((right.ts_open - left.ts_open).total_seconds()),
    } for left, right in zip(bars, bars[1:])), key=lambda item: abs(Decimal(item["close_change_pct"])), reverse=True)
    flags: set[str] = set()
    if gaps:
        flags.add("GAP")
    if observed is None:
        flags.add("INTERVAL_UNVERIFIED")
    elif observed != interval_s:
        flags.add("RESOLUTION_MISMATCH")
    jump_count = sum(abs(Decimal(jump["close_change_pct"])) >= 20 for jump in jumps)
    if jump_count:
        flags.add("LARGE_DISCONTINUITY")
    expected = int((end - start).total_seconds()) // interval_s
    return {
        "quality_status": "PARTIAL" if flags else "PASS", "flags": sorted(flags),
        "row_count": len(bars), "expected_bars": expected, "missing_bars": expected - len(bars),
        "coverage_fraction": str(Decimal(len(bars)) / expected), "gap_count": len(gaps), "gaps": gaps,
        "first_open_utc": bars[0].ts_open.isoformat() if bars else None,
        "last_open_utc": bars[-1].ts_open.isoformat() if bars else None,
        "last_close_utc": (bars[-1].ts_open + timedelta(seconds=interval_s)).isoformat() if bars else None,
        "actual_interval_seconds": observed, "observed_spacing_counts_seconds": {
            str(key): value for key, value in sorted(spacings.items())},
        "zero_volume_bars": sum(bar.volume == 0 for bar in bars),
        "large_discontinuities": jump_count, "diagnostic_jump_warning_pct": "20",
        "largest_close_changes": jumps[:10], "duplicate_bars": 0, "invalid_ohlcv_bars": 0,
    }


def split_bars(bars: Sequence[Bar], interval_s: int, boundaries: Sequence[datetime]) -> list[list[Bar]]:
    _validate_bars(bars, interval_s)
    for boundary in boundaries:
        _utc(boundary)
    if len(boundaries) < 2 or any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        raise ValueError("split boundaries must be strictly increasing")
    return [[bar for bar in bars if start <= bar.ts_open and bar.ts_open + timedelta(seconds=interval_s) <= end]
            for start, end in zip(boundaries, boundaries[1:])]


class PublicArchive:
    receipts: list[dict[str, Any]]
    accessed_receipts: list[dict[str, Any]]

    def __init__(self, directory: Path, *, get: Getter = urllib_get, sleep: Callable[[float], None] | None = None,
                 min_interval_s: float = 1, offline: bool = False) -> None:
        if min_interval_s < 0:
            raise ValueError("negative rate limit")
        self.directory = directory
        self.get = get
        self.sleep = sleep or time.sleep
        self.min_interval_s = min_interval_s
        self.offline = offline
        self.receipts = []
        self.accessed_receipts = []
        self.http_attempts = 0

    @staticmethod
    def _allowed(url: str) -> None:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "api.wallex.ir" or parsed.fragment:
            raise ValueError("only public Wallex HTTPS endpoints allowed")
        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if parsed.path == "/v1/udf/history":
            if (set(params) != {"symbol", "resolution", "from", "to"} or any(len(value) != 1 for value in params.values())
                    or not re.fullmatch(r"[A-Z0-9]{3,24}", params["symbol"][0])
                    or params["resolution"][0] not in {"1", "15", "60", "240", "480", "720", "D", "1D", "2D", "3D"}
                    or not params["from"][0].isdigit() or not params["to"][0].isdigit()
                    or int(params["from"][0]) > int(params["to"][0])):
                raise ValueError("invalid public candle parameters")
        elif parsed.path not in {"/v1/markets", "/hector/web/v1/markets", "/v1/udf/config", "/v1/prop/packages/183"} or params:
            raise ValueError("unknown or private endpoint denied")

    @staticmethod
    def _check_payload(raw: bytes, url: str) -> None:
        value: Any = json.loads(raw, parse_float=Decimal)
        if not isinstance(value, dict):
            raise WallexPayloadError("public response must be an object")
        payload = cast(dict[str, Any], value)
        if payload.get("success") is False:
            raise WallexPayloadError("public response success=false")
        if "/udf/history?" in url and payload.get("s") not in {"ok", "no_data"}:
            raise WallexPayloadError("public candle response status is not ok/no_data")

    def _remember(self, receipt: dict[str, Any]) -> None:
        for existing in self.receipts:
            if existing["file"] == receipt["file"]:
                if existing != receipt:
                    raise DatasetIntegrityError("conflicting cached receipt within the current archive session")
                self.accessed_receipts.append(existing)
                return
        self.receipts.append(receipt)
        self.accessed_receipts.append(receipt)

    def __call__(self, url: str, timeout: float) -> bytes:
        self._allowed(url)
        key = hashlib.sha256(url.encode()).hexdigest()
        raw_path = self.directory / f"{key}.json"
        receipt_path = self.directory / f"{key}.receipt.json"
        if raw_path.exists() != receipt_path.exists():
            raise DatasetIntegrityError("partial public cache: preserve the surviving response/receipt and investigate")
        if raw_path.exists() and receipt_path.exists():
            receipt: dict[str, Any] = json.loads(receipt_path.read_text())
            raw = raw_path.read_bytes()
            if (receipt.get("url") != url or receipt.get("file") != raw_path.name
                    or hashlib.sha256(raw).hexdigest() != receipt.get("sha256")):
                raise DatasetIntegrityError("cached public response hash or request mismatch")
            self._check_payload(raw, url)
            self._remember(receipt)
            return raw
        if self.offline:
            raise DatasetIntegrityError("offline cache missing public response or receipt")
        for attempt in range(3):
            self.sleep(self.min_interval_s)
            self.http_attempts += 1
            try:
                raw = self.get(url, timeout)
                self._check_payload(raw, url)
            except (OSError, urllib.error.URLError, json.JSONDecodeError, WallexPayloadError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code != 429 and exc.code < 500:
                    raise
                if attempt == 2:
                    raise
                self.sleep(2 ** attempt)
                continue
            receipt = {"url": url, "file": raw_path.name, "sha256": hashlib.sha256(raw).hexdigest(),
                       "bytes": len(raw), "collected_at_utc": datetime.now(UTC).isoformat()}
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary = raw_path.with_suffix(".tmp")
            temporary.write_bytes(raw)
            temporary.replace(raw_path)
            temporary = receipt_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(receipt, sort_keys=True, indent=1))
            temporary.replace(receipt_path)
            self._remember(receipt)
            return raw
        raise AssertionError("bounded read retry exhausted")


def collect_series(client: WallexPublicClient, symbol: str, resolution: str, interval_s: int, *,
                   start: datetime, end: datetime, chunk_seconds: int,
                   progress: Callable[[str], None] | None = None) -> tuple[list[Bar], dict[str, Any]]:
    _utc(start)
    _utc(end)
    if start >= end or chunk_seconds <= 0 or chunk_seconds % interval_s:
        raise ValueError("invalid collection range or chunk size")
    seen: dict[datetime, Bar] = {}
    chunks: list[dict[str, Any]] = []
    duplicates = 0
    outside = 0
    cursor = start
    while cursor < end:
        stop = min(cursor + timedelta(seconds=chunk_seconds), end)
        payload = client.candles(symbol, resolution, int(cursor.timestamp()), int(stop.timestamp()) - 1)
        audit = audit_payload(payload, interval_s, now=end, quarantine_invalid_rows=True)
        shifted_daily = resolution == "1D" and any(
            row["reason"] == "timestamps do not align to the candle grid" for row in audit.evidence["invalid_rows"])
        if audit.evidence["raw_rows"] and audit.evidence["actual_interval_seconds"] != interval_s and not shifted_daily:
            raise WallexPayloadError("source interval is unverified or mismatched; refusing to relabel bars")
        for bar in audit.bars:
            if bar.ts_open < cursor or bar.ts_open >= stop:
                outside += 1
            if not (start <= bar.ts_open and bar.ts_open + timedelta(seconds=interval_s) <= end):
                continue
            if bar.ts_open in seen:
                if seen[bar.ts_open] != bar:
                    raise WallexPayloadError(f"conflicting cross-chunk candle: {symbol} {bar.ts_open.isoformat()}")
                duplicates += 1
            seen[bar.ts_open] = bar
        chunks.append({"start_utc": cursor.isoformat(), "end_exclusive_utc": stop.isoformat(),
                       "status": payload["s"], **audit.evidence})
        cursor = stop
        if progress is not None and (len(chunks) % 5 == 0 or cursor == end):
            progress(f"{symbol} resolution={resolution} through={cursor.date()} chunks={len(chunks)} bars={len(seen)}")
    ordered = [seen[stamp] for stamp in sorted(seen)]
    quality = profile_bars(ordered, interval_s, start=start, end=end)
    raw_flags = {flag for chunk in chunks for flag in chunk["flags"] if flag != "INTERVAL_UNVERIFIED"}
    quality["flags"] = sorted(set(quality["flags"]) | raw_flags | ({"OUT_OF_REQUEST_RANGE"} if outside else set()))
    quality["quality_status"] = "PARTIAL" if quality["flags"] else "PASS"
    quality["invalid_source_rows"] = sum(chunk["invalid_source_rows"] for chunk in chunks)
    quality["grid_mismatch_rows"] = sum(row["reason"] == "timestamps do not align to the candle grid"
                                        for chunk in chunks for row in chunk["invalid_rows"])
    quality["invalid_ohlcv_rows"] = quality["invalid_source_rows"] - quality["grid_mismatch_rows"]
    if quality["grid_mismatch_rows"]:
        quality["flags"] = sorted(set(quality["flags"]) | {"TIMESTAMP_GRID_MISMATCH"})
        quality["quality_status"] = "BLOCKED"
    return ordered, {"quality": quality, "chunks": chunks, "cross_chunk_duplicates": duplicates,
                     "out_of_request_rows": outside, "raw_rows": sum(chunk["raw_rows"] for chunk in chunks),
                     "raw_duplicate_rows": sum(chunk["duplicate_rows"] for chunk in chunks),
                     "raw_out_of_order_pairs": sum(chunk["out_of_order_pairs"] for chunk in chunks),
                     "incomplete_rows_dropped": sum(chunk["incomplete_rows_dropped"] for chunk in chunks),
                     "invalid_source_rows": quality["invalid_source_rows"],
                     "grid_mismatch_rows": quality["grid_mismatch_rows"], "invalid_ohlcv_rows": quality["invalid_ohlcv_rows"]}


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, default=str, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False) + "\n").encode()
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)
    return hashlib.sha256(raw).hexdigest()


def _validate_plan(plan: dict[str, Any]) -> None:
    allowed = {"schema_version", "name", "symbols", "end_utc", "datasets", "views", "candidate_source", "fee", "eligibility"}
    if set(plan) - allowed or plan.get("schema_version") != "1" or plan.get("fee") is not None:
        raise ValueError("unknown plan key, schema or configured fee")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(plan.get("name", ""))):
        raise ValueError("invalid pack name")
    symbols = cast(list[str], plan["symbols"])
    if not symbols or len(set(symbols)) != len(symbols) or any(not re.fullmatch(r"[A-Z0-9]{3,24}", symbol) for symbol in symbols):
        raise ValueError("invalid collection symbols")
    end = datetime.fromisoformat(plan["end_utc"])
    _utc(end)
    if end > datetime.now(UTC):
        raise ValueError("collection end must already be closed")
    intervals: dict[str, int] = {}
    definitions = cast(list[dict[str, Any]], plan["datasets"])
    if not definitions:
        raise ValueError("at least one source dataset required")
    for definition in definitions:
        if set(definition) != {"id", "resolution", "seconds", "start_utc", "chunk_seconds"}:
            raise ValueError("unknown or missing dataset key")
        interval = definition["seconds"]
        if type(interval) is not int or {"1": 60, "60": 3600, "1D": 86400}.get(definition["resolution"]) != interval:
            raise ValueError("native resolution and interval must agree")
        start = datetime.fromisoformat(definition["start_utc"])
        _utc(start)
        chunk = definition["chunk_seconds"]
        if (start >= end or start.timestamp() % interval or end.timestamp() % interval
                or type(chunk) is not int or chunk <= 0 or chunk % interval):
            raise ValueError("invalid range or chunk grid")
        intervals[str(definition["id"])] = interval
    for view in cast(list[dict[str, Any]], plan.get("views", [])):
        if set(view) != {"id", "source", "seconds"} or view["source"] not in intervals:
            raise ValueError("invalid view definition")
        interval, base = view["seconds"], intervals[view["source"]]
        if type(interval) is not int or interval <= base or interval % base:
            raise ValueError("view must use a larger multiple of its source interval")
        intervals[str(view["id"])] = interval
    identities = [str(item["id"]) for item in [*definitions, *cast(list[dict[str, Any]], plan.get("views", []))]]
    if len(set(identities)) != len(identities) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", identity) for identity in identities):
        raise ValueError("dataset identifiers must be unique safe names")


def prefetch(plan: dict[str, Any], archive: PublicArchive, *, workers: int = 2,
             progress: Callable[[str], None] | None = None) -> int:
    """Warm the checked cache with at most two public reads in flight and one shared rate gate."""
    _validate_plan(plan)
    if workers not in (1, 2):
        raise ValueError("public prefetch permits one or two workers")
    urls = ["https://api.wallex.ir" + path for path in (
        "/v1/markets", "/hector/web/v1/markets", "/v1/prop/packages/183", "/v1/udf/config")]
    end = datetime.fromisoformat(plan["end_utc"])
    for definition in cast(list[dict[str, Any]], plan["datasets"]):
        for symbol in cast(list[str], plan["symbols"]):
            cursor = datetime.fromisoformat(definition["start_utc"])
            while cursor < end:
                stop = min(cursor + timedelta(seconds=int(definition["chunk_seconds"])), end)
                params = {"symbol": symbol, "resolution": str(definition["resolution"]),
                          "from": int(cursor.timestamp()), "to": int(stop.timestamp()) - 1}
                urls.append("https://api.wallex.ir/v1/udf/history?" + urllib.parse.urlencode(params))
                cursor = stop
    lock = threading.Lock()
    next_read_at = 0.0

    def get(url: str, timeout: float) -> bytes:
        nonlocal next_read_at
        with lock:
            delay = max(0, next_read_at - time.monotonic())
            if delay:
                archive.sleep(delay)
            next_read_at = time.monotonic() + archive.min_interval_s
        return archive.get(url, timeout)

    def fetch(url: str) -> int:
        worker = PublicArchive(archive.directory, get=get, sleep=archive.sleep, min_interval_s=0, offline=archive.offline)
        worker(url, 20)
        return worker.http_attempts

    attempts = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, count in enumerate(executor.map(fetch, urls), 1):
            attempts += count
            if progress is not None and (index % 25 == 0 or index == len(urls)):
                progress(f"public cache checked={index}/{len(urls)} new_http_attempts={attempts}")
    return attempts


def build_pack(plan: dict[str, Any], archive: PublicArchive, out: Path, *,
               progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    _validate_plan(plan)
    symbols = cast(list[str], plan["symbols"])
    if not symbols or len(set(symbols)) != len(symbols) or any(not re.fullmatch(r"[A-Z0-9]{3,24}", symbol) for symbol in symbols):
        raise ValueError("invalid collection symbols")
    end = datetime.fromisoformat(plan["end_utc"])
    _utc(end)
    if end > datetime.now(UTC):
        raise ValueError("collection end must already be closed")
    build_start = len(archive.accessed_receipts)
    client = WallexPublicClient(get=archive, min_interval_s=0)
    metadata_payloads: dict[str, dict[str, Any]] = {
        "v1_markets.json": client.markets_v1(), "hector_markets.json": client.markets_hector(),
        "package_183.json": client.package(183), "udf_config.json": json.loads(
            archive("https://api.wallex.ir/v1/udf/config", 20), parse_float=Decimal),
    }
    metadata_hashes = {filename: _write_json(out / "metadata" / filename, value)
                       for filename, value in metadata_payloads.items()}
    metadata_version = hashlib.sha256(json.dumps(metadata_hashes, sort_keys=True).encode()).hexdigest()
    parsed = parse_market_meta(metadata_payloads["v1_markets.json"], metadata_payloads["hector_markets.json"],
                               metadata_version=metadata_version)
    metadata = {"sha256": metadata_hashes, "version": metadata_version, "account_fee": None,
                "markets": {symbol: parsed[symbol].model_dump(mode="json") if symbol in parsed else None for symbol in symbols},
                "interpretation": "Current public metadata only; zero minima do not establish executable minimums. "
                                  "stepSize/tickSize are not interpreted as increments. Account fee and eligibility unresolved."}
    all_bars: dict[str, dict[str, list[Bar]]] = {}
    ranges: dict[str, tuple[datetime, datetime]] = {}
    source_receipts: dict[str, list[dict[str, Any]]] = {}
    evidence_by: dict[str, dict[str, dict[str, Any]]] = {}
    definitions = cast(list[dict[str, Any]], plan["datasets"])
    views = cast(list[dict[str, Any]], plan.get("views", []))
    ids = [str(definition["id"]) for definition in [*definitions, *views]]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", identity) for identity in ids):
        raise ValueError("dataset identifiers must be unique safe names")
    for definition in definitions:
        identity = str(definition["id"])
        start = datetime.fromisoformat(definition["start_utc"])
        interval = int(definition["seconds"])
        all_bars[identity] = {}
        evidence_by[identity] = {}
        ranges[identity] = (start, end)
        first_receipt = len(archive.accessed_receipts)
        for symbol in symbols:
            bars, evidence = collect_series(client, symbol, str(definition["resolution"]), interval, start=start, end=end,
                                            chunk_seconds=int(definition["chunk_seconds"]), progress=progress)
            all_bars[identity][symbol] = bars
            evidence_by[identity][symbol] = evidence
        source_receipts[identity] = archive.accessed_receipts[first_receipt:]
    for view in views:
        identity, source = str(view["id"]), str(view["source"])
        if source not in all_bars:
            raise ValueError("view must reference an earlier dataset")
        base_definition = next(item for item in [*definitions, *views] if item["id"] == source)
        base_interval, interval = int(base_definition["seconds"]), int(view["seconds"])
        if interval <= base_interval or interval % base_interval:
            raise ValueError("view must use a larger multiple of its source interval")
        start, stop = ranges[source]
        start = datetime.fromtimestamp(-(-int(start.timestamp()) // interval) * interval, UTC)
        stop = datetime.fromtimestamp(int(stop.timestamp()) // interval * interval, UTC)
        ranges[identity] = (start, stop)
        source_receipts[identity] = source_receipts[source]
        all_bars[identity] = {}
        evidence_by[identity] = {}
        for symbol in symbols:
            bars = [bar for bar in aggregate(all_bars[source][symbol], interval, base_interval)
                    if start <= bar.ts_open and bar.ts_open + timedelta(seconds=interval) <= stop]
            all_bars[identity][symbol] = bars
            evidence_by[identity][symbol] = {"derived_from": source, "quality": profile_bars(
                bars, interval, start=start, end=stop), "aggregation": "existing aggregate(); complete UTC buckets only"}
    series: list[dict[str, Any]] = []
    datasets: list[dict[str, Any]] = []
    for definition in [*definitions, *views]:
        identity, interval = str(definition["id"]), int(definition["seconds"])
        start, stop = ranges[identity]
        receipts = source_receipts[identity]
        collected = [str(receipt["collected_at_utc"]) for receipt in receipts]
        provenance = {"source": "WALLEX_PUBLIC_REST", "endpoint": "https://api.wallex.ir/v1/udf/history",
                      "tool": "WallexPublicClient.candles + PublicArchive (public GET, no credentials)",
                      "requested_interval": definition.get("resolution"), "derived_from": definition.get("source"),
                      "requested_start_utc": start.isoformat(), "requested_end_exclusive_utc": stop.isoformat(),
                      "collection_start_utc": min(collected), "collection_end_utc": max(collected)}
        directory = out / "datasets" / identity
        for filename, value in metadata_payloads.items():
            _write_json(directory / "metadata" / filename, value)
        dataset_manifest = write_dataset(directory, f"{plan['name']}-{identity}", all_bars[identity],
                                         extra={**provenance, "metadata_version": metadata_version}, resolution_seconds=interval)
        datasets.append({"id": identity, "directory": f"datasets/{identity}", "resolution_seconds": interval,
                         "manifest_sha256": hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()})
        for symbol in symbols:
            evidence = evidence_by[identity][symbol]
            quality = cast(dict[str, Any], evidence["quality"])
            quality_file = f"quality/{identity}/{symbol}.json"
            quality_hash = _write_json(out / quality_file, evidence)
            info = dataset_manifest["symbols"][symbol]
            series.append({**provenance, "dataset": identity, "symbol": symbol, "interval_seconds": interval,
                           "actual_interval_seconds": quality["actual_interval_seconds"],
                           "file": f"datasets/{identity}/{info['file']}", "sha256": info["sha256"],
                           "bars_sha256": info["bars_sha256"], "row_count": info["bars"],
                           "start_utc": quality["first_open_utc"], "end_exclusive_utc": quality["last_close_utc"],
                           "expected_bars": quality["expected_bars"], "missing_bars": quality["missing_bars"],
                           "gap_count": quality["gap_count"], "flags": quality["flags"],
                           "invalid_source_rows": evidence.get("invalid_source_rows", 0),
                           "grid_mismatch_rows": evidence.get("grid_mismatch_rows", 0),
                           "invalid_ohlcv_rows": evidence.get("invalid_ohlcv_rows", 0),
                           "quality_status": quality["quality_status"], "quality_file": quality_file,
                           "quality_sha256": quality_hash})
    build_receipts = list({receipt["file"]: receipt for receipt in archive.accessed_receipts[build_start:]}.values())
    manifest = {"schema_version": "1", "name": plan["name"], "plan_sha256": hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "collection_start_utc": min(str(receipt["collected_at_utc"]) for receipt in build_receipts),
        "collection_end_utc": max(str(receipt["collected_at_utc"]) for receipt in build_receipts),
        "archive_directory": "data/raw/historical-pack/http", "output_directory": "data/raw/historical-pack/export",
        "eligibility": "UNRESOLVED: collection candidates only; public package metadata is not challenge authorization",
        "metadata": metadata, "datasets": datasets, "series": series,
        "raw_responses": build_receipts, "limitations": [
            "No historical trades or order books collected; candles do not establish fills, spread or intrabar ordering.",
            "Missing bars are not filled; zero volume and discontinuities are retained and reported.",
            "Volume is the source v field; its economic unit is not independently verified.",
            "Current metadata is not point-in-time historical metadata; fees remain experiment configuration.",
            "Resolution inferred from timestamp spacing can be ambiguous in sparse chunks; inspect flags and histograms.",
            "No strategy-specific split ratios; split_bars enforces close-time containment, but feature/position leakage is the harness's responsibility.",
        ]}
    _write_json(out / "MANIFEST.json", manifest)
    return manifest
