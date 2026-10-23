"""Phase 04 loss diagnosis on the DEVELOPMENT window only (2026-09-07 .. 2026-10-13 UTC, Phase 03 TRAIN+VALIDATION).

  uv run python scripts/phase04_diagnose.py

The final window (>= 2026-10-13) is NOT read: bars are cut before any statistic is computed and an assertion guards
it. Float statistics are research-only (no trading decision or money is computed here). Output:
reports/phase-04/diagnosis/DIAGNOSIS.json. No network, no new dependency.

Questions answered with data (not assumed):
  1. volatility per timeframe vs round-trip cost        -> is any move large enough to pay fees?
  2. return autocorrelation / variance ratio            -> momentum or mean reversion at each horizon?
  3. forward returns after entry conditions (de-overlapped events, gross, from next-bar open)
     C1 breakout, plain Donchian breakout, EMA trend state, Bollinger dip, unconditional baseline
  4. C1 triple-barrier geometry with the shipped parameters -> TP/stop/time mix and required win rate
  5. drift (buy & hold) and hour-of-day range/volume profile
"""

import json
import math
import statistics
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wallex_agent.market.bars import Bar, aggregate  # noqa: E402
from wallex_agent.market.dataset import load_dataset  # noqa: E402

DEV_START = datetime(2026, 9, 7, tzinfo=UTC)
DEV_END = datetime(2026, 10, 13, tzinfo=UTC)  # exclusive; the final window starts here and is never read
FEE_SIDE_BPS = 25.0  # ASSUMED 0.25% per side (Phase 03 base)
SLIP_SIDE_BPS = 5.0
RT_BPS = 2 * (FEE_SIDE_BPS + SLIP_SIDE_BPS)  # 60 bps round trip, before spread
TIMEFRAMES = {"15m": 900, "1h": 3600, "4h": 14400}


def f(x: Decimal) -> float:
    return float(x)


def sma(xs: list[float], n: int, i: int) -> float | None:
    return sum(xs[i - n + 1:i + 1]) / n if i >= n - 1 else None


def ema_series(xs: list[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    k, e = 2 / (n + 1), None
    for i, x in enumerate(xs):
        e = x if e is None else x * k + e * (1 - k)
        out.append(e if i >= n - 1 else None)
    return out


def atr_series(bars: list[Bar], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    trs = [f(bars[0].high - bars[0].low)] + [max(f(c.high - c.low), abs(f(c.high - p.close)), abs(f(c.low - p.close)))
                                             for p, c in zip(bars, bars[1:])]
    for i in range(n, len(bars)):
        out[i] = sum(trs[i - n + 1:i + 1]) / n
    return out


def describe(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
    return {"n": len(xs), "mean": round(statistics.fmean(xs), 2), "median": round(s[len(s) // 2], 2),
            "p25": round(s[len(s) // 4], 2), "p75": round(s[3 * len(s) // 4], 2), "sd": round(sd, 2),
            "t": round(statistics.fmean(xs) / (sd / math.sqrt(len(xs))), 2) if sd > 0 and len(xs) > 2 else None,
            "hit_gt0": round(sum(x > 0 for x in xs) / len(xs), 3), "hit_gt_rt": round(sum(x > RT_BPS for x in xs) / len(xs), 3)}


def autocorr(xs: list[float], lag: int) -> float:
    m = statistics.fmean(xs)
    num = sum((xs[i] - m) * (xs[i - lag] - m) for i in range(lag, len(xs)))
    den = sum((x - m) ** 2 for x in xs)
    return round(num / den, 4)


def variance_ratio(r: list[float], q: int) -> float:
    """Lo-MacKinlay VR(q) with overlapping q-sums; <1 mean reversion, >1 momentum."""
    v1 = statistics.pvariance(r)
    sums = [sum(r[i:i + q]) for i in range(len(r) - q + 1)]
    return round(statistics.pvariance(sums) / (q * v1), 3)


def forward_events(bars: list[Bar], cond: list[bool], h: int) -> list[float]:
    """Gross bps from next bar OPEN to close h bars later; events de-overlapped (skip h bars after each)."""
    out: list[float] = []
    i = 0
    while i < len(bars) - h - 1:
        if cond[i]:
            entry, exit_ = f(bars[i + 1].open), f(bars[i + h].close)
            out.append((exit_ / entry - 1) * 1e4)
            i += h
        else:
            i += 1
    return out


def c1_geometry(bars: list[Bar], cond: list[bool], atr: list[float | None], stop_atr: float, tp_atr: float,
                max_bars: int) -> dict[str, Any]:
    """Shipped C1 exit geometry replayed on bars (stop first when both in one bar), entries at next open."""
    res: dict[str, int] = {"TP": 0, "STOP": 0, "TIME": 0}
    rets: list[float] = []
    mfe_atr: list[float] = []
    i = 0
    while i < len(bars) - max_bars - 1:
        a = atr[i]
        if not cond[i] or a is None:
            i += 1
            continue
        entry = f(bars[i + 1].open)
        stop, tp = entry - stop_atr * a, entry + tp_atr * a
        out, hi = None, entry
        for j in range(i + 1, i + 1 + max_bars):
            b = bars[j]
            hi = max(hi, f(b.high))
            if f(b.low) <= stop:
                out, kind = min(stop, f(b.open)), "STOP"
                break
            if f(b.high) >= tp:
                out, kind = tp, "TP"
                break
        else:
            out, kind, j = f(bars[i + max_bars].close), "TIME", i + max_bars
        res[kind] += 1
        rets.append((out / entry - 1) * 1e4)
        mfe_atr.append((hi - entry) / a)
        i = j + 1
    n = sum(res.values())
    win, loss = tp_atr, stop_atr
    return {"events": n, "exits": res, "gross_bps": describe(rets), "net_bps_mean": round(statistics.fmean(rets) - RT_BPS, 2) if rets else None,
            "mfe_atr": describe(mfe_atr), "tp_share": round(res["TP"] / n, 3) if n else None,
            "note": f"geometric break-even win rate ignoring costs = {loss / (win + loss):.3f}"}


def study(sym: str, minute: list[Bar]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tf, sec in TIMEFRAMES.items():
        bars = aggregate(minute, sec)
        closes = [f(b.close) for b in bars]
        rets = [math.log(closes[i] / closes[i - 1]) * 1e4 for i in range(1, len(closes))]
        atr = atr_series(bars, 14)
        rng = [f(b.high - b.low) / f(b.close) * 1e4 for b in bars]
        atr_bps = [a / closes[i] * 1e4 for i, a in enumerate(atr) if a is not None]
        e20, e50 = ema_series(closes, 20), ema_series(closes, 50)
        lb = {"15m": 16, "1h": 24, "4h": 12}[tf]
        hold = {"15m": [1, 4, 8, 16, 24], "1h": [1, 2, 4, 8, 16], "4h": [1, 2, 3, 6]}[tf]
        c1: list[bool] = []
        don: list[bool] = []
        trend: list[bool] = []
        dip: list[bool] = []
        every: list[bool] = []
        for i, b in enumerate(bars):
            a = atr[i]
            prior = [x.high for x in bars[max(0, i - lb):i]]
            m32 = sma(closes, 32, i)
            m20 = sma(closes, 20, i)
            sd20 = statistics.pstdev(closes[i - 19:i + 1]) if i >= 19 else None
            brk: bool = a is not None and len(prior) == lb and closes[i] > f(max(prior)) + 0.1 * a
            c1.append(bool(brk and m32 is not None and closes[i] > m32))
            don.append(bool(brk))
            trend.append(bool(e20[i] is not None and e50[i] is not None and e20[i] > e50[i] and closes[i] > e20[i]))  # type: ignore[operator]
            dip.append(bool(m20 is not None and sd20 and closes[i] < m20 - 2 * sd20))
            every.append(True)
        fwd = {name: {f"h{h}": describe(forward_events(bars, cond, h)) for h in hold}
               for name, cond in (("unconditional", every), ("c1_breakout", c1), ("donchian", don), ("ema_trend_state", trend),
                                  ("bollinger_dip", dip))}
        out[tf] = {
            "bars": len(bars),
            "range_bps": describe(rng), "atr14_bps": describe(atr_bps),
            "rt_cost_over_median_atr": round(RT_BPS / sorted(atr_bps)[len(atr_bps) // 2], 2) if atr_bps else None,
            "autocorr": {f"lag{k}": autocorr(rets, k) for k in (1, 2, 4)},
            "variance_ratio": {f"q{q}": variance_ratio(rets, q) for q in (2, 4, 8, 16) if len(rets) > 4 * q},
            "forward_gross_bps": fwd,
        }
        if tf == "15m":
            out[tf]["c1_shipped_geometry"] = c1_geometry(bars, c1, atr, 1.5, 3.0, 24)
        if tf == "1h":
            out[tf]["c1_geometry_1h_wide"] = c1_geometry(bars, c1, atr, 2.0, 4.0, 12)
    first, last = f(minute[0].open), f(minute[-1].close)
    out["buy_and_hold_pct"] = round((last / first - 1) * 100, 2)
    hourly = aggregate(minute, 3600)
    by_hour: dict[int, list[float]] = {}
    vol_hour: dict[int, float] = {}
    for b in hourly:
        h = b.ts_open.hour
        by_hour.setdefault(h, []).append(f(b.high - b.low) / f(b.close) * 1e4)
        vol_hour[h] = vol_hour.get(h, 0.0) + f(b.volume * b.close)
    tot = sum(vol_hour.values())
    out["hour_utc_profile"] = {h: {"mean_range_bps": round(statistics.fmean(v), 1), "notional_share": round(vol_hour[h] / tot, 3)}
                               for h, v in sorted(by_hour.items())}
    return out


def main() -> int:
    ds = load_dataset(REPO / "data" / "datasets" / "wallex-1m-btctmn-ethtmn")
    result: dict[str, Any] = {"window": [DEV_START.isoformat(), DEV_END.isoformat()], "rt_cost_bps": RT_BPS,
                              "cost_assumption": "fee 0.25%/side + slippage 5 bps/side, spread excluded (ASSUMED)", "symbols": {}}
    for sym, bars in ds.bars.items():
        dev = [b for b in bars if DEV_START <= b.ts_open and b.ts_open + timedelta(minutes=1) <= DEV_END]
        assert dev and dev[-1].ts_open < DEV_END, "final window must not be read"
        result["symbols"][sym] = study(sym, dev)
    path = REPO / "reports" / "phase-04" / "diagnosis" / "DIAGNOSIS.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
