"""LIVE preflight: every prerequisite is checked and any material mismatch fails closed. Phase 03 exercises it only
with fake account readers; the real private read happens in a later, explicit phase on the owner's machine."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from wallex_agent.contracts import AutonomyMode, MarketMeta

from .config import RunConfig, missing_live_requirements
from .provenance import current_code_version, live_release_problems


class AccountReader(Protocol):
    def balances(self) -> dict[str, tuple[Decimal, Decimal]]: ...

    def fee_rate(self, symbol: str) -> Decimal | None: ...

    def open_orders(self) -> list[dict[str, Any]]: ...


@dataclass
class PreflightReport:
    checks: list[tuple[str, bool, str]] = field(default_factory=lambda: [])

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append((name, ok, detail))

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks)


def run_preflight(cfg: RunConfig, *, now: datetime, account: AccountReader | None, metas: dict[str, MarketMeta],
                  eligible: frozenset[str], marks: dict[str, Decimal], require_clean_tree: bool = True) -> PreflightReport:
    r = PreflightReport()
    missing = missing_live_requirements(cfg)
    r.add("config_complete", not missing, ", ".join(missing) or "all LIVE keys set and owner-approved")
    if cfg.run.autonomy is AutonomyMode.AUTONOMOUS:
        r.add("autonomous_gate", cfg.run.live_autonomous_enabled, "live_autonomous_enabled must be set deliberately (U04 answered)")
    end = cfg.competition.end_utc
    r.add("competition_clock", end is not None and end > now, f"end={end.isoformat() if end else None}")
    if require_clean_tree:
        code = current_code_version()
        problems = live_release_problems()
        r.add("clean_tagged_tree", not code.dirty and not problems, f"commit={code.git_commit[:12]} " + ("; ".join(problems) or "clean, tagged"))
    for s in cfg.run.symbols:
        m = metas.get(s)
        r.add(f"market:{s}", s in eligible and m is not None and m.market_order_enabled,
              "eligible + metadata + market orders enabled" if m else "no metadata")
    if account is None:
        r.add("account_access", False, "no private account reader (Wallex key not configured in this phase)")
        return r
    bal = account.balances()
    tmn_avail, tmn_locked = bal.get("TMN", (Decimal(0), Decimal(0)))
    tol = cfg.limits.kill.reconciliation_tolerance_tmn or Decimal(0)
    expect = cfg.run.starting_capital_tmn
    r.add("spendable_tmn", abs(tmn_avail - expect) <= tol, f"available={tmn_avail} expected={expect} tolerance={tol}")
    r.add("no_locked_tmn", tmn_locked == 0, f"locked={tmn_locked}")
    unexpected = {a: v for a, v in bal.items() if a != "TMN" and (v[0] + v[1]) * marks.get(f"{a}TMN", Decimal(0)) > tol}
    unknown_value = [a for a, v in bal.items() if a != "TMN" and (v[0] + v[1]) > 0 and f"{a}TMN" not in marks]
    r.add("no_unexpected_assets", not unexpected and not unknown_value, f"unexpected={sorted(unexpected)} unvalued={unknown_value}")
    orders = account.open_orders()
    r.add("no_open_orders", not orders, f"open_orders={len(orders)}")
    for s in cfg.run.symbols:
        fee = account.fee_rate(s)
        r.add(f"fee_rate:{s}", fee is not None and fee <= cfg.costs.fee_rate,
              f"account fee={fee} must be <= the fee the evidence assumed ({cfg.costs.fee_rate})")
    return r
