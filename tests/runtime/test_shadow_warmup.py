"""F2 SHADOW warm-up: the runtime refuses to start without the minimum native-1h history, reaches the strategy's
regime features when warmed up, journals the warm-up report, and reuses its validated cache on restart."""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from support.synthetic import EV1

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.contracts import Environment, QualityFlag
from wallex_agent.journal.store import JournalStore
from wallex_agent.market.warmup import WarmupUnavailable
from wallex_agent.runtime.config import RunConfig, ShadowSettings
from wallex_agent.runtime.shadow import run_shadow

T0 = datetime(2026, 10, 3, 3, 57, tzinfo=UTC)
F2 = {"version": "f2-test", "entry_band_atr": "0.5", "stop_atr": "5", "trail_atr": "5", "max_holding_s": 604800,
      "candidate_ttl_s": 900, "regime_filter": True}


def price(t: int) -> Decimal:
    return Decimal(round(1e8 * math.exp(2e-8 * (t - 1.7e9)) * (1 + 0.01 * math.sin(t / 86400))))


@dataclass
class HourlyAwareClient:
    """Public-only fake: 1m candles for the last 3 days, native 1h candles from `hourly_from`. No write methods."""
    now: Callable[[], datetime]
    hourly_from: datetime
    calls: list[str] = field(default_factory=lambda: [])

    def _body(self, name: str) -> Any:
        raw = json.loads((EV1 / name).read_text())["body"]
        return json.loads(raw, parse_float=Decimal) if isinstance(raw, str) else raw

    def candles(self, symbol: str, resolution: str, frm: int, to: int) -> dict[str, Any]:
        self.calls.append(f"GET candles {symbol} {resolution}")
        step = 60 if resolution == "1" else 3600
        lo = int((self.now() - timedelta(days=3)).timestamp()) if step == 60 else int(self.hourly_from.timestamp())
        now = int(self.now().timestamp())
        ts = [t for t in range(max(frm, lo) - max(frm, lo) % step, to + 1, step) if t >= max(frm, lo) and t + step <= now]
        if not ts:
            return {"s": "no_data"}
        o = [price(t) for t in ts]
        c = [price(t + step) for t in ts]
        return {"s": "ok", "t": ts, "o": [str(x) for x in o], "c": [str(x) for x in c],
                "h": [str(max(a, b) * Decimal("1.001")) for a, b in zip(o, c)],
                "l": [str(min(a, b) * Decimal("0.999")) for a, b in zip(o, c)], "v": ["1"] * len(ts)}

    def depth(self, symbol: str) -> dict[str, Any]:
        self.calls.append(f"GET depth {symbol}")
        p = price(int(self.now().timestamp()))
        return {"result": {"ask": [{"price": p * Decimal("1.0003"), "quantity": Decimal(5)}],
                           "bid": [{"price": p * Decimal("0.9997"), "quantity": Decimal(5)}]}}

    def markets_v1(self) -> Any:
        return self._body("_v1_markets.json")

    def markets_hector(self) -> Any:
        return self._body("_hector_web_v1_markets.json")

    def package(self, package_id: int) -> Any:
        return self._body("_v1_prop_packages_183.json")


def _cfg(make_cfg: Callable[..., RunConfig], cache: Path) -> RunConfig:
    return make_cfg(**{"run.environment": Environment.SHADOW, "run.symbols": ["BTCTMN"], "run.bar_interval_seconds": 14400,
                       "features.ema_bars": 48, "features.regime_er_bars": 42, "features.regime_rank_bars": 1080,
                       "strategy.f2": F2, "strategy.active": "F2_tsm",
                       "limits.cutoff": {"no_new_entry_before_end_s": 14400, "liquidation_start_before_end_s": 7200},
                       "shadow": ShadowSettings(poll_interval_s=60, history_bars=200, warmup_cache_dir=str(cache)).model_dump()})


def _run(cfg: RunConfig, store: JournalStore, client: HourlyAwareClient, clock: dict[str, datetime], run_id: str) -> None:
    def sleep(s: float) -> None:
        clock["t"] += timedelta(seconds=s)

    run_shadow(cfg, store, BaselineSelector(), run_id=run_id, duration_s=300, client=client,  # type: ignore[arg-type]
               clock=lambda: clock["t"], sleep=sleep)


def test_f2_shadow_refuses_to_start_without_minimum_warmup(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    clock = {"t": T0}
    client = HourlyAwareClient(lambda: clock["t"], hourly_from=T0 - timedelta(days=10))
    store = JournalStore(tmp_path / "s.sqlite")
    with pytest.raises(WarmupUnavailable, match="INSUFFICIENT_HISTORY"):
        _run(_cfg(make_cfg, tmp_path / "cache"), store, client, clock, "sh-f2")
    assert store.manifest("sh-f2") is None and store.unfinished_runs() == []  # no session was started
    assert all(not c.startswith(("POST", "DELETE", "PUT")) for c in client.calls)


def test_f2_shadow_warms_up_reaches_regime_features_and_reuses_cache(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    clock = {"t": T0}
    client = HourlyAwareClient(lambda: clock["t"], hourly_from=T0 - timedelta(days=200))
    store = JournalStore(tmp_path / "s.sqlite")
    cfg = _cfg(make_cfg, tmp_path / "cache")
    _run(cfg, store, client, clock, "sh-f2")
    reports: list[dict[str, Any]] | None = store.get_ref("sh-f2", "warmup")
    assert reports is not None and reports[0]["ok"] and reports[0]["sources"]["public"] > 1000 and reports[0]["sha256"]
    [rec] = [d for d in store.decisions("sh-f2") if d.recorded_at >= datetime(2026, 10, 3, 4, tzinfo=UTC)]
    assert rec.snapshot_id is not None and rec.features_ref is not None
    ctx: dict[str, Any] | None = store.get_ref("sh-f2", f"ctx:{rec.decision_id}")
    assert ctx is not None
    feats: dict[str, Any] = {f["feature_id"]: f["value"] for f in ctx["features"]["features"]}
    assert feats["BTCTMN.er_rank.42"] is not None and feats["BTCTMN.vol_rank.14"] is not None  # 180-day ranks reached
    assert not set(ctx["snapshot"]["quality_flags"]) & {QualityFlag.GAP.value, QualityFlag.STALE.value}
    assert all(not c.startswith(("POST", "DELETE", "PUT")) for c in client.calls)

    client.calls.clear()  # restart: the validated cache is used, only the tail is fetched
    _run(cfg, store, client, clock, "sh-f2-b")
    again: list[dict[str, Any]] | None = store.get_ref("sh-f2-b", "warmup")
    assert again is not None and again[0]["ok"] and again[0]["sources"]["cache"] > 1000 and again[0]["public_requests"] <= 2
