"""F2 startup warm-up `warmup-1`: validated cache first, public hourly reads only for the missing tail, closed UTC
candles, dedupe, exact resolution, no future bars, recorded coverage/hash/source, fail closed when insufficient."""

import gzip
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from wallex_agent.market.bars import Bar
from wallex_agent.market.warmup import HourlyCache, bootstrap_hourly, combine_hours

H = 3600
NOW = datetime(2026, 10, 23, 10, 30, tzinfo=UTC)  # mid-hour: the 10:00 candle is still forming
BASE = datetime(2026, 1, 1, tzinfo=UTC)
H0 = NOW.replace(minute=0)  # hour-aligned anchor for cached candles


def px(t: int) -> int:
    return 100_000_000 + (t // H) % 97 * 1000


@dataclass
class FakeClient:
    available_from: datetime = BASE
    calls: list[tuple[str, str, int, int]] = field(default_factory=lambda: [])
    resolution_s: int = H
    override: dict[int, int] = field(default_factory=lambda: {})

    def candles(self, symbol: str, resolution: str, frm: int, to: int) -> dict[str, Any]:
        self.calls.append((symbol, resolution, frm, to))
        start = max(frm - frm % self.resolution_s, int(self.available_from.timestamp()))
        ts = [t for t in range(start, to + 1, self.resolution_s) if t >= frm]
        if not ts:
            return {"s": "no_data"}
        c = [self.override.get(t, px(t)) for t in ts]
        return {"s": "ok", "t": ts, "o": c, "h": [x + 500 for x in c], "l": [x - 500 for x in c], "c": c,
                "v": ["1"] * len(ts)}


def hour_bar(t: datetime, close: int | None = None) -> Bar:
    c = Decimal(close if close is not None else px(int(t.timestamp())))
    return Bar(t, c, c + 500, c - 500, c, Decimal(1))


def hours(start: datetime, end: datetime) -> list[Bar]:
    out: list[Bar] = []
    t = start
    while t < end:
        out.append(hour_bar(t))
        t += timedelta(hours=1)
    return out


def last_closed() -> datetime:
    return NOW.replace(minute=0) - timedelta(hours=1)


def test_cold_start_fetches_required_history_in_bounded_chunks_and_caches(tmp_path: Path) -> None:
    client = FakeClient()
    bars, rep = bootstrap_hourly("BTCTMN", client=client, cache=HourlyCache(tmp_path), seed=None, now=NOW, required_hours=24 * 30)
    assert rep.ok and rep.sources["public"] == len(bars) and rep.sources["cache"] == 0
    assert bars[-1].ts_open == last_closed()  # forming 10:00 candle excluded
    assert all(int(b.ts_open.timestamp()) % H == 0 for b in bars) and len({b.ts_open for b in bars}) == len(bars)
    assert all(to - frm <= 7 * 86400 for _, _, frm, to in client.calls) and {r for _, r, _, _ in client.calls} == {"60"}
    assert rep.first_open is not None and rep.first_open <= NOW - timedelta(hours=24 * 30)
    assert rep.sha256 and (tmp_path / "BTCTMN" / "manifest.json").exists()


def test_validated_cache_means_only_the_missing_tail_is_fetched(tmp_path: Path) -> None:
    cache = HourlyCache(tmp_path)
    cache.save("BTCTMN", hours(H0 - timedelta(days=40), H0 - timedelta(hours=12)))
    client = FakeClient()
    bars, rep = bootstrap_hourly("BTCTMN", client=client, cache=cache, seed=None, now=NOW, required_hours=24 * 30)
    assert rep.ok and rep.sources["cache"] > 0 and rep.sources["public"] <= 13  # 12 missing + 1 revision overlap
    assert min(frm for _, _, frm, _ in client.calls) >= int((H0 - timedelta(hours=14)).timestamp())  # tail + 1h overlap
    assert bars[-1].ts_open == last_closed()


def test_corrupt_cache_is_ignored_and_refetched(tmp_path: Path) -> None:
    cache = HourlyCache(tmp_path)
    cache.save("BTCTMN", hours(H0 - timedelta(days=40), H0 - timedelta(hours=12)))
    f = next((tmp_path / "BTCTMN").glob("*.csv.gz"))
    f.write_bytes(gzip.compress(b"t,o,h,l,c,v\n"))  # hash no longer matches its manifest
    bars, rep = bootstrap_hourly("BTCTMN", client=FakeClient(), cache=cache, seed=None, now=NOW, required_hours=24 * 30)
    assert rep.ok and rep.sources["cache"] == 0 and "CACHE_INVALID" in rep.notes and len(bars) >= 24 * 30


def test_insufficient_public_history_fails_closed(tmp_path: Path) -> None:
    client = FakeClient(available_from=NOW - timedelta(days=10))
    _, rep = bootstrap_hourly("BTCTMN", client=client, cache=HourlyCache(tmp_path), seed=None, now=NOW, required_hours=24 * 30)
    assert not rep.ok and rep.reason == "INSUFFICIENT_HISTORY"


def test_wrong_public_resolution_is_rejected(tmp_path: Path) -> None:
    client = FakeClient(resolution_s=60)  # asked for 1h, served minutes
    _, rep = bootstrap_hourly("BTCTMN", client=client, cache=HourlyCache(tmp_path), seed=None, now=NOW, required_hours=48)
    assert not rep.ok and rep.reason == "RESOLUTION_MISMATCH"


def test_conflicting_duplicate_prefers_the_fresh_public_candle_and_is_counted(tmp_path: Path) -> None:
    cache = HourlyCache(tmp_path)
    old = hours(H0 - timedelta(days=40), H0 - timedelta(hours=2))
    t_conf = old[-1].ts_open
    old[-1] = hour_bar(t_conf, close=1)  # a revised candle: the exchange's current value wins
    cache.save("BTCTMN", old)
    client = FakeClient()
    bars, rep = bootstrap_hourly("BTCTMN", client=client, cache=cache, seed=None, now=NOW, required_hours=24 * 30,
                                 overlap_hours=3)
    assert rep.conflicts == 1 and next(b for b in bars if b.ts_open == t_conf).close == px(int(t_conf.timestamp()))


def test_combine_prefers_complete_minute_hours_and_never_invents_a_missing_hour() -> None:
    t0 = datetime(2026, 10, 1, tzinfo=UTC)
    minutes = [Bar(t0 + timedelta(minutes=m), Decimal(200), Decimal(201), Decimal(199), Decimal(200), Decimal(1))
               for m in range(180) if m != 70]  # hour 1 incomplete in the minute feed
    native = [hour_bar(t0), hour_bar(t0 + timedelta(hours=1))]  # hour 2 missing in the native feed
    out = {b.ts_open: b for b in combine_hours(minutes, native)}
    assert out[t0].close == 200  # complete minute hour wins
    assert out[t0 + timedelta(hours=1)].close == px(int((t0 + timedelta(hours=1)).timestamp()))  # native fills the gap
    assert out[t0 + timedelta(hours=2)].close == 200 and len(out) == 3  # only real sources; no fabricated hour
    assert combine_hours(minutes[:59], []) == []  # an incomplete hour with no native candle stays missing
