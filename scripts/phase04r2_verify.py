"""Phase 04 round 2 — final verification helpers (no network, no Wallex, no model).

  uv run python scripts/phase04r2_verify.py secrets          # pattern secret scan over tracked files
  uv run python scripts/phase04r2_verify.py replay           # F2 backtest (1h base, V7) -> replay -> identical?
  uv run python scripts/phase04r2_verify.py determinism OUT  # re-run p04r2-g10-f2-off-1m into OUT, compare result hashes

Results are printed as JSON; the final gate collects them into reports/phase-04/round2/VERIFICATION_R2.json.
"""

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "private_key_block": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "api_key_assignment": r"(?i)(x-api-key|api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{24,}['\"]",
    "ninerouter_key_value": r"NINEROUTER_API_KEY\s*=\s*[A-Za-z0-9_\-]{12,}",
    "openai_style_key": r"\bsk-[A-Za-z0-9]{24,}\b",
    "github_token": r"\bgh[pousr]_[A-Za-z0-9]{36,}\b",
    "bearer_token": r"(?i)bearer\s+[A-Za-z0-9._\-]{30,}",
    "iran_iban": r"\bIR\d{24}\b",
    "card_number": r"\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b",  # grouped like a card; Luhn-checked below
}


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def secrets() -> dict[str, Any]:
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    findings: list[dict[str, Any]] = []
    scanned = 0
    for name in files:
        p = ROOT / name
        if not p.is_file() or p.stat().st_size > 5_000_000:
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        scanned += 1
        for pid, rx in PATTERNS.items():
            for m in re.finditer(rx, text):
                if pid == "card_number" and not luhn(re.sub(r"\D", "", m.group(0))):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                findings.append({"file": name, "line": line, "pattern": pid})
    return {"files": scanned, "patterns": len(PATTERNS), "findings": findings}


def replay() -> dict[str, Any]:
    from wallex_agent.agent.baseline import BaselineSelector
    from wallex_agent.agent.replay import ReplayDecisionProvider
    from wallex_agent.journal.store import JournalStore
    from wallex_agent.market.dataset import load_dataset
    from wallex_agent.runtime.backtest import run_backtest
    from wallex_agent.runtime.config import load_run_config

    cfg = load_run_config(ROOT / "config/experiments/phase04r2/base-4h-f2.toml")
    ds = load_dataset(ROOT / "data/raw/historical-pack/export/datasets/1h", expected_resolution_seconds=3600)
    period = (datetime(2026, 9, 16, tzinfo=UTC), datetime(2026, 10, 13, tzinfo=UTC))
    tmp = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "var" / "verify-r2"
    tmp.mkdir(parents=True, exist_ok=True)
    store = JournalStore(tmp / f"replay-{datetime.now(UTC):%Y%m%dT%H%M%S}.sqlite")
    run_backtest(cfg, ds, store, BaselineSelector(), run_id="r2-src", period=period)
    provider = ReplayDecisionProvider(store, "r2-src", exit_enabled=cfg.agent.allow_exit_position)
    run_backtest(cfg, ds, store, provider, run_id="r2-replay", period=period)
    orig, again = store.decisions("r2-src"), store.decisions("r2-replay")
    same = [(d.symbol, d.recorded_at, d.effective_action, d.status) for d in orig] == \
        [(d.symbol, d.recorded_at, d.effective_action, d.status) for d in again]
    trades_a = [t["net_pnl_tmn"] for t in store.trades("r2-src")]
    trades_b = [t["net_pnl_tmn"] for t in store.trades("r2-replay")]
    signals = [e for e in store.risk_events("r2-src") if e["kind"] == "EXIT_SIGNAL"]
    return {"run": "F2 f2-0.1.0, 1h base, V7 2026-09-16..10-13, BASELINE", "decisions": len(again), "identical_decisions": same,
            "trades": len(trades_a), "identical_trades": trades_a == trades_b, "missing_recordings": provider.missing,
            "exit_signal_events": len(signals)}


def determinism(out_root: Path) -> dict[str, Any]:
    exp = "p04r2-g10-f2-off-1m"
    subprocess.run(["uv", "run", "wallex-agent", "experiment", "run", f"config/experiments/phase04r2/{exp}.toml",
                    "--output-root", str(out_root)], cwd=ROOT, check=True, capture_output=True)

    def hashes(d: Path) -> list[str]:
        return [json.loads(x)["result_hash"] for x in (d / "TRIALS.jsonl").read_text().splitlines() if x.strip()]

    a = hashes(ROOT / "reports/phase-04/round2/experiments" / exp)
    b = hashes(out_root / exp)
    return {"experiment": exp, "trials": len(a), "identical_result_hashes": sum(1 for x, y in zip(a, b) if x == y),
            "complete": len(a) == len(b)}


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "secrets":
        print(json.dumps(secrets(), indent=1))
    elif cmd == "replay":
        print(json.dumps(replay(), indent=1))
    else:
        print(json.dumps(determinism(Path(sys.argv[2])), indent=1))
