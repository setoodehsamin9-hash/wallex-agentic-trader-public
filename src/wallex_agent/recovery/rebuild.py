"""Deterministic rebuild of ledger, orders and loss history from the state log.

Replays exactly what the live process did: the same `Ledger.apply_fill` calls with the same fill amounts, plan and
dust threshold, in the same order. Every FILL record carries the cash and position quantity observed after it; any
divergence (or a fill/state for an unknown order, a duplicated fill, an unbalanced reservation, a checkpoint digest
mismatch, an intent whose content no longer matches its hash) raises `RebuildError`. Recovery never "repairs" or
guesses state; it fails closed.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from wallex_agent.contracts import TERMINAL_ORDER_STATES, OrderIntent, OrderState, RiskVerdict
from wallex_agent.contracts.hashing import content_id
from wallex_agent.execution.engine import OrderRecord, CANCEL_CONFIRMED, CANCEL_ENDS, awaiting_cancel_confirmation
from wallex_agent.execution.venue import VenueFill
from wallex_agent.portfolio.ledger import Fill, Ledger
from wallex_agent.risk.protections import Protections

from .state_log import StateRecord, aware, plan_from_json


class RebuildError(RuntimeError):
    pass


@dataclass
class Rebuilt:
    orders: dict[str, OrderRecord] = field(default_factory=lambda: {})
    risk_state: dict[str, Any] | None = None
    checkpoints_verified: int = 0
    records: int = 0


def _num(d: Decimal) -> str:
    return format(d.normalize(), "f")


def ledger_digest(ledger: Ledger) -> dict[str, Any]:
    """Value-based fingerprint of trade truth (independent of Decimal exponent)."""
    return {"cash": _num(ledger.cash_tmn), "realized": _num(ledger.realized_pnl_tmn), "fees": _num(ledger.fees_paid_tmn),
            "reservations": {k: _num(v) for k, v in sorted(ledger.reservations.items())},
            "positions": {s: {"id": p.position_id, "qty": _num(p.qty), "cost": _num(p.cost_tmn), "exit": p.exit_order_id}
                          for s, p in sorted(ledger.positions.items())},
            "dust": {k: _num(v) for k, v in sorted(ledger.dust.items())}, "trades": len(ledger.trades)}


def risk_state(ledger: Ledger, prot: Protections, ai_invalid_streak: int) -> dict[str, Any]:
    """The restart-relevant risk memory that is not derivable from fills (peak/drawdown, pauses, session baselines)."""
    return {"peak_equity_tmn": str(ledger.peak_equity_tmn), "max_drawdown_fraction": str(ledger.max_drawdown_fraction),
            "drawdown_pause_until": prot.drawdown_pause_until.isoformat() if prot.drawdown_pause_until else None,
            "pause_trigger_drawdown": None if prot.pause_trigger_drawdown is None else str(prot.pause_trigger_drawdown),
            "session_start_equity": {k: str(v) for k, v in sorted(prot.session_start_equity.items())},
            "ai_invalid_streak": ai_invalid_streak}


def apply_risk_state(data: dict[str, Any], ledger: Ledger, prot: Protections) -> int:
    """Restores the risk memory; returns the AI invalid-decision streak."""
    ledger.peak_equity_tmn = Decimal(data["peak_equity_tmn"])
    ledger.max_drawdown_fraction = Decimal(data["max_drawdown_fraction"])
    until = data["drawdown_pause_until"]
    prot.drawdown_pause_until = aware(until) if until else None
    trig = data["pause_trigger_drawdown"]
    prot.pause_trigger_drawdown = None if trig is None else Decimal(trig)
    prot.session_start_equity = {k: Decimal(v) for k, v in data["session_start_equity"].items()}
    return int(data["ai_invalid_streak"])


def rebuild(records: list[StateRecord], *, ledger: Ledger, protections: Protections) -> Rebuilt:
    """Replays `records` into a FRESH ledger/protections (no journaling hooks attached)."""
    if ledger.trades or ledger.positions or ledger.reservations or ledger.cash_tmn != ledger.starting_cash_tmn:
        raise RebuildError("rebuild needs a fresh ledger")
    out = Rebuilt(records=len(records))
    orders = out.orders
    for r in records:
        d = r.data
        try:
            match r.kind:
                case "ORDER":
                    intent = OrderIntent.model_validate(d["intent"])
                    if content_id(intent.model_dump(exclude={"intent_hash", "schema_version"})) != intent.intent_hash:
                        raise RebuildError(f"seq {r.seq}: intent content does not match its hash")
                    cid = intent.client_order_id
                    if cid in orders:
                        raise RebuildError(f"seq {r.seq}: duplicate order {cid}")
                    orders[cid] = OrderRecord(intent, RiskVerdict.model_validate(d["verdict"]), plan_from_json(d["plan"]),
                                              d["strategy_version"])
                case "STATE":
                    rec = _order(orders, d["cid"], r.seq)
                    if Decimal(d["cum_filled_qty"]) != rec.filled_qty:
                        raise RebuildError(f"seq {r.seq}: {d['cid']} state reports {d['cum_filled_qty']} filled, "
                                           f"replayed {rec.filled_qty}")
                    rec.state = OrderState(d["state"])
                    if rec.state is OrderState.CANCEL_PENDING and rec.cancel_requested_at is None:
                        rec.cancel_requested_at = r.at  # a recovered cancel still needs its double-lookup confirmation
                    if rec.cancel_requested_at is not None and rec.state in CANCEL_ENDS:
                        rec.terminal_seen = 2 if d.get("raw") == CANCEL_CONFIRMED else max(rec.terminal_seen, 1)
                case "FILL":
                    _fill(orders, r, ledger, protections)
                case "RESERVE":
                    if d["rid"] in ledger.reservations:
                        raise RebuildError(f"seq {r.seq}: duplicate reservation {d['rid']}")
                    ledger.reservations[d["rid"]] = Decimal(d["amount"])
                case "RELEASE":
                    if ledger.reservations.pop(d["rid"], None) is None:
                        raise RebuildError(f"seq {r.seq}: release of unknown reservation {d['rid']}")
                case "RISK":
                    out.risk_state = d
                case "CHECKPOINT":
                    if "digest" in d:
                        _derive_exit_markers(orders, ledger)
                        if d["digest"] != ledger_digest(ledger):
                            raise RebuildError(f"seq {r.seq}: checkpoint digest does not match the replayed ledger")
                        out.checkpoints_verified += 1
                case _:
                    raise RebuildError(f"seq {r.seq}: unknown record kind {r.kind}")
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            if isinstance(exc, RebuildError):
                raise
            raise RebuildError(f"seq {r.seq} ({r.kind}): {type(exc).__name__}: {exc}") from exc
    _derive_exit_markers(orders, ledger)
    return out


def _order(orders: dict[str, OrderRecord], cid: str, seq: int) -> OrderRecord:
    rec = orders.get(cid)
    if rec is None:
        raise RebuildError(f"seq {seq}: record for unknown order {cid}")
    return rec


def _fill(orders: dict[str, OrderRecord], r: StateRecord, ledger: Ledger, prot: Protections) -> None:
    d = r.data
    rec = _order(orders, d["cid"], r.seq)
    intent = rec.intent
    if d["fill_id"] in rec.applied:
        raise RebuildError(f"seq {r.seq}: fill {d['fill_id']} applied twice")
    vf = VenueFill(d["fill_id"], Decimal(d["qty"]), Decimal(d["price"]), Decimal(d["fee"]), d["fee_asset"],
                   aware(d["at"]))
    trade = ledger.apply_fill(
        Fill(intent.symbol, intent.side, vf.qty, vf.price, vf.fee, vf.fee_asset, vf.at, intent.client_order_id,
             intent.purpose.value, intent.decision_id, rec.strategy_version),
        plan=rec.plan if intent.side == "BUY" else None, dust_qty=Decimal(d["dust_qty"]))
    if ledger.cash_tmn != Decimal(d["cash_after"]):
        raise RebuildError(f"seq {r.seq}: replayed cash {ledger.cash_tmn} != recorded {d['cash_after']}")
    pos = ledger.positions.get(intent.symbol)
    if (pos.qty if pos is not None else Decimal(0)) != Decimal(d["position_qty_after"]):
        raise RebuildError(f"seq {r.seq}: replayed position qty differs from recorded {d['position_qty_after']}")
    rec.applied.add(vf.fill_id)
    rec.filled_qty += vf.qty
    rec.fills.append(vf)
    if trade is not None:
        rec.trades.append(trade)
        prot.record_exit(trade.net_pnl_tmn, trade.closed_at)


def _derive_exit_markers(orders: dict[str, OrderRecord], ledger: Ledger) -> None:
    """A position's pending-exit marker is exactly its non-terminal SELL order (the engine's single-exit rule)."""
    for pos in ledger.positions.values():
        pos.exit_order_id = None
    for cid, rec in orders.items():
        if rec.intent.side != "SELL" or (rec.state in TERMINAL_ORDER_STATES and not awaiting_cancel_confirmation(rec)):
            continue
        pos = ledger.positions.get(rec.intent.symbol)
        if pos is None:
            continue
        if pos.exit_order_id is not None:
            raise RebuildError(f"two pending exits for {rec.intent.symbol}: {pos.exit_order_id}, {cid}")
        pos.exit_order_id = cid
