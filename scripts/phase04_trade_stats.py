"""Trade-level statistics from experiment journals (TRAIN+VALIDATION only, one cost scenario).

  uv run python scripts/phase04_trade_stats.py EXP_ID [EXP_ID ...] [--cost base] [--root reports/phase-04/experiments]

Per configuration: trades by exit reason, mean price move per trade in bps (fill prices, i.e. after slippage, before
fees) split by exit reason, mean fee bps, mean holding hours, and the ATR-vs-cost gate effect is visible as trade
count. Writes <root>/TRADE_STATS_<cost>.json. Float statistics are research-only; nothing here trades.
"""

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("experiments", nargs="+")
    ap.add_argument("--cost", default="base")
    ap.add_argument("--root", default="reports/phase-04/experiments")
    ap.add_argument("--roles", nargs="+", default=["TRAIN", "VALIDATION"])
    a = ap.parse_args()
    root = REPO / a.root
    out: dict[str, Any] = {}
    for exp in a.experiments:
        by_cfg: dict[str, list[dict[str, Any]]] = defaultdict(list)
        names: dict[str, dict[str, Any]] = {}
        for line in (root / exp / "TRIALS.jsonl").read_text().splitlines():
            t = json.loads(line)
            if t["cost"]["name"] != a.cost or t["split"]["role"] not in a.roles:
                continue
            names[t["parameter_hash"]] = t["parameters"]
            db = sqlite3.connect(root / exp / "journals" / f"{t['trial_id']}.sqlite")
            for (body,) in db.execute("SELECT body FROM trades"):
                tr = json.loads(body)
                entry, exit_ = float(tr["avg_entry_price"]), float(tr["avg_exit_price"])
                notional = float(tr["qty"]) * entry
                held = (datetime.fromisoformat(tr["closed_at"]) - datetime.fromisoformat(tr["opened_at"])).total_seconds() / 3600
                by_cfg[t["parameter_hash"]].append({"move_bps": (exit_ / entry - 1) * 1e4, "fee_bps": float(tr["fees_tmn"]) / notional * 1e4,
                                                    "net_tmn": float(tr["net_pnl_tmn"]), "reason": tr["exit_reason"], "hours": held,
                                                    "symbol": tr["symbol"], "split": t["split"]["name"]})
            db.close()
        for h, trades in by_cfg.items():
            p = names[h]
            key = f"{exp} " + " ".join(f"{k}={v}" for k, v in p.items() if k in ("lookback_bars", "entry_trend_atr", "stop_atr", "max_holding_s"))
            reasons: dict[str, list[float]] = defaultdict(list)
            for tr in trades:
                reasons[tr["reason"]].append(tr["move_bps"])
            moves = [tr["move_bps"] for tr in trades]
            out[key] = {"trades": len(trades), "mean_move_bps": round(statistics.fmean(moves), 1) if moves else None,
                        "median_move_bps": round(statistics.median(moves), 1) if moves else None,
                        "mean_fee_bps": round(statistics.fmean(tr["fee_bps"] for tr in trades), 1) if trades else None,
                        "win_rate": round(sum(tr["net_tmn"] > 0 for tr in trades) / len(trades), 3) if trades else None,
                        "mean_hours": round(statistics.fmean(tr["hours"] for tr in trades), 1) if trades else None,
                        "by_exit": {r: {"n": len(v), "mean_move_bps": round(statistics.fmean(v), 1)} for r, v in sorted(reasons.items())},
                        "by_symbol": {s: round(sum(tr["net_tmn"] for tr in trades if tr["symbol"] == s)) for s in sorted({tr["symbol"] for tr in trades})}}
    path = root / f"TRADE_STATS_{a.cost}.json"
    path.write_text(json.dumps(out, indent=1) + "\n")
    for k, v in sorted(out.items(), key=lambda kv: -(kv[1]["mean_move_bps"] or -1e9)):
        print(k, "| n", v["trades"], "move", v["mean_move_bps"], "med", v["median_move_bps"], "fee", v["mean_fee_bps"], "win", v["win_rate"],
              "h", v["mean_hours"], "|", v["by_exit"], v["by_symbol"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
