"""Apply the PRE-REGISTERED Phase 04 decision rules (reports/phase-04/PREREGISTRATION.md section 6) to experiment output.

  uv run python scripts/phase04_evaluate.py ROUND_LABEL EXP_ID [EXP_ID ...] --control CTRL_EXP_ID [--roles TRAIN VALIDATION]

Reads <root>/<exp>/TRIALS.jsonl (completed, hash-checked by the harness), pools the 5 development slices per
configuration and cost, and writes <root>/<ROUND_LABEL>_EVALUATION.json + .md. Money stays Decimal; ratios are
reported rounded. Buy & hold per slice (equal-weight BTCTMN/ETHTMN, 100% invested, no costs) is a benchmark only.
No rule is tuned here: thresholds below are copied from the pre-registration.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from wallex_agent.market.dataset import load_dataset  # noqa: E402

MIN_TRADES = 30          # R2
MIN_POSITIVE_SLICES = 3  # R3
WORST_SLICE_PCT = Decimal("-3")  # R3
MIN_PF = Decimal("1.1")  # R4
MAX_FEE_SHARE = Decimal("0.5")  # R5
D0 = Decimal(0)


def dec(x: Any) -> Decimal:
    return Decimal(str(x)) if x is not None else D0


def rows(root: Path, exp: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (root / exp / "TRIALS.jsonl").read_text().splitlines() if line.strip()]


def label(params: dict[str, Any]) -> str:
    keys = [k for k in ("lookback_bars", "trend_gate", "entry_trend_atr", "stop_atr", "max_holding_s") if k in params]
    out: list[str] = []
    for k in keys:
        v = params[k]
        out.append(f"hold={int(v) // 3600}h" if k == "max_holding_s" else f"{k.replace('_bars', '').replace('_atr', '')}={v}")
    return " ".join(out)


def pool(trials: list[dict[str, Any]]) -> dict[str, Any]:
    m: list[dict[str, Any]] = [t["metrics"] or {} for t in trials]
    net = sum((dec(x.get("net_pnl_tmn")) for x in m), D0)
    fees = sum((dec(x.get("fees_tmn")) for x in m), D0)
    gp = sum((dec(x.get("gross_profit_tmn")) for x in m), D0)
    gl = sum((dec(x.get("gross_loss_tmn")) for x in m), D0)
    trades = sum(int(x.get("trades") or 0) for x in m)
    slices = {t["split"]["name"]: {"role": t["split"]["role"], "net": str(dec(t["metrics"]["net_pnl_tmn"]).quantize(Decimal(1))),
                                   "return_pct": t["metrics"]["return_pct"], "trades": t["metrics"]["trades"],
                                   "max_dd_pct": t["metrics"]["max_drawdown_pct"]} for t in trials if t["metrics"]}
    prefee = net + fees
    return {"net_tmn": net, "fees_tmn": fees, "pre_fee_tmn": prefee, "trades": trades,
            "profit_factor": (gp / gl) if gl > 0 else None, "positive_slices": sum(1 for s in slices.values() if dec(s["net"]) > 0),
            "worst_slice_pct": min((dec(s["return_pct"]) for s in slices.values()), default=D0),
            "max_dd_pct": max((dec(s["max_dd_pct"]) for s in slices.values()), default=D0),
            "fee_share": (fees / prefee) if prefee > 0 else None, "failed": sum(1 for t in trials if t["status"] != "PASS"),
            "slices": slices}


def buy_and_hold(splits: list[dict[str, Any]]) -> dict[str, Any]:
    ds = load_dataset(REPO / "data" / "datasets" / "wallex-1m-btctmn-ethtmn")
    out: dict[str, Any] = {}
    for sp in splits:
        start, end = (datetime.fromisoformat(sp[k].replace("Z", "+00:00")) for k in ("start_utc", "end_utc"))
        per: dict[str, Decimal] = {}
        for sym, bars in ds.bars.items():
            inside = [b for b in bars if start <= b.ts_open < end]
            per[sym] = (inside[-1].close / inside[0].open - 1) * 100
        out[sp["name"]] = {"role": sp["role"], **{s: str(v.quantize(Decimal("0.01"))) for s, v in per.items()},
                           "equal_weight_pct": str((sum(per.values(), D0) / len(per)).quantize(Decimal("0.01")))}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("round_label")
    ap.add_argument("experiments", nargs="+")
    ap.add_argument("--control", required=True)
    ap.add_argument("--root", default="reports/phase-04/experiments")
    ap.add_argument("--roles", nargs="+", default=["TRAIN", "VALIDATION"])
    a = ap.parse_args()
    root = REPO / a.root
    roles = set(a.roles)
    configs: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    params: dict[tuple[str, str], dict[str, Any]] = {}
    for exp in [*a.experiments, a.control]:
        for t in rows(root, exp):
            if t["split"]["role"] in roles:
                configs[(exp, t["parameter_hash"], t["cost"]["name"])].append(t)
                params[(exp, t["parameter_hash"])] = t["parameters"]
    pooled = {k: pool(v) for k, v in configs.items()}
    ctrl = {(params[(e, h)]["max_holding_s"], c): p for (e, h, c), p in pooled.items() if e == a.control}
    results: list[dict[str, Any]] = []
    for exp in a.experiments:
        hashes = sorted({h for (e, h) in params if e == exp})
        for h in hashes:
            prm = params[(exp, h)]
            base = pooled[(exp, h, "base")]
            neighbours = [n for n in hashes if n != h and sum(params[(exp, n)][k] != prm[k] for k in prm) == 1]
            nb_net = sum((pooled[(exp, n, "base")]["net_tmn"] for n in neighbours), D0) / len(neighbours) if neighbours else D0
            ctrl_base = ctrl.get((prm["max_holding_s"], "base"))
            reasons: list[str] = []
            if base["net_tmn"] <= 0:
                reasons.append("R1 pooled net <= 0")
            if base["trades"] < MIN_TRADES:
                reasons.append(f"R2 trades {base['trades']} < {MIN_TRADES} (insufficient evidence)")
            if base["positive_slices"] < MIN_POSITIVE_SLICES or base["worst_slice_pct"] < WORST_SLICE_PCT:
                reasons.append(f"R3 positive slices {base['positive_slices']}/5, worst {base['worst_slice_pct']}%")
            if base["profit_factor"] is None or base["profit_factor"] < MIN_PF:
                reasons.append("R4 profit factor < 1.1")
            if base["fee_share"] is None or base["fee_share"] > MAX_FEE_SHARE:
                reasons.append("R5 fees > 50% of pre-fee P&L (or pre-fee <= 0)")
            if ctrl_base is None or base["net_tmn"] <= ctrl_base["net_tmn"]:
                reasons.append("R6 not above drift control (beta only)")
            if nb_net <= 0:
                reasons.append("R7 neighbours' mean net <= 0 (no plateau)")
            adverse = pooled.get((exp, h, "adverse"))
            robust = adverse is not None and adverse["net_tmn"] > 0
            verdict = "SURVIVES" if not reasons and robust else ("FRAGILE" if not reasons else "REJECT")
            results.append({"experiment": exp, "parameter_hash": h, "config": label(prm), "verdict": verdict, "reasons": reasons,
                            "base": base, "adverse": adverse, "low": pooled.get((exp, h, "low")),
                            "neighbour_mean_net_tmn": nb_net, "control_net_tmn": ctrl_base["net_tmn"] if ctrl_base else None})
    control_rows = [{"config": label(params[(e, h)]), "cost": c, **{k: p[k] for k in ("net_tmn", "trades", "profit_factor", "positive_slices",
                                                                                     "worst_slice_pct", "fees_tmn")}}
                    for (e, h, c), p in sorted(pooled.items()) if e == a.control]
    splits = [t["split"] for t in rows(root, a.control) if t["split"]["role"] in roles]
    uniq = list({s["name"]: s for s in splits}.values())
    bh = buy_and_hold(uniq)
    out: dict[str, Any] = {"round": a.round_label, "roles": sorted(roles), "rules": "reports/phase-04/PREREGISTRATION.md section 6",
                           "buy_and_hold_benchmark_pct": bh, "control": control_rows, "configurations": results}
    text = json.dumps(out, default=lambda x: str(x.quantize(Decimal("0.0001"))) if isinstance(x, Decimal) else str(x), indent=1, ensure_ascii=False)
    (root / f"{a.round_label}_EVALUATION.json").write_text(text + "\n")

    def fmt(x: Any, q: str = "1") -> str:
        return "—" if x is None else str(Decimal(x).quantize(Decimal(q)))

    md = [f"# {a.round_label} evaluation ({'+'.join(sorted(roles))}, pooled over slices; rules pre-registered)", "",
          "| experiment | config | verdict | net base TMN | net adverse TMN | net low TMN | trades | PF | +slices | worst slice % | fee share | vs CTRL TMN | failed rules |",
          "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in sorted(results, key=lambda r: -r["base"]["net_tmn"]):
        b = r["base"]
        md.append(f"| {r['experiment']} | {r['config']} | {r['verdict']} | {fmt(b['net_tmn'])} | {fmt(r['adverse']['net_tmn']) if r['adverse'] else '—'} | "
                  f"{fmt(r['low']['net_tmn']) if r['low'] else '—'} | {b['trades']} | {fmt(b['profit_factor'], '0.01')} | {b['positive_slices']}/5 | "
                  f"{fmt(b['worst_slice_pct'], '0.01')} | {fmt(b['fee_share'], '0.01')} | {fmt(r['control_net_tmn'])} | {'; '.join(x.split(' ')[0] for x in r['reasons']) or '—'} |")
    md += ["", "Control (drift benchmark):", "", "| config | cost | net TMN | trades | PF | +slices | worst slice % | fees TMN |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    md += [f"| {c['config']} | {c['cost']} | {fmt(c['net_tmn'])} | {c['trades']} | {fmt(c['profit_factor'], '0.01')} | {c['positive_slices']}/5 | "
           f"{fmt(c['worst_slice_pct'], '0.01')} | {fmt(c['fees_tmn'])} |" for c in control_rows]
    md += ["", "Buy & hold (equal-weight, 100% invested, no costs; benchmark only):", "", "| slice | role | BTCTMN % | ETHTMN % | EW % |", "|---|---|---:|---:|---:|"]
    md += [f"| {n} | {v['role']} | {v['BTCTMN']} | {v['ETHTMN']} | {v['equal_weight_pct']} |" for n, v in bh.items()]
    (root / f"{a.round_label}_EVALUATION.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
