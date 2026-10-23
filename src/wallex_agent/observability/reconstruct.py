"""Pure observation replay of the persisted hash chain into a fresh, hook-free ledger.

No runtime, recovery coordinator, venue or execution engine is imported or constructed.
Never replay cumulative execution fees/fills as deltas; only unique FILL records move this private ledger.
"""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from wallex_agent.contracts import OrderIntent, OrderState
from wallex_agent.contracts.hashing import content_id, sha256_hex
from wallex_agent.portfolio.ledger import Fill, Ledger

from .source import EvidenceError, RunEvidence, number, obj, parse, timestamp

ZERO = Decimal(0)


@dataclass
class Reconstruction:
    ledger: Ledger
    orders: dict[str, dict[str, Any]] = field(default_factory=lambda: {})
    state_events: list[tuple[str, str, Decimal]] = field(default_factory=lambda: [])
    last_fill_at: datetime | None = None
    last_risk: dict[str, Any] | None = None
    checkpoints_verified: int = 0
    available: bool = False


def digest(ledger: Ledger, orders: dict[str, dict[str, Any]]) -> dict[str, Any]:
    def norm(value: Decimal) -> str:
        return format(value.normalize(), "f")

    exits: dict[str, str] = {}
    for cid, order in orders.items():
        if order["side"] == "SELL" and order["state"] not in ("FILLED", "CANCELED", "REJECTED", "EXPIRED"):
            if order["symbol"] in exits:
                raise EvidenceError("multiple unresolved exits in journal")
            exits[order["symbol"]] = cid
    return {"cash": norm(ledger.cash_tmn), "realized": norm(ledger.realized_pnl_tmn), "fees": norm(ledger.fees_paid_tmn),
        "reservations": {k: norm(v) for k, v in sorted(ledger.reservations.items())},
        "positions": {s: {"id": p.position_id, "qty": norm(p.qty), "cost": norm(p.cost_tmn), "exit": exits.get(s)}
                      for s, p in sorted(ledger.positions.items())},
        "dust": {k: norm(v) for k, v in sorted(ledger.dust.items())}, "trades": len(ledger.trades)}


def reconstruct(source: RunEvidence, starting_capital: Decimal) -> Reconstruction:
    out = Reconstruction(Ledger(starting_capital), available=bool(source.states))
    ledger = out.ledger
    previous = "0" * 64
    fills: set[tuple[str, str]] = set()
    try:
        for expected, row in enumerate(source.states, 1):
            if row["seq"] != expected or row["prev"] != previous or sha256_hex((previous + row["body"]).encode()) != row["hash"]:
                raise EvidenceError("state journal sequence/hash chain does not verify")
            data = parse(row["body"])
            kind, at = data["kind"], data["at"]
            if data["v"] != 1 or kind != row["kind"]:
                raise EvidenceError("state journal version/kind mismatch")
            if at is not None:
                timestamp(at)
            d = obj(data["data"])
            match kind:
                case "ORDER":
                    intent = OrderIntent.model_validate(d["intent"])
                    if content_id(intent.model_dump(exclude={"intent_hash", "schema_version"})) != intent.intent_hash:
                        raise EvidenceError("persisted intent hash mismatch")
                    if intent.environment != source.manifest.environment or intent.autonomy != source.manifest.autonomy:
                        raise EvidenceError("order environment/autonomy contamination")
                    cid = intent.client_order_id
                    if cid in out.orders:
                        raise EvidenceError("duplicate order across journal/restart")
                    out.orders[cid] = {"client_order_id": cid, "intent_id": intent.intent_id, "symbol": intent.symbol,
                        "side": intent.side, "qty": str(intent.qty), "purpose": intent.purpose.value,
                        "decision_id": intent.decision_id, "strategy_version": d["strategy_version"], "state": "INTENT", "filled": ZERO}
                case "FILL":
                    cid = d["cid"]
                    order = out.orders[cid]
                    fill_key = (cid, str(d["fill_id"]))
                    if fill_key in fills:
                        raise EvidenceError("duplicate persisted fill")
                    fills.add(fill_key)
                    qty = number(d["qty"])
                    order["filled"] += qty
                    if order["filled"] > number(order["qty"]):
                        raise EvidenceError("fills exceed persisted order quantity")
                    fill_at = timestamp(d["at"])
                    ledger.apply_fill(Fill(order["symbol"], order["side"], qty, number(d["price"]), number(d["fee"]),
                        d["fee_asset"], fill_at, cid, order["purpose"], order["decision_id"], order["strategy_version"]),
                        plan=None, dust_qty=number(d["dust_qty"]))
                    if ledger.cash_tmn != number(d["cash_after"]):
                        raise EvidenceError("reconstructed cash differs from persisted fill checkpoint")
                    pos = ledger.positions.get(order["symbol"])
                    if (pos.qty if pos else ZERO) != number(d["position_qty_after"]):
                        raise EvidenceError("reconstructed position differs from persisted fill checkpoint")
                    out.last_fill_at = max(out.last_fill_at, fill_at) if out.last_fill_at else fill_at
                case "STATE":
                    order = out.orders[d["cid"]]
                    qty = number(d["cum_filled_qty"])
                    if qty != order["filled"]:
                        raise EvidenceError("cumulative state quantity disagrees with persisted fills")
                    order["state"] = OrderState(d["state"]).value
                    out.state_events.append((d["cid"], d["state"], qty))
                case "RESERVE":
                    if d["rid"] in ledger.reservations or number(d["amount"]) <= 0:
                        raise EvidenceError("invalid/duplicate persisted reservation")
                    ledger.reservations[d["rid"]] = number(d["amount"])
                case "RELEASE":
                    if ledger.reservations.pop(d["rid"], None) is None:
                        raise EvidenceError("release without persisted reservation")
                case "RISK":
                    out.last_risk = d
                    for key in ("peak_equity_tmn", "max_drawdown_fraction"):
                        if key in d:
                            number(d[key])
                case "CHECKPOINT":
                    if "digest" in d:
                        if d["digest"] != digest(ledger, out.orders):
                            raise EvidenceError("persisted ledger checkpoint does not reproduce")
                        out.checkpoints_verified += 1
                case _:
                    raise EvidenceError("unsupported state journal event kind")
            previous = row["hash"]
        if out.available:
            if {t.trade_id for t in ledger.trades} != {t["trade_id"] for t in source.trades}:
                raise EvidenceError("persisted trades are missing or differ from reconstructed closed trades")
            recorded = {t["trade_id"]: t for t in source.trades}
            for trade in ledger.trades:
                t = recorded[trade.trade_id]
                if any(number(t[k]) != getattr(trade, k) for k in ("net_pnl_tmn", "fees_tmn", "qty", "avg_entry_price", "avg_exit_price")):
                    raise EvidenceError("persisted trade amounts do not reproduce")
                if timestamp(t["opened_at"]) != trade.opened_at or timestamp(t["closed_at"]) != trade.closed_at:
                    raise EvidenceError("persisted trade times do not reproduce")
        return out
    except EvidenceError:
        raise
    except (ValueError, TypeError, KeyError, ArithmeticError, RuntimeError):
        raise EvidenceError("state journal is incomplete or cannot reconstruct trade truth") from None
