"""Phase 04 round 2 — data validation/profile of the Codex historical pack (development data only, < 2026-10-13).

  uv run python scripts/phase04r2_profile.py   ->  reports/phase-04/round2/DATA_PROFILE.json

Checks: file hash vs the COMMITTED data/historical/MANIFEST.json, BLOCKED series refused, coverage and gaps,
zero-volume / flat bars (illiquidity markers), robust (MAD) z-score return anomalies (flag only, never repaired),
and a cross-check of native 1h bars against 1h bars aggregated from the 1m series (same metric computed two ways).
"""

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase04r2_lib import (  # noqa: E402
    COMMITTED_MANIFEST, DEV_END_TS, REPO, Series, aggregate_complete, dump, iso, load_series)

SYMBOLS = ["BTCTMN", "ETHTMN", "BNBTMN", "XAUTTMN", "USDTTMN"]
OUT = REPO / "reports" / "phase-04" / "round2" / "DATA_PROFILE.json"


def profile(s: Series) -> dict[str, Any]:
    gaps = [(b - a) // s.interval - 1 for a, b in zip(s.t, s.t[1:]) if b - a != s.interval]
    expected = (s.t[-1] - s.t[0]) // s.interval + 1
    lr = [math.log(b / a) for a, b in zip(s.c, s.c[1:]) if a > 0 and b > 0]
    med = statistics.median(lr)
    mad = statistics.median(abs(x - med) for x in lr) or 1e-12
    z = [(x - med) / (1.4826 * mad) for x in lr]
    big = sorted(((abs(zz), i) for i, zz in enumerate(z) if abs(zz) > 10), reverse=True)
    return {
        "rows": len(s.t), "first": iso(s.t[0]), "last_open": iso(s.t[-1]), "expected_rows": expected,
        "coverage": round(len(s.t) / expected, 5), "gap_count": len(gaps), "missing_bars": sum(gaps),
        "longest_gap_bars": max(gaps) if gaps else 0,
        "zero_volume_share": round(sum(1 for v in s.v if v == 0) / len(s.v), 4),
        "flat_bar_share": round(sum(1 for h, lo in zip(s.h, s.l) if h == lo) / len(s.h), 4),
        "abs_logret_p99_bps": round(sorted(abs(x) for x in lr)[int(0.99 * len(lr))] * 10000, 1),
        "robust_z_gt10_count": len(big),
        "top_anomalies": [{"open": iso(s.t[i + 1]), "ret_bps": round(lr[i] * 10000, 1), "z": round(zz, 1)}
                          for zz, i in big[:5]],
        "sha256_verified": s.sha256,
    }


def cross_check(sym: str, h1: Series, m1: Series) -> dict[str, Any]:
    agg, _ = aggregate_complete(m1, 60)
    native = dict(zip(h1.t, h1.c))
    diffs = [abs(c / native[t] - 1) * 10000 for t, c in zip(agg.t, agg.c) if t in native]
    return {"symbol": sym, "overlap_hours": len(diffs), "native_hours_in_window": sum(1 for t in h1.t if t >= m1.t[0]),
            "exact_close_match_share": round(sum(1 for d in diffs if d < 1e-9) / len(diffs), 4) if diffs else None,
            "median_abs_diff_bps": round(statistics.median(diffs), 3) if diffs else None,
            "max_abs_diff_bps": round(max(diffs), 2) if diffs else None}


def main() -> None:
    manifest: dict[str, Any] = json.loads(COMMITTED_MANIFEST.read_text())
    status = {f"{s['dataset']}/{s['symbol']}": {"quality_status": s.get("quality_status"), "flags": s.get("flags"),
                                                 "invalid_ohlcv_rows": s.get("invalid_ohlcv_rows"),
                                                 "grid_mismatch_rows": s.get("grid_mismatch_rows")}
              for s in manifest["series"]}
    out: dict[str, Any] = {"dev_end_exclusive": iso(DEV_END_TS), "pack_manifest_status": status, "series": {},
                           "cross_check_1h_vs_1m": []}
    hourly: dict[str, Series] = {}
    for sym in SYMBOLS:
        h1 = load_series("1h", sym, 3600)
        m1 = load_series("1m", sym, 60)
        hourly[sym] = h1
        out["series"][f"1h/{sym}"] = profile(h1)
        out["series"][f"1m/{sym}"] = profile(m1)
        out["cross_check_1h_vs_1m"].append(cross_check(sym, h1, m1))
    refused: list[str] = []
    for sym in SYMBOLS:
        try:
            load_series("1d", sym, 86400)
        except RuntimeError as e:
            refused.append(f"1d/{sym}: {e}")
    out["daily_refused"] = refused
    digest = dump(OUT, out)
    for k, v in out["series"].items():
        print(f"{k:12s} rows={v['rows']:>7} {v['first']}..{v['last_open']} cov={v['coverage']:.4f} gaps={v['gap_count']}"
              f" longest={v['longest_gap_bars']} zeroV={v['zero_volume_share']} flat={v['flat_bar_share']}"
              f" p99={v['abs_logret_p99_bps']}bps z>10={v['robust_z_gt10_count']}")
    for c in out["cross_check_1h_vs_1m"]:
        print(c)
    print("\n".join(refused))
    print("sha256", digest)


if __name__ == "__main__":
    main()
