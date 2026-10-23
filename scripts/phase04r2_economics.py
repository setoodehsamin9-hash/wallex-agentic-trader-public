"""Phase 04 round 2 — fee economics and horizon diagnostics on TRAIN ONLY (2023-02-01 .. 2025-03-01 UTC).

  uv run python scripts/phase04r2_economics.py   ->  reports/phase-04/round2/ECONOMICS_TRAIN.json

Part A (run BEFORE any family is defined): per timeframe volatility vs round-trip cost, available forward move per
holding horizon, drift, variance ratios per TRAIN slice, regime frequencies and regime-conditional forward returns,
and the TMN decomposition BTC/TMN = BTC/USDT x USDT/TMN. No strategy is evaluated here. VALIDATION/TEST not read.
"""

import math
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase04r2_lib import (  # noqa: E402
    COSTS, HOUR, REPO, Series, aggregate_complete, atr, dump, load_series, regime_labels, ts)

TRAIN = (ts("2023-02-01"), ts("2025-03-01"))
TRAIN_SLICES = [("T1", "2023-02-01", "2023-07-01"), ("T2", "2023-07-01", "2023-12-01"), ("T3", "2023-12-01", "2024-05-01"),
                ("T4", "2024-05-01", "2024-10-01"), ("T5", "2024-10-01", "2025-03-01")]
SYMBOLS = ["BTCTMN", "ETHTMN", "BNBTMN", "XAUTTMN", "USDTTMN"]
HORIZONS_H = [4, 12, 24, 48, 96, 168]
OUT = REPO / "reports" / "phase-04" / "round2" / "ECONOMICS_TRAIN.json"


def in_train(t: int) -> bool:
    return TRAIN[0] <= t < TRAIN[1]


def vr(logrets: list[float], q: int) -> float | None:
    """Variance ratio Var(q-period sum) / (q Var(1-period)), non-overlapping blocks."""
    if len(logrets) < q * 30:
        return None
    v1 = statistics.pvariance(logrets)
    blocks = [sum(logrets[i:i + q]) for i in range(0, len(logrets) - q + 1, q)]
    return statistics.pvariance(blocks) / (q * v1) if v1 > 0 else None


def fwd_returns(s: Series, h: int) -> dict[int, float]:
    """t (1h open) -> log return from the NEXT bar open to the open h hours later (entry after the decision bar)."""
    pos = {t: i for i, t in enumerate(s.t)}
    out: dict[int, float] = {}
    for t in s.t:
        j = pos.get(t + HOUR)
        k = pos.get(t + HOUR + h * HOUR)
        if j is not None and k is not None and in_train(t) and in_train(t + HOUR + h * HOUR):
            out[t] = math.log(s.o[k] / s.o[j])
    return out


def main() -> None:
    rt = COSTS["base"].round_trip_bps
    out: dict[str, Any] = {"train": [TRAIN[0], TRAIN[1]], "round_trip_bps": {k: c.round_trip_bps for k, c in COSTS.items()},
                           "symbols": {}}
    hourly = {s: load_series("1h", s, 3600) for s in SYMBOLS}
    for sym, h1 in hourly.items():
        h4, last4 = aggregate_complete(h1, 4)
        d1, _ = aggregate_complete(h1, 24)
        info: dict[str, Any] = {}
        for name, s in (("1h", h1), ("4h", h4), ("1d_from_1h", d1)):
            a = atr(s, 14)
            bps = [ai / c * 10000 for ai, c, t in zip(a, s.c, s.t) if ai is not None and in_train(t)]
            if bps:
                med = statistics.median(bps)
                info[f"atr14_median_bps_{name}"] = round(med, 1)
                info[f"cost_over_atr_{name}"] = round(rt / med, 3)
        moves: dict[str, Any] = {}
        for hh in HORIZONS_H:
            fr = list(fwd_returns(h1, hh).values())
            if len(fr) < 100:
                continue
            moves[f"{hh}h"] = {"n": len(fr), "mean_bps": round(statistics.fmean(fr) * 10000, 1),
                               "median_abs_bps": round(statistics.median(abs(x) for x in fr) * 10000, 1),
                               "std_bps": round(statistics.pstdev(fr) * 10000, 1),
                               "share_abs_gt_rt": round(sum(1 for x in fr if abs(x) * 10000 > rt) / len(fr), 3)}
        info["forward_move_from_next_open"] = moves
        vrs: dict[str, Any] = {}
        for name, a0, b0 in TRAIN_SLICES:
            a, b = ts(a0), ts(b0)
            lr = [math.log(h1.c[i] / h1.c[i - 1]) for i in range(1, len(h1.c))
                  if a <= h1.t[i] < b and h1.t[i] - h1.t[i - 1] == HOUR]
            vrs[name] = {f"VR{q}": (round(x, 3) if (x := vr(lr, q)) is not None else None) for q in (4, 24, 96)}
        info["variance_ratio_1h_by_slice"] = vrs
        labels = regime_labels(h4)
        reg_at: dict[int, str] = {}
        for k, lab in enumerate(labels):
            reg_at[h1.t[last4[k]]] = lab  # label known at the close of the 4h bar = close of its last 1h bar
        cond: dict[str, Any] = {}
        for hh in (24, 48, 96):
            fr = fwd_returns(h1, hh)
            groups: dict[str, list[float]] = {}
            for t, r in fr.items():
                if t in reg_at:
                    groups.setdefault(reg_at[t], []).append(r)
            cond[f"{hh}h"] = {g: {"n": len(v), "mean_bps": round(statistics.fmean(v) * 10000, 1),
                                  "hit_gt_rt": round(sum(1 for x in v if x * 10000 > rt) / len(v), 3)}
                              for g, v in sorted(groups.items()) if len(v) >= 30}
        freq: dict[str, int] = {}
        for t, lab in reg_at.items():
            if in_train(t):
                freq[lab] = freq.get(lab, 0) + 1
        info["regime_frequency_4h_bars"] = freq
        info["regime_conditional_forward_bps"] = cond
        out["symbols"][sym] = info
    bt, us = hourly["BTCTMN"], hourly["USDTTMN"]
    ub = {t: c for t, c in zip(us.t, us.c)}
    pairs = [(t, c, ub[t]) for t, c in zip(bt.t, bt.c) if t in ub and in_train(t)]
    a, b = pairs[0], pairs[-1]
    out["tmn_decomposition_train"] = {
        "BTCTMN_ret": round(b[1] / a[1] - 1, 4), "USDTTMN_ret": round(b[2] / a[2] - 1, 4),
        "BTC_in_USDT_ret": round((b[1] / b[2]) / (a[1] / a[2]) - 1, 4),
        "log_share_from_USDTTMN": round(math.log(b[2] / a[2]) / math.log(b[1] / a[1]), 3)}
    digest = dump(OUT, out)
    for sym, info in out["symbols"].items():
        print(sym, {k: v for k, v in info.items() if k.startswith(("atr", "cost"))})
        print("  moves", {k: (v["mean_bps"], v["median_abs_bps"], v["share_abs_gt_rt"]) for k, v in
                          info["forward_move_from_next_open"].items()})
        print("  VR", info["variance_ratio_1h_by_slice"])
        print("  regime freq", info["regime_frequency_4h_bars"])
        for hh, g in info["regime_conditional_forward_bps"].items():
            print("  cond", hh, {k: (v["n"], v["mean_bps"], v["hit_gt_rt"]) for k, v in g.items()})
    print(out["tmn_decomposition_train"])
    print("sha256", digest)


if __name__ == "__main__":
    main()
