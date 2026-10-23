"""State log: the append-only, hash-chained record of every mutation of trade truth, per run.

Written synchronously by the execution engine (ORDER before the venue call, every FILL, every STATE change), by the
ledger (RESERVE/RELEASE) and by the runtime (RISK checkpoints, CHECKPOINT digests). Restart recovery replays it into
a fresh ledger (`rebuild`); nothing else is trusted, in particular not model conversation memory.

Integrity: each record stores `hash = sha256(prev_hash + body)` with a contiguous per-run `seq`. An edited, deleted
or reordered record breaks the chain and recovery fails closed. Past records are never rewritten; recovery appends.
No secrets are written: only contracts and amounts the journal already holds.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from wallex_agent.contracts import ExecutionEvent
from wallex_agent.contracts.hashing import canonical_json, sha256_hex
from wallex_agent.execution.engine import OrderRecord
from wallex_agent.execution.venue import VenueFill
from wallex_agent.journal.store import JournalStore
from wallex_agent.portfolio.ledger import ExitPlan, Ledger

STATE_LOG_VERSION = 1
GENESIS = "0" * 64
KINDS = frozenset({"ORDER", "FILL", "STATE", "RESERVE", "RELEASE", "RISK", "CHECKPOINT"})


class StateLogCorrupt(RuntimeError):
    """The persisted log cannot be trusted (hash/chain/sequence/version/format)."""


@dataclass(frozen=True, slots=True)
class StateRecord:
    seq: int
    kind: str
    at: datetime | None
    data: dict[str, Any]


def aware(text: str) -> datetime:
    """Parse a persisted timestamp; a timezone-naive value is corrupt state (all stored times are UTC-aware)."""
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise ValueError(f"timezone-naive timestamp {text!r}")
    return value


def plan_json(plan: ExitPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    out: dict[str, Any] = {"candidate_id": plan.candidate_id, "stop_price": str(plan.stop_price),
                           "take_profit_price": None if plan.take_profit_price is None else str(plan.take_profit_price),
                           "deadline": plan.deadline.isoformat()}
    if plan.trail_distance is not None:  # optional keys keep legacy plans (and their hashes) byte-identical
        out["trail_distance"] = str(plan.trail_distance)
    if plan.signal_exit:
        out["signal_exit"] = True
    return out


def plan_from_json(data: dict[str, Any] | None) -> ExitPlan | None:
    if data is None:
        return None
    tp = data["take_profit_price"]
    trail = data.get("trail_distance")
    return ExitPlan(data["candidate_id"], Decimal(data["stop_price"]), None if tp is None else Decimal(tp),
                    aware(data["deadline"]), None if trail is None else Decimal(trail), bool(data.get("signal_exit", False)))


class StateLog:
    def __init__(self, store: JournalStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id
        last = store.last_state(run_id)
        self._seq, self._prev = last if last is not None else (0, GENESIS)

    @property
    def last_seq(self) -> int:
        return self._seq

    def append(self, kind: str, data: dict[str, Any], at: datetime | None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown state record kind {kind}")
        body = canonical_json({"v": STATE_LOG_VERSION, "kind": kind, "at": at, "data": data}).decode()
        digest = sha256_hex((self._prev + body).encode())
        self.store.append_state(self.run_id, self._seq + 1, at.isoformat() if at else None, kind, body, self._prev, digest)
        self._seq, self._prev = self._seq + 1, digest

    def records(self) -> list[StateRecord]:
        out: list[StateRecord] = []
        prev = GENESIS
        for expected, (seq, kind, body, stored_prev, digest) in enumerate(self.store.state_rows(self.run_id), start=1):
            if seq != expected:
                raise StateLogCorrupt(f"sequence gap: expected {expected}, found {seq}")
            if stored_prev != prev:
                raise StateLogCorrupt(f"broken chain at seq {seq}")
            if sha256_hex((prev + body).encode()) != digest:
                raise StateLogCorrupt(f"hash mismatch at seq {seq}")
            try:
                parsed = json.loads(body)
                at = parsed["at"]
                rec = StateRecord(seq, parsed["kind"], aware(at) if at else None, parsed["data"])
                version = parsed["v"]
            except (ValueError, KeyError, TypeError) as exc:
                raise StateLogCorrupt(f"unreadable record at seq {seq}: {exc}") from exc
            if version != STATE_LOG_VERSION:
                raise StateLogCorrupt(f"state log version {version} at seq {seq}; this code reads {STATE_LOG_VERSION}")
            if rec.kind != kind or rec.kind not in KINDS:
                raise StateLogCorrupt(f"kind mismatch at seq {seq}")
            out.append(rec)
            prev = digest
        return out

    # --- ExecutionRecorder ------------------------------------------------------------------------------------
    def order_opened(self, rec: OrderRecord, at: datetime) -> None:
        self.append("ORDER", {"intent": rec.intent.model_dump(mode="json"), "verdict": rec.verdict.model_dump(mode="json"),
                              "plan": plan_json(rec.plan), "strategy_version": rec.strategy_version}, at)

    def fill_applied(self, rec: OrderRecord, fill: VenueFill, dust_qty: Decimal, ledger: Ledger) -> None:
        pos = ledger.positions.get(rec.intent.symbol)
        self.append("FILL", {"cid": rec.intent.client_order_id, "fill_id": fill.fill_id, "qty": str(fill.qty),
                             "price": str(fill.price), "fee": str(fill.fee), "fee_asset": fill.fee_asset,
                             "at": fill.at.isoformat(), "dust_qty": str(dust_qty), "cash_after": str(ledger.cash_tmn),
                             "position_qty_after": str(pos.qty) if pos is not None else "0"}, fill.at)

    def state_changed(self, rec: OrderRecord, ev: ExecutionEvent) -> None:
        self.append("STATE", {"cid": ev.client_order_id, "state": ev.state.value, "raw": ev.exchange_status_raw,
                              "cum_filled_qty": str(ev.cum_filled_qty)}, ev.at)

    # --- Ledger hook ------------------------------------------------------------------------------------------
    def reservation(self, kind: str, reservation_id: str, amount: Decimal) -> None:
        self.append(kind, {"rid": reservation_id, "amount": str(amount)}, None)
