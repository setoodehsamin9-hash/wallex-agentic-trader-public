"""Phase 04 round 2 — uncertainty of the frozen F2 edge on VALIDATION (research simulator, BTC+ETH, base cost).

  uv run python scripts/phase04r2_stats.py   ->  reports/phase-04/round2/STATS_VAL.json

Per-trade net return distribution (mean AND median: trend following is right-skewed), bootstrap 95% CI of the mean
(10,000 resamples, fixed seed), the same for its regime-off twin and for RAND-X trades, and the multiple-testing
context. Information only; no gate is re-evaluated here.
"""

import random
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase04r2_lib import COSTS, dump, random_plan, simulate  # noqa: E402
from phase04r2_run import OUT, PRIMARY, SLICES, Market, parse  # noqa: E402


def trades_for(key: str, mk: dict[str, Market], rand_rate: float | None = None, seed: int = 0) -> list[float]:
    c = parse(key)
    base = COSTS["base"]
    out: list[float] = []
    rng = random.Random(seed)
    for _, a, b in SLICES["VAL"]:
        for s in PRIMARY:
            plan = mk[s].plan(c, base)
            if rand_rate is not None:
                plan = random_plan(plan, rand_rate, rng)
            out.extend(t.net for t in simulate(mk[s].h1, plan, c.rule(), base, a, b).trades)
    return out


def describe(xs: list[float], seed: int = 11) -> dict[str, Any]:
    xs_s = sorted(xs)
    n = len(xs)
    rng = random.Random(seed)
    boots = sorted(statistics.fmean(rng.choices(xs, k=n)) for _ in range(10_000))
    return {"n": n, "mean_bps": statistics.fmean(xs) * 1e4, "median_bps": statistics.median(xs) * 1e4,
            "p05_bps": xs_s[int(0.05 * n)] * 1e4, "p95_bps": xs_s[int(0.95 * n)] * 1e4,
            "win_share": sum(1 for x in xs if x > 0) / n, "mean_ci95_bps": [boots[250] * 1e4, boots[9750] * 1e4],
            "p_mean_le_0": sum(1 for b in boots if b <= 0) / len(boots)}


def main() -> None:
    import json

    val = json.loads((OUT / "VAL.json").read_text())
    mk = {s: Market(s) for s in PRIMARY}
    frozen, twin = "F2|4|48|5|1", "F2|4|48|5|0"
    rate = val["results"][frozen]["rand_x"]["rate"]
    pooled_rand: list[float] = []
    for seed in range(1, 31):
        pooled_rand.extend(trades_for(frozen, mk, rand_rate=rate, seed=seed))
    out = {"frozen": describe(trades_for(frozen, mk)), "twin_regime_off": describe(trades_for(twin, mk)),
           "rand_x_30_seeds_pooled": describe(pooled_rand),
           "multiple_testing": {"economics_gates": 4, "train_grid_configs": 32, "frozen": 2, "validation_tested": 4,
                                "rand_x_p_value_frozen": val["results"][frozen]["rand_x"]["p_value"],
                                "bonferroni_x4": min(1.0, 4 * val["results"][frozen]["rand_x"]["p_value"]),
                                "note": "p = 1/301 is the resolution floor of 300 seeds; all 300 random paths were below"}}
    for k, v in out.items():
        print(k, {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in v.items()})
    print("sha256", dump(OUT / "STATS_VAL.json", out))


if __name__ == "__main__":
    main()
