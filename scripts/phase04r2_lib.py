"""Phase 04 strategy round 2 — research library (RESEARCH ONLY: float statistics, no money, no orders).

Used by scripts/phase04r2_*.py to screen low-turnover families on the Codex historical pack before any candidate is
re-expressed in the Decimal kernel and confirmed through the Experiment Harness. Nothing here is imported by the
trading runtime. Pre-registration: reports/phase-04/round2/PREREGISTRATION_R2.md.

TEST PROTECTION: every loader cuts bars at DEV_END (2026-10-13 00:00 UTC, the protected final TEST start) and asserts
that no returned bar closes after it. `load_series(..., allow_test=True)` exists only for a frozen configuration that
passed every pre-registered development gate; it refuses to run unless `TEST_UNLOCK` was set by the caller.

Fill semantics mirror wallex_agent.execution.sim_backtest (conservative variant on hourly bars):
- decision on a CLOSED bar; entry LIMIT = close x (1 + entry cap); filled at the NEXT 1h open x (1 + slip) only if
  that is <= limit, otherwise the entry is skipped (the kernel would wait up to its 600 s TTL; we do not see minutes);
- protective/trailing stop: min(stop, open) x (1 - slip) on the hour bar whose low touches it (gap -> worse open);
- signal / time / end-of-slice exits: next 1h open x (1 - slip);
- fee = notional x fee rate on each side: net = exit (1 - fee) / (entry (1 + fee)) - 1.
Stops are evaluated on 1h bars (the kernel uses 1m bars): the trigger is identical, intrabar ordering is not modelled.
"""

import bisect
import csv
import gzip
import hashlib
import io
import json
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "data" / "raw" / "historical-pack" / "export"
COMMITTED_MANIFEST = REPO / "data" / "historical" / "MANIFEST.json"
DEV_END = datetime(2026, 10, 13, tzinfo=UTC)  # exclusive; protected TEST is [2026-10-13, 2026-10-22)
DEV_END_TS = int(DEV_END.timestamp())
TEST_UNLOCK = {"enabled": False}
HOUR = 3600
ENTRY_CAP_BPS = 20.0  # same cap as the Phase 04 kernel configs (entry LIMIT +20 bps)


def ts(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=UTC).timestamp())


def iso(t: int) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M")


# ------------------------------------------------------------------------------------------------------- data
@dataclass
class Series:
    symbol: str
    interval: int
    t: list[int]
    o: list[float]
    h: list[float]
    l: list[float]  # noqa: E741
    c: list[float]
    v: list[float]
    sha256: str = ""


def _manifest_series() -> dict[tuple[str, str], dict[str, Any]]:
    m: dict[str, Any] = json.loads(COMMITTED_MANIFEST.read_text())
    return {(str(s["dataset"]), str(s["symbol"])): s for s in m["series"]}


def load_series(dataset: str, symbol: str, interval: int, *, allow_test: bool = False) -> Series:
    """Load one pack series, verify its file hash against the COMMITTED manifest, cut at DEV_END unless unlocked."""
    if allow_test and not TEST_UNLOCK["enabled"]:
        raise RuntimeError("TEST window is locked: no configuration has passed the pre-registered development gates")
    info = _manifest_series()[(dataset, symbol)]
    if info.get("quality_status") == "BLOCKED":
        raise RuntimeError(f"{dataset}/{symbol} is BLOCKED in the data pack; not usable for time-based experiments")
    if int(info["actual_interval_seconds"]) != interval:
        raise RuntimeError(f"{dataset}/{symbol}: interval {info['actual_interval_seconds']} != expected {interval}")
    raw = (PACK / str(info["file"])).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != info["sha256"]:
        raise RuntimeError(f"{dataset}/{symbol}: file hash differs from committed MANIFEST")
    end = 10**12 if allow_test else DEV_END_TS
    s = Series(symbol, interval, [], [], [], [], [], [], digest)
    for r in list(csv.reader(io.StringIO(gzip.decompress(raw).decode())))[1:]:
        t = int(r[0])
        if t + interval > end:
            continue
        s.t.append(t)
        s.o.append(float(r[1]))
        s.h.append(float(r[2]))
        s.l.append(float(r[3]))
        s.c.append(float(r[4]))
        s.v.append(float(r[5]))
    if not allow_test:
        assert not s.t or s.t[-1] + interval <= DEV_END_TS, "TEST guard violated"
    return s


def aggregate_complete(s: Series, factor: int) -> tuple[Series, list[int]]:
    """Aggregate to `factor` x interval using only COMPLETE, aligned buckets. Returns the series and, per output bar,
    the index in `s` of its last component bar (the bar whose close is the decision time)."""
    width = s.interval * factor
    out = Series(s.symbol, width, [], [], [], [], [], [], s.sha256)
    last_idx: list[int] = []
    i, n = 0, len(s.t)
    while i < n:
        start = s.t[i] - s.t[i] % width
        j = i
        while j < n and s.t[j] < start + width:
            j += 1
        if j - i == factor and s.t[i] == start:
            out.t.append(start)
            out.o.append(s.o[i])
            out.h.append(max(s.h[i:j]))
            out.l.append(min(s.l[i:j]))
            out.c.append(s.c[j - 1])
            out.v.append(sum(s.v[i:j]))
            last_idx.append(j - 1)
        i = j
    return out, last_idx


# ------------------------------------------------------------------------------------------------- indicators
Arr = list[float | None]


def sma(x: Sequence[float], n: int) -> Arr:
    out: Arr = [None] * len(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc += v
        if i >= n:
            acc -= x[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out


def ema(x: Sequence[float], n: int) -> Arr:
    out: Arr = [None] * len(x)
    k, e = 2.0 / (n + 1), 0.0
    for i, v in enumerate(x):
        e = v if i == 0 else v * k + e * (1 - k)
        if i >= n - 1:
            out[i] = e
    return out


def atr(s: Series, n: int) -> Arr:
    """Simple mean of the last n true ranges (same definition as wallex_agent.features.engine._atr)."""
    tr = [0.0] + [max(s.h[i] - s.l[i], abs(s.h[i] - s.c[i - 1]), abs(s.l[i] - s.c[i - 1])) for i in range(1, len(s.c))]
    out: Arr = [None] * len(tr)
    acc = 0.0
    for i in range(1, len(tr)):
        acc += tr[i]
        if i > n:
            acc -= tr[i - n]
        if i >= n:
            out[i] = acc / n
    return out


def prior_high(h: Sequence[float], n: int) -> Arr:
    """Highest high of the n bars BEFORE i (current bar excluded)."""
    out: Arr = [None] * len(h)
    for i in range(n, len(h)):
        out[i] = max(h[i - n:i])
    return out


def rolling_std(x: Sequence[float], n: int) -> Arr:
    out: Arr = [None] * len(x)
    for i in range(n - 1, len(x)):
        w = x[i - n + 1:i + 1]
        m = sum(w) / n
        out[i] = math.sqrt(sum((v - m) ** 2 for v in w) / n)
    return out


def efficiency_ratio(c: Sequence[float], n: int) -> Arr:
    """Kaufman efficiency ratio |c_t - c_{t-n}| / sum |dc| over n bars (0 = noise, 1 = straight line)."""
    out: Arr = [None] * len(c)
    d = [0.0] + [abs(c[i] - c[i - 1]) for i in range(1, len(c))]
    acc = 0.0
    for i in range(1, len(c)):
        acc += d[i]
        if i > n:
            acc -= d[i - n]
        if i >= n and acc > 0:
            out[i] = abs(c[i] - c[i - n]) / acc
    return out


def pct_rank(x: Arr, window: int) -> Arr:
    """Causal percentile rank of x[i] among the previous `window` defined values (requires >= window/2 history)."""
    out: Arr = [None] * len(x)
    for i, v in enumerate(x):
        if v is None:
            continue
        w = [u for u in x[max(0, i - window):i] if u is not None]
        if len(w) >= window // 2:
            out[i] = sum(1 for u in w if u < v) / len(w)
    return out


# ------------------------------------------------------------------------------------------------------ regime
REGIME_VERSION = "regime-r2-0.1.0"
REG_ER_BARS, REG_RANK_WINDOW, REG_ATR_BARS = 42, 1080, 14  # 4h bars: 7 days, 180 days, 56 hours (pre-registered)
REG_SHOCK_RANK, REG_QUIET_RANK, REG_TREND_RANK = 0.95, 0.20, 0.50


def regime_labels(s4: Series) -> list[str]:
    """Deterministic, causal regime per CLOSED 4h bar: SHOCK > QUIET > TREND_UP / TREND_DOWN > RANGE; UNKNOWN warm-up.
    trend strength = Kaufman efficiency ratio over 7 days ranked vs its own trailing 180 days; volatility = ATR% rank."""
    er = efficiency_ratio(s4.c, REG_ER_BARS)
    er_r = pct_rank(er, REG_RANK_WINDOW)
    a = atr(s4, REG_ATR_BARS)
    vol: Arr = [ai / c if ai is not None and c > 0 else None for ai, c in zip(a, s4.c)]
    vol_r = pct_rank(vol, REG_RANK_WINDOW)
    out: list[str] = []
    for i in range(len(s4.c)):
        er_i, vr = er_r[i], vol_r[i]
        if er_i is None or vr is None or i < REG_ER_BARS:
            out.append("UNKNOWN")
        elif vr >= REG_SHOCK_RANK:
            out.append("SHOCK")
        elif vr <= REG_QUIET_RANK:
            out.append("QUIET")
        elif er_i >= REG_TREND_RANK:
            out.append("TREND_UP" if s4.c[i] > s4.c[i - REG_ER_BARS] else "TREND_DOWN")
        else:
            out.append("RANGE")
    return out


def activity_rank(s4: Series) -> Arr:
    """Diagnostic only (Wallex `v` unit unverified): rank of 24h volume vs trailing 180 days."""
    v24 = [sum(s4.v[max(0, i - 5):i + 1]) for i in range(len(s4.v))]
    return pct_rank([float(x) for x in v24], REG_RANK_WINDOW)


# ------------------------------------------------------------------------------------------------- simulation
@dataclass(frozen=True)
class Costs:
    name: str
    fee: float  # per side, fraction
    slip_bps: float  # per side

    @property
    def round_trip_bps(self) -> float:
        return 2 * (self.fee * 10000 + self.slip_bps)


COSTS = {"base": Costs("base", 0.0025, 5.0), "adverse": Costs("adverse", 0.0035, 10.0), "low": Costs("low", 0.0020, 5.0)}


@dataclass
class Trade:
    symbol: str
    entry_t: int
    exit_t: int
    entry_px: float
    exit_px: float
    gross: float  # price move after slippage, before fees
    net: float
    reason: str
    regime: str


@dataclass
class Plan:
    """Decision-bar signals mapped onto the 1h execution series (index = 1h bar whose CLOSE is the decision time)."""
    entry: dict[int, float]  # 1h index -> ATR (decision timeframe) at that decision, when the entry rule fires
    allowed: dict[int, float]  # 1h index -> ATR for every decision bar where an entry is permitted (control pool)
    exit_signal: set[int] = field(default_factory=lambda: set[int]())
    regime: dict[int, str] = field(default_factory=lambda: dict[int, str]())


@dataclass(frozen=True)
class ExitRule:
    stop_atr: float  # initial stop = entry reference - stop_atr x ATR
    trail_atr: float | None  # chandelier: highest high since entry - trail_atr x ATR(entry); never lowered
    max_hold_s: int
    use_exit_signal: bool = False


@dataclass
class SimResult:
    trades: list[Trade]
    equity: list[tuple[int, float]]  # (1h close time, equity multiple) marked at each hour close
    exposure_hours: int
    hours: int


def simulate(s: Series, plan: Plan, rule: ExitRule, costs: Costs, start: int, end: int,
             entry_filter: Callable[[int], bool] | None = None) -> SimResult:
    """One symbol, one slice [start, end): 100% notional when long, compounding. Flat at slice end."""
    slip = costs.slip_bps / 10000
    f = costs.fee
    idx = range(bisect.bisect_left(s.t, start), bisect.bisect_right(s.t, end - HOUR))
    trades: list[Trade] = []
    curve: list[tuple[int, float]] = []
    eq = 1.0
    pos: dict[str, Any] | None = None
    pending: tuple[float, float, str] | None = None  # (limit, atr, regime)
    pending_exit: str | None = None
    exposure = 0
    last_i = idx[-1] if idx else -1

    def close_pos(px: float, at: int, reason: str) -> None:
        nonlocal eq, pos
        assert pos is not None
        net = px * (1 - f) / (pos["px"] * (1 + f)) - 1
        trades.append(Trade(s.symbol, pos["t"], at, pos["px"], px, px / pos["px"] - 1, net, reason, pos["regime"]))
        eq = pos["eq0"] * (1 + net)
        pos = None

    for i in idx:
        t, o, hi, lo, c = s.t[i], s.o[i], s.h[i], s.l[i], s.c[i]
        if pos is None and pending is not None:
            limit, a, reg = pending
            px = o * (1 + slip)
            if px <= limit:
                pos = {"t": t, "px": px, "ref": limit / (1 + ENTRY_CAP_BPS / 10000), "atr": a, "eq0": eq,
                       "stop": limit / (1 + ENTRY_CAP_BPS / 10000) - rule.stop_atr * a, "hh": hi, "regime": reg}
            pending = None
        elif pos is not None and pending_exit is not None:
            close_pos(o * (1 - slip), t, pending_exit)
            pending_exit = None
        if pos is not None:
            if lo <= pos["stop"]:
                close_pos(min(pos["stop"], o) * (1 - slip), t, "STOP")
            else:
                pos["hh"] = max(pos["hh"], hi)
                if rule.trail_atr is not None:
                    pos["stop"] = max(pos["stop"], pos["hh"] - rule.trail_atr * pos["atr"])
                if t + HOUR - pos["t"] >= rule.max_hold_s:
                    pending_exit = "TIME"
                elif rule.use_exit_signal and i in plan.exit_signal:
                    pending_exit = "SIGNAL"
        if pos is not None:
            exposure += 1
        if i == last_i:
            if pos is not None:  # flatten inside the slice: at this last bar's close (kernel liquidates before end)
                close_pos(c * (1 - slip), t + HOUR, "END")
                pending_exit = None
        elif pos is None and pending_exit is None and i in plan.entry and (entry_filter is None or entry_filter(i)):
            pending = (c * (1 + ENTRY_CAP_BPS / 10000), plan.entry[i], plan.regime.get(i, ""))
        mark = eq if pos is None else pos["eq0"] * (c * (1 - f) / (pos["px"] * (1 + f)))
        curve.append((t + HOUR, mark))
    return SimResult(trades, curve, exposure, len(idx))


def random_plan(plan: Plan, rate: float, rng: random.Random) -> Plan:
    """RAND-X control: Bernoulli entries at `rate` over the permitted decision pool (same ATR, same exits)."""
    entry = {i: a for i, a in plan.allowed.items() if rng.random() < rate}
    return Plan(entry=entry, allowed=plan.allowed, exit_signal=plan.exit_signal, regime=plan.regime)


# -------------------------------------------------------------------------------------------------- statistics
def max_drawdown(curve: Sequence[float]) -> float:
    peak, dd = -math.inf, 0.0
    for v in curve:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak if peak > 0 else 0.0)
    return dd


def portfolio(results: Sequence[SimResult]) -> tuple[float, float]:
    """Equal-weight independent sub-accounts. Returns (net return, max drawdown) of the summed hourly curve."""
    if not results:
        return 0.0, 0.0
    by_t: dict[int, list[float]] = {}
    for r in results:
        for t, v in r.equity:
            by_t.setdefault(t, []).append(v)
    last = [1.0] * len(results)
    curve: list[float] = []
    pos_of = [{t: v for t, v in r.equity} for r in results]
    for t in sorted(by_t):
        for k, m in enumerate(pos_of):
            if t in m:
                last[k] = m[t]
        curve.append(sum(last) / len(last))
    return curve[-1] - 1 if curve else 0.0, max_drawdown(curve)


def trade_stats(trades: Sequence[Trade], costs: Costs) -> dict[str, float]:
    n = len(trades)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "pf": 0.0, "gross_bps": 0.0, "net_bps": 0.0, "fee_share": 0.0,
                "median_hold_h": 0.0}
    wins = [x.net for x in trades if x.net > 0]
    losses = [-x.net for x in trades if x.net <= 0]
    gross = sum(x.gross for x in trades) / n * 10000
    holds = sorted((x.exit_t - x.entry_t) / HOUR for x in trades)
    pre_fee = sum(x.gross for x in trades)
    fees = n * 2 * costs.fee
    return {"trades": n, "win_rate": len(wins) / n, "pf": (sum(wins) / sum(losses)) if losses else math.inf,
            "gross_bps": gross, "net_bps": sum(x.net for x in trades) / n * 10000,
            "fee_share": fees / pre_fee if pre_fee > 0 else math.inf, "median_hold_h": holds[n // 2]}


def buy_hold(s: Series, start: int, end: int, costs: Costs | None) -> float | None:
    idx = range(bisect.bisect_left(s.t, start), bisect.bisect_right(s.t, end - HOUR))
    if len(idx) < 2:
        return None
    a, b = s.o[idx[0]], s.c[idx[-1]]
    if costs is None:
        return b / a - 1
    slip = costs.slip_bps / 10000
    return b * (1 - slip) * (1 - costs.fee) / (a * (1 + slip) * (1 + costs.fee)) - 1


def dump(path: Path, obj: Any) -> str:
    text = json.dumps(obj, indent=1, sort_keys=True, default=str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")
    return hashlib.sha256(text.encode()).hexdigest()
