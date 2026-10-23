"""Phase 04 round 2 — G10 verdict: Experiment Harness (Decimal kernel) vs research simulator on VALIDATION.

  uv run python scripts/phase04r2_g10.py   ->  reports/phase-04/round2/G10.json / G10.md

Rule (PREREGISTRATION_R2.md section 11): kernel pooled net > 0 at base over the seven VALIDATION slices AND >= 5 of 7
slices with the same sign as the research simulator, with clean provenance (committed code) and no failed trial.
Fidelity runs (regime-off twin on 1m, C2 v0.2 calibration on 1m) are reported alongside; they are not gates.
"""

import csv
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase04r2_lib import dump  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
R2 = ROOT / "reports" / "phase-04" / "round2"
EXP = R2 / "experiments"


def harness(exp_id: str) -> dict[str, Any]:
    rows = list(csv.DictReader((EXP / exp_id / "SUMMARY.csv").open()))
    trials = [json.loads(x) for x in (EXP / exp_id / "TRIALS.jsonl").read_text().splitlines() if x.strip()]
    out: dict[str, Any] = {"dirty_code": any(t["code"]["dirty"] for t in trials),
                           "failed": [r["trial_id"] for r in rows if r["status"] != "PASS"], "costs": {}}
    for cost in ("base", "adverse", "low"):
        rs = [r for r in rows if r["cost"] == cost]
        out["costs"][cost] = {"pooled_pnl_tmn": sum(float(r["realized_pnl_tmn"]) for r in rs),
                              "trades": sum(int(r["trades"]) for r in rs),
                              "slices": {r["split"].upper(): {"return_pct": float(r["return_pct"]), "trades": int(r["trades"]),
                                                              "max_dd_pct": float(r["max_drawdown_pct"])} for r in rs}}
    return out


def main() -> None:
    val = json.loads((R2 / "VAL.json").read_text())
    frozen = "F2|4|48|5|1"
    sim = {x["slice"]: x for x in val["results"][frozen]["slices"]}
    k = harness("p04r2-g10-f2")
    kb = k["costs"]["base"]
    same = {s: (kb["slices"][s]["return_pct"] > 0) == (sim[s]["net"] > 0) for s in sim}
    verdict = {"pooled_positive": kb["pooled_pnl_tmn"] > 0, "same_sign_slices": sum(same.values()),
               "same_sign_ok": sum(same.values()) >= 5, "clean_code": not k["dirty_code"], "no_failed_trials": not k["failed"]}
    verdict["pass"] = all(v for key, v in verdict.items() if key != "same_sign_slices")
    fidelity: dict[str, dict[str, Any]] = {e: harness(e) for e in ("p04r2-g10-f2-off", "p04r2-g10-f2-off-1m", "p04r2-cal-c2-1m")}
    out: dict[str, Any] = {"frozen": frozen, "verdict": verdict, "kernel": k, "same_sign": same,
                           "simulator": {s: {"net": x["net"], "trades": x["trades"]} for s, x in sim.items()},
                           "fidelity": fidelity}
    out["simulator_twin"] = {x["slice"]: {"net": x["net"], "trades": x["trades"]}
                             for x in val["results"]["F2|4|48|5|0"]["slices"]}
    out["simulator_c2"] = {x["slice"]: {"net": x["net"], "trades": x["trades"]} for x in val["baselines"]["C2|1|24|3|0"]["slices"]}
    digest = dump(R2 / "G10.json", out)
    lines = ["# G10 — kernel (Experiment Harness, Decimal, risk-sized) vs research simulator (100% notional)", "",
             f"Frozen `{frozen}`: verdict **{'PASS' if verdict['pass'] else 'FAIL'}** {verdict}", "",
             "| slice | kernel return % (base) | kernel trades | simulator net % | simulator trades | same sign |",
             "|---|---:|---:|---:|---:|---|"]
    for s in sim:
        lines.append(f"| {s} | {kb['slices'][s]['return_pct']:+.2f} | {kb['slices'][s]['trades']} | {sim[s]['net'] * 100:+.1f} | "
                     f"{sim[s]['trades']} | {'✔' if same[s] else '✘'} |")
    lines += ["", f"Kernel pooled P&L: base {kb['pooled_pnl_tmn']:,.0f} TMN, adverse {k['costs']['adverse']['pooled_pnl_tmn']:,.0f}, "
              f"low {k['costs']['low']['pooled_pnl_tmn']:,.0f} (each slice starts at 5,000,000 TMN).", "", "Fidelity runs:", ""]
    for e, f in fidelity.items():
        b = f["costs"]["base"]
        lines.append(f"- {e}: base pooled {b['pooled_pnl_tmn']:,.0f} TMN, trades {b['trades']}, slices "
                     + ", ".join(f"{s} {v['return_pct']:+.2f}% ({v['trades']})" for s, v in b["slices"].items()))
    (R2 / "G10.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("sha256", digest)


if __name__ == "__main__":
    main()
