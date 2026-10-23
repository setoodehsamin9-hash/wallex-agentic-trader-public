"""Pre-registered backtest study (Phase 03). Every trial is recorded; nothing is dropped.

Protocol (fixed before running):
  data      data/datasets/wallex-1m-btctmn-ethtmn (45 days, BTCTMN+ETHTMN, 1m, hash-pinned)
  splits    time-ordered 60/20/20: TRAIN / VALIDATION / TEST (TEST is read once, for the chosen setting only)
  trials    6 C1 settings (below), decided by B0 (deterministic selector) on TRAIN and VALIDATION
  selection highest VALIDATION realized return; tie -> lower max drawdown
  test      chosen setting on TEST with B0, then with the LLM agent via NineRouter (ablation: same data/costs)
  stress    chosen setting on TEST with fee x2 and slippage x2 (B0)

Usage:
  uv run python scripts/backtest_study.py [--llm-model av/claude-sonnet-5-5] [--skip-llm]
Outputs: reports/phase-03/backtest/TRIALS.jsonl, STUDY.json, and per-run exports under runs/published/.
"""

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from wallex_agent.agent.baseline import BaselineSelector  # noqa: E402
from wallex_agent.journal.export import export_run  # noqa: E402
from wallex_agent.journal.store import JournalStore  # noqa: E402
from wallex_agent.market.dataset import load_dataset  # noqa: E402
from wallex_agent.runtime.backtest import run_backtest  # noqa: E402
from wallex_agent.runtime.config import RunConfig, load_run_config  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "reports" / "phase-03" / "backtest"
TRIALS: list[dict[str, Any]] = [
    {"id": "T1", "bar": 900, "c1": {"lookback_bars": 16, "stop_atr": "1.5", "tp_atr": "3.0"}},
    {"id": "T2", "bar": 900, "c1": {"lookback_bars": 24, "stop_atr": "1.5", "tp_atr": "3.0"}},
    {"id": "T3", "bar": 900, "c1": {"lookback_bars": 16, "stop_atr": "2.0", "tp_atr": "4.0"}},
    {"id": "T4", "bar": 900, "c1": {"lookback_bars": 16, "stop_atr": "1.5", "tp_atr": "3.0", "min_volume_ratio": "1.5"}},
    {"id": "T5", "bar": 1800, "c1": {"lookback_bars": 16, "stop_atr": "1.5", "tp_atr": "3.0"}},
    {"id": "T6", "bar": 1800, "c1": {"lookback_bars": 12, "stop_atr": "2.0", "tp_atr": "4.0"}},
]


def variant(base: RunConfig, trial: dict[str, Any], *, split: str, fee_mult: int = 1, slip_mult: int = 1,
            decider: str = "BASELINE", model: str | None = None) -> RunConfig:
    d = base.model_dump(mode="python")
    d["run"]["bar_interval_seconds"] = trial["bar"]
    d["limits"]["data"]["max_bar_age_s"] = 2 * trial["bar"] + 60
    d["strategy"]["c1"].update(trial["c1"])
    d["strategy"]["c1"]["version"] = f"c1-0.1.0+{trial['id']}"
    d["backtest"]["split"] = split
    d["costs"]["fee_rate"] = base.costs.fee_rate * fee_mult
    d["costs"]["backtest_slippage_bps"] = base.costs.backtest_slippage_bps * slip_mult
    d["agent"]["decision_provider"] = decider
    if model:
        d["agent"]["model_id"] = model
    d["run"]["label"] = f"study-{trial['id']}-{split.lower()}-{decider.lower()}" + (f"-stress{fee_mult}x" if fee_mult > 1 else "")
    return RunConfig.model_validate(d)


def one(cfg: RunConfig, store: JournalStore, ds: Any, *, export: bool) -> dict[str, Any]:
    if cfg.agent.decision_provider == "LLM":
        from wallex_agent.runtime.compose import build_decider

        decider, meta = build_decider(cfg, store)
    else:
        decider, meta = BaselineSelector(), {"route": "B0 deterministic selector", "provider": None, "model_id": None}
    rid = cfg.run.label
    res = run_backtest(cfg, ds, store, decider, run_id=rid, model_meta=meta)
    s = res.summary
    row = {"run_id": rid, "trial": cfg.strategy.c1.version if cfg.strategy else None, "split": cfg.backtest.split if cfg.backtest else None,
           "decider": cfg.agent.decision_provider, "model": meta.get("model_id"), "period": [p.isoformat() for p in res.period],
           "fee_rate": str(cfg.costs.fee_rate), "slippage_bps": str(cfg.costs.backtest_slippage_bps), "trades": s.trades,
           "wins": s.wins, "realized_pnl_tmn": str(s.realized_pnl_tmn), "realized_return_pct": str(s.realized_return_pct),
           "fees_tmn": str(s.fees_tmn), "max_drawdown_pct": str(s.max_drawdown_pct), "candidate_cycles": s.wait_decisions
           + s.entries_submitted + s.risk_rejected + s.proposed, "ai_calls": s.ai_calls, "ai_valid": s.ai_valid,
           "wait_decisions": s.wait_decisions, "risk_rejected": s.risk_rejected, "top_risk_blocks": s.top_risk_blocks,
           "ai_latency_ms_p50": s.ai_latency_ms_p50, "ai_latency_ms_p95": s.ai_latency_ms_p95}
    if export:
        export_run(store, rid, REPO / "runs" / "published" / rid, summary=s, context={**res.context, "model_route": meta.get("route")})
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm-model", default=os.environ.get("NINEROUTER_MODEL", "av/claude-sonnet-5-5"))
    ap.add_argument("--skip-llm", action="store_true")
    a = ap.parse_args()
    base = load_run_config(REPO / "config" / "backtest.toml")
    ds = load_dataset(REPO / "data" / "datasets" / base.backtest.dataset)  # type: ignore[union-attr]
    OUT.mkdir(parents=True, exist_ok=True)
    store = JournalStore(REPO / "var" / "study.sqlite")
    rows: list[dict[str, Any]] = []
    with (OUT / "TRIALS.jsonl").open("w") as fh:
        def log(row: dict[str, Any]) -> None:
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(json.dumps({k: row[k] for k in ("run_id", "trades", "realized_return_pct", "max_drawdown_pct")}), flush=True)

        for t in TRIALS:
            for split in ("TRAIN", "VALIDATION"):
                log(one(variant(base, t, split=split), store, ds, export=False))
        val = [r for r in rows if r["split"] == "VALIDATION"]
        best = max(val, key=lambda r: (Decimal(r["realized_return_pct"]), -Decimal(r["max_drawdown_pct"])))
        chosen = next(t for t in TRIALS if best["trial"].endswith(t["id"]))
        log(one(variant(base, chosen, split="TEST"), store, ds, export=True))
        log(one(variant(base, chosen, split="TEST", fee_mult=2, slip_mult=2), store, ds, export=False))
        if not a.skip_llm:
            log(one(variant(base, chosen, split="TEST", decider="LLM", model=a.llm_model), store, ds, export=True))
    study = {"protocol": __doc__, "chosen_trial": chosen["id"], "selection_row": best, "rows": rows}
    (OUT / "STUDY.json").write_text(json.dumps(study, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
