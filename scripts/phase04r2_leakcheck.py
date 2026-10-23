"""Phase 04 round 2 — post-hoc leakage diagnostics for the frozen configurations (information, NOT a gate).

  uv run python scripts/phase04r2_leakcheck.py   ->  reports/phase-04/round2/LEAKCHECK.json

1. Delay test: every entry signal is acted on one decision bar (4 h) LATER. A genuine trend effect degrades gently;
   a look-ahead leak collapses. 2. Hand-verifiable trade trace: the first VALIDATION trades with the 4h bars that
   produced them (signal close, prior-window high / EMA, next 1h open used for the fill).
"""

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase04r2_lib import COSTS, HOUR, Plan, dump, ema, iso, prior_high  # noqa: E402
from phase04r2_run import OUT, PRIMARY, Market, parse, run  # noqa: E402

FROZEN = ["F1|4|24|5|0", "F2|4|48|5|1"]


def delayed(p: Plan, step_h: int, mk: Market) -> Plan:
    pos = {t: i for i, t in enumerate(mk.h1.t)}
    entry = {pos[mk.h1.t[i] + step_h * HOUR]: a for i, a in p.entry.items() if mk.h1.t[i] + step_h * HOUR in pos}
    return Plan(entry=entry, allowed=p.allowed, exit_signal=p.exit_signal, regime=p.regime)


def main() -> None:
    base = COSTS["base"]
    mk = {s: Market(s) for s in PRIMARY}
    out: dict[str, Any] = {"delay": {}, "trace": {}}
    for key in FROZEN:
        c = parse(key)
        row: dict[str, Any] = {}
        for role in ("TRAIN", "VAL"):
            row[role] = {"as_registered": run(c, mk, role, base, PRIMARY)["pooled_net"]}
            for d in (4, 8):
                plans = {s: delayed(mk[s].plan(c, base), d, mk[s]) for s in PRIMARY}
                row[role][f"delay_{d}h"] = run(c, mk, role, base, PRIMARY, plan_override=plans)["pooled_net"]
        out["delay"][key] = row
        print(key, {r: {k: f"{v * 100:+.1f}%" for k, v in x.items()} for r, x in row.items()})
        m = mk["BTCTMN"]
        s4, last4 = m.dec[4]
        ph = prior_high(s4.h, c.p)
        em = ema(s4.c, c.p)
        plan = m.plan(c, base)
        rows: list[dict[str, Any]] = []
        for k, i in enumerate(last4):
            if i in plan.entry and m.h1.t[i] >= 1740787200 and len(rows) < 3:  # first VAL (2025-03-01) signals
                rows.append({"signal_4h_open": iso(s4.t[k]), "decision_at": iso(m.h1.t[i] + HOUR), "close": s4.c[k],
                             f"prior_{c.p}_high": ph[k], "ema": em[k], "regime": m.reg[i],
                             "fill_bar_open_utc": iso(m.h1.t[i + 1]), "fill_bar_open": m.h1.o[i + 1]})
        out["trace"][key] = rows
        for r in rows:
            print("   ", r)
    print("sha256", dump(OUT / "LEAKCHECK.json", out))


if __name__ == "__main__":
    main()
