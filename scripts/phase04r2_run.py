"""Phase 04 round 2 — pre-registered experiments in the research simulator (RESEARCH ONLY; TEST locked).

  uv run python scripts/phase04r2_run.py gate    # economics gate (TRAIN, gate configs)        -> round2/GATE.json
  uv run python scripts/phase04r2_run.py train   # TRAIN grids + RAND-X + selection S1-S5      -> round2/TRAIN.json/.md
  uv run python scripts/phase04r2_run.py val     # frozen configs on VALIDATION, gates G1-G9   -> round2/VAL.json/.md
  uv run python scripts/phase04r2_run.py test    # ONCE, only if G1-G9 and G10 passed          -> round2/TEST.json/.md

Rules, grids, splits and costs: reports/phase-04/round2/PREREGISTRATION_R2.md (committed before results).
Deterministic: fixed seeds, sorted iteration, canonical JSON; each output prints its SHA-256.
"""

import itertools
import json
import random
import statistics
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phase04r2_lib import (  # noqa: E402
    COSTS, HOUR, REPO, TEST_UNLOCK, Arr, Costs, ExitRule, Plan, Series, SimResult, aggregate_complete, atr, buy_hold, dump, ema,
    load_series, max_drawdown, pct_rank, portfolio, prior_high, random_plan, regime_labels, rolling_std, simulate, sma,
    trade_stats, ts)

OUT = REPO / "reports" / "phase-04" / "round2"
PRIMARY = ["BTCTMN", "ETHTMN"]
SECONDARY = ["BNBTMN", "XAUTTMN"]
ALLOWED = {"TREND_UP", "RANGE"}
SLICES: dict[str, list[tuple[str, int, int]]] = {
    "TRAIN": [("T1", ts("2023-02-01"), ts("2023-07-01")), ("T2", ts("2023-07-01"), ts("2023-12-01")),
              ("T3", ts("2023-12-01"), ts("2024-05-01")), ("T4", ts("2024-05-01"), ts("2024-10-01")),
              ("T5", ts("2024-10-01"), ts("2025-03-01"))],
    "VAL": [("V1", ts("2025-03-01"), ts("2025-08-01")), ("V2", ts("2025-08-01"), ts("2026-01-01")),
            ("V3", ts("2026-01-01"), ts("2026-06-01")), ("V4", ts("2026-06-01"), ts("2026-07-24")),
            ("V5", ts("2026-07-24"), ts("2026-08-20")), ("V6", ts("2026-08-20"), ts("2026-09-16")),
            ("V7", ts("2026-09-16"), ts("2026-10-13"))],
    "TEST": [("X", ts("2026-10-13"), ts("2026-10-22"))],
}
GRID: dict[str, list[int]] = {"F1": [24, 72], "F2": [48, 168], "F3": [20, 40]}  # F3 p = squeeze rank q x 100
GATE_CONFIGS = ["F1|4|24|3|0", "F2|4|48|3|0", "F3|4|20|3|0", "F4|4|48|3|0"]


@dataclass(frozen=True)
class Cfg:
    family: str  # F1 DCB, F2 TSM, F3 VBO, F4 MRR, C2 (v0.2 baseline), CTRL (v0.2 drift control)
    tf: int  # decision timeframe in hours (1 or 4)
    p: int  # family parameter: N (F1), L (F2), q x 100 (F3), cap hours (F4), trend bars (C2/CTRL)
    m: float  # ATR multiple: initial stop and chandelier trail
    regime: bool

    @property
    def key(self) -> str:
        return f"{self.family}|{self.tf}|{self.p}|{self.m:g}|{int(self.regime)}"

    def rule(self) -> ExitRule:
        if self.family in ("C2", "CTRL"):
            return ExitRule(stop_atr=3.0, trail_atr=None, max_hold_s=48 * HOUR)
        if self.family == "F4":
            return ExitRule(stop_atr=self.m, trail_atr=None, max_hold_s=self.p * HOUR, use_exit_signal=True)
        return ExitRule(stop_atr=self.m, trail_atr=self.m, max_hold_s=168 * HOUR, use_exit_signal=self.family == "F2")


def parse(key: str) -> Cfg:
    f, tf, p, m, r = key.split("|")
    return Cfg(f, int(tf), int(p), float(m), r == "1")


def grid(family: str) -> list[Cfg]:
    return [Cfg(family, tf, p, m, rg) for tf, p, m, rg in itertools.product([1, 4], GRID[family], [3.0, 5.0], [False, True])]


def neighbours(c: Cfg) -> list[Cfg]:
    ps = GRID[c.family]
    return [replace(c, tf=5 - c.tf), replace(c, p=ps[1 - ps.index(c.p)]), replace(c, m=8.0 - c.m),
            replace(c, regime=not c.regime)]


class Market:
    """One symbol: native 1h execution series + decision series per timeframe + regime per 1h index (causal)."""

    def __init__(self, symbol: str, *, allow_test: bool = False) -> None:
        self.h1: Series = load_series("1h", symbol, 3600, allow_test=allow_test)
        n = len(self.h1.t)
        self.dec: dict[int, tuple[Series, list[int]]] = {1: (self.h1, list(range(n))), 4: aggregate_complete(self.h1, 4)}
        s4, last4 = self.dec[4]
        labels = regime_labels(s4)
        self.reg: list[str] = ["UNKNOWN"] * n
        k = 0
        cur = "UNKNOWN"
        for i in range(n):
            while k < len(last4) and last4[k] <= i:
                cur = labels[k]
                k += 1
            self.reg[i] = cur
        self._plans: dict[tuple[str, int, int, bool, str], Plan] = {}

    def plan(self, c: Cfg, costs: Costs) -> Plan:
        ck = (c.family, c.tf, c.p, c.regime, costs.name if c.family in ("C2", "CTRL") else "")
        if ck in self._plans:
            return self._plans[ck]
        d, last = self.dec[c.tf]
        a = atr(d, 14)
        cl = d.c
        entry: dict[int, float] = {}
        allowed: dict[int, float] = {}
        exit_sig: set[int] = set()
        sig: list[bool] = [False] * len(cl)
        ex: list[bool] = [False] * len(cl)
        if c.family == "F1":
            ph = prior_high(d.h, c.p)
            sig = [ph[k] is not None and cl[k] > (ph[k] or 0.0) for k in range(len(cl))]
        elif c.family == "F2":
            e = ema(cl, c.p)
            for k in range(c.p, len(cl)):
                ek, ak = e[k], a[k]
                if ek is not None and ak is not None:
                    sig[k] = cl[k] >= ek + 0.5 * ak and cl[k] > cl[k - c.p]
                    ex[k] = cl[k] < ek
        elif c.family in ("F3", "F4"):
            mid, sd = sma(cl, 24), rolling_std(cl, 24)
            if c.family == "F3":
                bbw: Arr = [4 * s / m if s is not None and m else None for s, m in zip(sd, mid)]
                rk = pct_rank(bbw, 180)
                for k in range(6, len(cl)):
                    w = [x for x in rk[k - 5:k + 1] if x is not None]
                    mk, sk = mid[k], sd[k]
                    if w and mk is not None and sk is not None:
                        sig[k] = min(w) <= c.p / 100 and cl[k] > mk + 2 * sk
            else:
                for k in range(len(cl)):
                    mk, sk = mid[k], sd[k]
                    if mk is not None and sk is not None:
                        sig[k] = self.reg[last[k]] == "RANGE" and cl[k] < mk - 2 * sk
                        ex[k] = cl[k] >= mk
        else:  # C2 v0.2 / CTRL v0.2 replica: kernel features (SMA24, return over closed[-24]) + cost-awareness gate
            sm = sma(cl, c.p)
            for k in range(c.p, len(cl)):
                sk, ak = sm[k], a[k]
                if sk is None or ak is None or ak / cl[k] * 10000 < costs.round_trip_bps:
                    continue
                sig[k] = c.family == "CTRL" or (cl[k] >= sk + 0.5 * ak and cl[k] > cl[k - c.p + 1])
        for k in range(len(cl)):
            ak = a[k]
            if ak is None:
                continue
            i = last[k]
            allowed[i] = ak
            if sig[k] and (not c.regime or self.reg[i] in ALLOWED):
                entry[i] = ak
            if ex[k]:
                exit_sig.add(i)
        p = Plan(entry=entry, allowed=allowed, exit_signal=exit_sig, regime={i: self.reg[i] for i in entry})
        self._plans[ck] = p
        return p

    def regime_pool(self, c: Cfg, costs: Costs) -> Plan:
        p = self.plan(c, costs)
        return Plan(entry=p.entry, allowed={i: a for i, a in p.allowed.items() if self.reg[i] in ALLOWED},
                    exit_signal=p.exit_signal, regime=p.regime)


def run(c: Cfg, mk: dict[str, Market], role: str, costs: Costs, symbols: list[str],
        plan_override: dict[str, Plan] | None = None) -> dict[str, Any]:
    slices: list[dict[str, Any]] = []
    trades_all: list[Any] = []
    per_sym = {s: 0.0 for s in symbols}
    exp_h, hours = 0, 0
    for name, a, b in SLICES[role]:
        res: list[SimResult] = []
        for s in symbols:
            plan = plan_override[s] if plan_override else mk[s].plan(c, costs)
            r = simulate(mk[s].h1, plan, c.rule(), costs, a, b)
            if r.hours == 0:
                continue
            res.append(r)
            per_sym[s] += r.equity[-1][1] - 1 if r.equity else 0.0
            exp_h += r.exposure_hours
            hours += r.hours
            trades_all.extend(r.trades)
        net, dd = portfolio(res)
        slices.append({"slice": name, "net": net, "maxdd": dd, "trades": sum(len(r.trades) for r in res)})
    st = trade_stats(trades_all, costs)
    nets = [x["net"] for x in slices]
    return {"key": c.key, "cost": costs.name, "pooled_net": sum(nets), "pos_slices": sum(1 for x in nets if x > 0),
            "n_slices": len(nets), "worst_slice": min(nets) if nets else 0.0,
            "worst_dd": max((x["maxdd"] for x in slices), default=0.0), "exposure": exp_h / hours if hours else 0.0,
            "per_symbol": per_sym, "slices": slices, **st,
            "exit_mix": {k: sum(1 for t in trades_all if t.reason == k) for k in ("STOP", "TIME", "SIGNAL", "END")}}


def rand_control(c: Cfg, mk: dict[str, Market], role: str, costs: Costs, symbols: list[str], target: dict[str, Any],
                 seeds: int, regime_pool: bool = False) -> dict[str, Any]:
    """Rate calibrated so the random path trades about as often as the strategy (same exits, same ATR)."""
    pools = {s: (mk[s].regime_pool(c, costs) if regime_pool else mk[s].plan(c, costs)) for s in symbols}
    n_pool = sum(1 for s in symbols for _, a, b in SLICES[role] for i in pools[s].allowed
                 if a <= mk[s].h1.t[i] and mk[s].h1.t[i] + HOUR <= b)
    flat = max(0.05, 1 - target["exposure"])
    rate = min(1.0, target["trades"] / max(1.0, n_pool * flat)) if target["trades"] else 0.0
    nets: list[float] = []
    trades: list[int] = []
    for seed in range(1, seeds + 1):
        rng = random.Random(seed * 7919 + len(c.key))
        plans = {s: random_plan(pools[s], rate, rng) for s in symbols}
        r = run(c, mk, role, costs, symbols, plan_override=plans)
        nets.append(r["pooled_net"])
        trades.append(r["trades"])
    nets_sorted = sorted(nets)
    p = (1 + sum(1 for x in nets if x >= target["pooled_net"])) / (1 + len(nets))
    return {"seeds": seeds, "rate": rate, "median": statistics.median(nets), "p90": nets_sorted[int(0.9 * len(nets)) - 1],
            "mean": statistics.fmean(nets), "p_value": p, "mean_trades": statistics.fmean(trades),
            "regime_pool": regime_pool}


def bh(mk: dict[str, Market], role: str, costs: Costs | None, symbols: list[str]) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    for name, a, b in SLICES[role]:
        rs = [x for s in symbols if (x := buy_hold(mk[s].h1, a, b, costs)) is not None]
        dds: list[float] = []
        for s in symbols:
            h = mk[s].h1
            cs = [cc for t, cc in zip(h.t, h.c) if a <= t and t + HOUR <= b]
            if cs:
                dds.append(max_drawdown(cs))
        out.append({"slice": name, "net": statistics.fmean(rs) if rs else 0.0, "maxdd": max(dds) if dds else 0.0})
    return {"pooled_net": sum(x["net"] for x in out), "worst_dd": max(x["maxdd"] for x in out), "slices": out}


def markets(symbols: list[str]) -> dict[str, Market]:
    return {s: Market(s) for s in symbols}


def pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


# --------------------------------------------------------------------------------------------------- stages
def stage_gate() -> None:
    mk = markets(PRIMARY)
    base = COSTS["base"]
    rows: list[dict[str, Any]] = []
    for key in GATE_CONFIGS:
        c = parse(key)
        r = run(c, mk, "TRAIN", base, PRIMARY)
        gross_pre = r["gross_bps"] + 2 * base.slip_bps
        ok = r["trades"] >= 20 and gross_pre >= 1.5 * base.round_trip_bps
        rows.append({"config": key, "trades": r["trades"], "gross_pre_cost_bps": gross_pre, "net_bps": r["net_bps"],
                     "round_trip_bps": base.round_trip_bps, "gross_over_cost": gross_pre / base.round_trip_bps,
                     "median_hold_h": r["median_hold_h"], "exit_mix": r["exit_mix"], "pass": ok})
        print(f"{key:14s} trades={r['trades']:4d} gross_pre={gross_pre:7.1f}bps ratio={gross_pre / base.round_trip_bps:5.2f}"
              f" net/trade={r['net_bps']:7.1f} hold_med={r['median_hold_h']:.0f}h exits={r['exit_mix']} -> "
              f"{'PASS' if ok else 'REJECT'}")
    print("sha256", dump(OUT / "GATE.json", {"rule": "gross pre-cost >= 1.5 x 60 bps and >= 20 trades", "rows": rows}))


def stage_train() -> None:
    gate = json.loads((OUT / "GATE.json").read_text())
    fams = [r["config"].split("|")[0] for r in gate["rows"] if r["pass"] and r["config"][:2] in GRID]
    mk = markets(PRIMARY + SECONDARY)
    base = COSTS["base"]
    results: dict[str, Any] = {}
    for fam in fams:
        for c in grid(fam):
            r = run(c, mk, "TRAIN", base, PRIMARY)
            r["rand_x"] = rand_control(c, mk, "TRAIN", base, PRIMARY, r, seeds=30)
            r["adverse_net"] = run(c, mk, "TRAIN", COSTS["adverse"], PRIMARY)["pooled_net"]
            r["secondary"] = {s: run(c, mk, "TRAIN", base, [s])["pooled_net"] for s in SECONDARY}
            results[c.key] = r
            print(f"{c.key:16s} net={pct(r['pooled_net'])} adv={pct(r['adverse_net'])} tr={r['trades']:4d}"
                  f" +sl={r['pos_slices']}/5 pf={r['pf']:.2f} net/tr={r['net_bps']:6.1f} exp={r['exposure']:.2f}"
                  f" randmed={pct(r['rand_x']['median'])} p={r['rand_x']['p_value']:.2f}")
    baselines: dict[str, Any] = {}
    for key in ("C2|1|24|3|0", "CTRL|1|24|3|0"):
        c = parse(key)
        r = run(c, mk, "TRAIN", base, PRIMARY)
        r["adverse_net"] = run(c, mk, "TRAIN", COSTS["adverse"], PRIMARY)["pooled_net"]
        baselines[key] = r
        print(f"{key:16s} net={pct(r['pooled_net'])} adv={pct(r['adverse_net'])} tr={r['trades']}")
    selection: dict[str, Any] = {}
    for fam in fams:
        cand: list[tuple[float, int, int, int, str]] = []
        for c in grid(fam):
            r = results[c.key]
            nb = statistics.fmean(results[n.key]["pooled_net"] for n in neighbours(c))
            checks = {"S1": r["pooled_net"] > 0, "S2": r["pos_slices"] >= 3, "S3": r["trades"] >= 40,
                      "S4": r["pooled_net"] > r["rand_x"]["median"], "S5": nb > 0}
            r["selection_checks"], r["neighbour_mean"] = checks, nb
            if all(checks.values()):
                cand.append((-(r["pooled_net"] - r["rand_x"]["median"]), r["trades"], int(c.regime), -c.tf, c.key))
        cand.sort()
        selection[fam] = {"frozen": cand[0][4] if cand else None, "passing": [x[4] for x in cand]}
        print(fam, "->", selection[fam])
    bh_train = bh(mk, "TRAIN", base, PRIMARY)
    payload = {"families": fams, "results": results, "baselines": baselines, "buy_hold": bh_train, "selection": selection}
    print("sha256", dump(OUT / "TRAIN.json", payload))
    write_md("TRAIN", payload)


def stage_val() -> None:
    train = json.loads((OUT / "TRAIN.json").read_text())
    mk = markets(PRIMARY + SECONDARY)
    base, adv = COSTS["base"], COSTS["adverse"]
    frozen = [v["frozen"] for v in train["selection"].values() if v["frozen"]]
    info_keys = sorted({c.key for fam, v in train["selection"].items() if v["frozen"] for c in grid(fam)})
    results: dict[str, Any] = {}
    bh_base = bh(mk, "VAL", base, PRIMARY)
    for key in info_keys:
        c = parse(key)
        r = run(c, mk, "VAL", base, PRIMARY)
        r["adverse_net"] = run(c, mk, "VAL", adv, PRIMARY)["pooled_net"]
        r["low_net"] = run(c, mk, "VAL", COSTS["low"], PRIMARY)["pooled_net"]
        r["secondary"] = {s: run(c, mk, "VAL", base, [s])["pooled_net"] for s in SECONDARY}
        results[key] = r
    gates: dict[str, Any] = {}
    for key in frozen + [k for f in frozen for k in [neighbours(parse(f))[3].key]]:
        c = parse(key)
        r = results[key]
        r["rand_x"] = rand_control(c, mk, "VAL", base, PRIMARY, r, seeds=300)
        ra = run(c, mk, "VAL", adv, PRIMARY)
        r["rand_x_adverse"] = rand_control(c, mk, "VAL", adv, PRIMARY, ra, seeds=300)
        if c.regime:
            r["rand_r"] = rand_control(c, mk, "VAL", base, PRIMARY, r, seeds=300, regime_pool=True)
        r["episodes_3d"] = episodes(c, mk, base)
        nb = statistics.fmean(results[n.key]["pooled_net"] for n in neighbours(c))
        dd_ok = r["worst_dd"] <= 0.20 and r["worst_dd"] <= bh_base["worst_dd"]
        calmar = r["pooled_net"] / r["worst_dd"] if r["worst_dd"] > 0 else float("inf")
        bh_calmar = bh_base["pooled_net"] / bh_base["worst_dd"] if bh_base["worst_dd"] > 0 else float("inf")
        g = {"G1": r["pooled_net"] > 0, "G2": r["adverse_net"] >= 0, "G3": r["trades"] >= 20,
             "G4": r["pos_slices"] >= 4 and r["worst_slice"] > -0.08, "G5": dd_ok,
             "G6": r["rand_x"]["p_value"] <= 0.10 and r["adverse_net"] > r["rand_x_adverse"]["median"],
             "G7": r["pooled_net"] >= bh_base["pooled_net"] or calmar >= bh_calmar, "G8": nb > 0,
             "G9": r["per_symbol"]["BTCTMN"] > 0 and r["per_symbol"]["ETHTMN"] > 0}
        gates[key] = {"frozen": key in frozen, "checks": g, "pass_G1_G9": all(g.values()), "neighbour_mean": nb,
                      "calmar": calmar, "bh_calmar": bh_calmar}
        print(key, "frozen" if key in frozen else "twin", {k: int(v) for k, v in g.items()}, "p=",
              round(r["rand_x"]["p_value"], 3), "net", pct(r["pooled_net"]), "randmed", pct(r["rand_x"]["median"]))
    baselines: dict[str, Any] = {}
    for key in ("C2|1|24|3|0", "CTRL|1|24|3|0"):
        c = parse(key)
        r = run(c, mk, "VAL", base, PRIMARY)
        r["adverse_net"] = run(c, mk, "VAL", adv, PRIMARY)["pooled_net"]
        r["vb_slices"] = [x for x in r["slices"] if x["slice"] in ("V5", "V6", "V7")]
        baselines[key] = r
    payload = {"frozen": frozen, "results": results, "gates": gates, "baselines": baselines, "buy_hold": bh_base,
               "buy_hold_adverse": bh(mk, "VAL", adv, PRIMARY), "buy_hold_nocost": bh(mk, "VAL", None, PRIMARY)}
    print("sha256", dump(OUT / "VAL.json", payload))
    write_md("VAL", payload)


def stage_test() -> None:
    """The single pre-registered TEST read (PREREGISTRATION_R2.md section 8): frozen F2 and its regime twin, base and
    adverse, with RAND-X (300 seeds) and buy & hold. Refuses unless G1-G9 (VAL.json) and G10 (G10.json) passed."""
    val = json.loads((OUT / "VAL.json").read_text())
    g10 = json.loads((OUT / "G10.json").read_text())
    frozen = g10["frozen"]
    if not (val["gates"][frozen]["pass_G1_G9"] and g10["verdict"]["pass"]):
        raise SystemExit("TEST stays locked: the frozen configuration has not passed G1-G10")
    if (OUT / "TEST.json").exists():
        raise SystemExit("TEST was already evaluated once; refusing a second read")
    TEST_UNLOCK["enabled"] = True
    mk = {s: Market(s, allow_test=True) for s in PRIMARY + SECONDARY}
    base, adv = COSTS["base"], COSTS["adverse"]
    results: dict[str, Any] = {}
    for key in (frozen, neighbours(parse(frozen))[3].key):
        c = parse(key)
        r = run(c, mk, "TEST", base, PRIMARY)
        r["adverse_net"] = run(c, mk, "TEST", adv, PRIMARY)["pooled_net"]
        r["secondary"] = {s: run(c, mk, "TEST", base, [s])["pooled_net"] for s in SECONDARY}
        r["rand_x"] = rand_control(c, mk, "TEST", base, PRIMARY, r, seeds=300)
        r["keep"] = r["pooled_net"] > 0 and r["adverse_net"] >= 0 and r["pooled_net"] > r["rand_x"]["median"]
        results[key] = r
        print(key, "net", pct(r["pooled_net"]), "adv", pct(r["adverse_net"]), "trades", r["trades"], "randmed",
              pct(r["rand_x"]["median"]), "p", round(r["rand_x"]["p_value"], 3), "keep", r["keep"])
    payload = {"frozen": frozen, "results": results, "baselines": {}, "buy_hold": bh(mk, "TEST", base, PRIMARY),
               "buy_hold_adverse": bh(mk, "TEST", adv, PRIMARY)}
    print("B&H", pct(payload["buy_hold"]["pooled_net"]))
    print("sha256", dump(OUT / "TEST.json", payload))
    write_md("TEST", payload)


def episodes(c: Cfg, mk: dict[str, Market], costs: Costs) -> dict[str, Any]:
    """Non-overlapping 72 h episodes inside each VAL slice: start flat, flatten at end (challenge-like)."""
    eps: list[tuple[int, int]] = []
    for _, a, b in SLICES["VAL"]:
        t = a
        while t + 72 * HOUR <= b:
            eps.append((t, t + 72 * HOUR))
            t += 72 * HOUR
    strat: list[float] = []
    bhs: list[float] = []
    for a, b in eps:
        res = [simulate(mk[s].h1, mk[s].plan(c, costs), c.rule(), costs, a, b) for s in PRIMARY]
        strat.append(portfolio([r for r in res if r.hours])[0])
        bhs.append(statistics.fmean(x for s in PRIMARY if (x := buy_hold(mk[s].h1, a, b, costs)) is not None))
    return {"n": len(eps), "share_positive": sum(1 for x in strat if x > 0) / len(strat), "mean": statistics.fmean(strat),
            "worst": min(strat), "share_flat": sum(1 for x in strat if x == 0) / len(strat),
            "bh_share_positive": sum(1 for x in bhs if x > 0) / len(bhs), "bh_mean": statistics.fmean(bhs),
            "bh_worst": min(bhs)}


def write_md(role: str, p: dict[str, Any]) -> None:
    lines = [f"# Round 2 {role} (research simulator; BTC+ETH equal weight; base cost unless stated)", "",
             "| config | net | adverse | trades | +slices | worst slice | worst DD | PF | net bps/tr | exposure | "
             "RAND-X median | p | BNB | XAUT |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for key, r in sorted(p["results"].items()):
        rx = r.get("rand_x")
        lines.append(f"| {key} | {pct(r['pooled_net'])} | {pct(r['adverse_net'])} | {r['trades']} | "
                     f"{r['pos_slices']}/{r['n_slices']} | {pct(r['worst_slice'])} | {r['worst_dd'] * 100:.1f}% | "
                     f"{r['pf']:.2f} | {r['net_bps']:.1f} | {r['exposure']:.2f} | "
                     f"{pct(rx['median']) if rx else '—'} | {f'{rx['p_value']:.2f}' if rx else '—'} | "
                     f"{pct(r['secondary']['BNBTMN'])} | {pct(r['secondary']['XAUTTMN'])} |")
    lines += ["", "Baselines:", ""]
    for key, r in p["baselines"].items():
        lines.append(f"- {key}: net {pct(r['pooled_net'])}, adverse {pct(r['adverse_net'])}, trades {r['trades']}, "
                     f"PF {r['pf']:.2f}, net bps/trade {r['net_bps']:.1f}, exposure {r['exposure']:.2f}")
    b = p["buy_hold"]
    lines += [f"- Buy & hold (one round trip, base): pooled {pct(b['pooled_net'])}, worst-slice DD {b['worst_dd'] * 100:.1f}%; "
              + ", ".join(f"{x['slice']} {pct(x['net'])}" for x in b["slices"])]
    if "selection" in p:
        lines += ["", "Selection (S1–S5):", ""] + [f"- {f}: frozen **{v['frozen']}**; passing {v['passing']}"
                                                   for f, v in p["selection"].items()]
    if "gates" in p:
        lines += ["", "Gates (G1–G9):", ""] + [f"- {k} ({'frozen' if g['frozen'] else 'regime twin'}): "
                                               + " ".join(f"{c}={'✔' if ok else '✘'}" for c, ok in g["checks"].items())
                                               + f" → {'PASS' if g['pass_G1_G9'] else 'FAIL'}" for k, g in p["gates"].items()]
    (OUT / f"{role}.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    {"gate": stage_gate, "train": stage_train, "val": stage_val, "test": stage_test}[sys.argv[1]]()
