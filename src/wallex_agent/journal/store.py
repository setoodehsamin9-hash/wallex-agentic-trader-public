"""SQLite journal: append-only decision records, execution events, trades, risk events, payloads (recorded model
outputs / tool results / cycle contexts for replay), kill switch state, approval tickets, runtime status and the
hash-chained state log that restart recovery replays (`wallex_agent.recovery`).

One file under the git-ignored state dir. WAL mode so the CLI (status/approve/kill) can read/write while a
SHADOW/LIVE process runs. Secrets are never written here: callers pass contracts and payloads that do not contain
credentials, and export runs a secret scan before anything leaves `var/`.
"""

import dataclasses
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wallex_agent.contracts import ApprovalStatus, ApprovalTicket, DecisionRecord, ExecutionEvent, RunManifest
from wallex_agent.contracts.hashing import canonical_json, sha256_hex
from wallex_agent.portfolio.ledger import TradeRecord
from wallex_agent.risk.kill_switch import KillState, KillSwitch

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, manifest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, recorded_at TEXT, status TEXT,
  seq INTEGER, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exec_events (event_id TEXT PRIMARY KEY, run_id TEXT, client_order_id TEXT, at TEXT, seq INTEGER,
  body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trades (trade_id TEXT PRIMARY KEY, run_id TEXT, closed_at TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS risk_events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, at TEXT, kind TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS payloads (sha256 TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS refs (run_id TEXT, key TEXT, sha256 TEXT, PRIMARY KEY (run_id, key));
CREATE TABLE IF NOT EXISTS kill (id INTEGER PRIMARY KEY CHECK (id = 1), latched INTEGER, reason TEXT, at TEXT, by TEXT);
CREATE TABLE IF NOT EXISTS tickets (ticket_id TEXT PRIMARY KEY, run_id TEXT, intent_hash TEXT, status TEXT, expires_at TEXT,
  ticket TEXT NOT NULL, bundle TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS status (run_id TEXT PRIMARY KEY, updated_at TEXT, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS equity (run_id TEXT, at TEXT, equity TEXT);
CREATE TABLE IF NOT EXISTS state_log (run_id TEXT NOT NULL, seq INTEGER NOT NULL, at TEXT, kind TEXT NOT NULL, body TEXT NOT NULL,
  prev TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (run_id, seq));
"""


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt is not None else None


class JournalStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path, isolation_level=None, timeout=10, check_same_thread=False)  # tools run in a worker thread; access is serialized
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._seq = 0

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Connection, None, None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._db.close()

    # --- runs ---------------------------------------------------------------------------------------------------
    def record_run(self, manifest: RunManifest) -> None:
        self._db.execute("INSERT INTO runs VALUES (?,?,?,?)",
                         (manifest.run_id, _iso(manifest.started_at), None, manifest.model_dump_json()))

    def seal_run(self, run_id: str, ended_at: datetime, manifest: RunManifest | None = None) -> None:
        if manifest is not None:
            self._db.execute("UPDATE runs SET ended_at=?, manifest=? WHERE run_id=?", (_iso(ended_at), manifest.model_dump_json(), run_id))
        else:
            self._db.execute("UPDATE runs SET ended_at=? WHERE run_id=?", (_iso(ended_at), run_id))

    def manifest(self, run_id: str) -> RunManifest | None:
        row = self._db.execute("SELECT manifest FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return RunManifest.model_validate_json(row[0]) if row else None

    def run_ended_at(self, run_id: str) -> datetime | None:
        row = self._db.execute("SELECT ended_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def latest_run_id(self) -> str | None:
        row = self._db.execute("SELECT run_id FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def unfinished_runs(self) -> list[RunManifest]:
        return [RunManifest.model_validate_json(row[0]) for row in self._db.execute(
            "SELECT manifest FROM runs WHERE ended_at IS NULL ORDER BY started_at")]

    # --- decisions / events / trades / risk ------------------------------------------------------------------------
    def record_decision(self, rec: DecisionRecord) -> None:
        self._seq += 1
        self._db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?)",
                         (rec.decision_id, rec.run_id, _iso(rec.recorded_at), rec.status.value, self._seq, rec.model_dump_json()))

    def decisions(self, run_id: str, *, limit: int | None = None, newest_first: bool = False) -> list[DecisionRecord]:
        order = "DESC" if newest_first else "ASC"
        q = f"SELECT body FROM decisions WHERE run_id=? ORDER BY recorded_at {order}, seq {order}, rowid {order}"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [DecisionRecord.model_validate_json(r[0]) for r in self._db.execute(q, (run_id,))]

    def decision(self, decision_id: str) -> DecisionRecord | None:
        row = self._db.execute("SELECT body FROM decisions WHERE decision_id=?", (decision_id,)).fetchone()
        return DecisionRecord.model_validate_json(row[0]) if row else None

    def record_event(self, run_id: str, ev: ExecutionEvent, extra: dict[str, Any]) -> None:
        self._seq += 1
        body = {"event": json.loads(ev.model_dump_json()), **extra}
        self._db.execute("INSERT OR IGNORE INTO exec_events VALUES (?,?,?,?,?,?)",
                         (ev.event_id, run_id, ev.client_order_id, _iso(ev.at), self._seq, json.dumps(body, default=str)))

    def events(self, run_id: str) -> list[dict[str, Any]]:
        return [json.loads(r[0]) for r in self._db.execute(
            "SELECT body FROM exec_events WHERE run_id=? ORDER BY at, seq", (run_id,))]

    def record_trade(self, run_id: str, trade: TradeRecord) -> None:
        body = {k: (v if isinstance(v, (str, int)) else str(v)) for k, v in dataclasses.asdict(trade).items()}
        self._db.execute("INSERT OR IGNORE INTO trades VALUES (?,?,?,?)",
                         (f"{run_id}:{trade.trade_id}", run_id, _iso(trade.closed_at), json.dumps(body, default=str)))

    def trades(self, run_id: str) -> list[dict[str, Any]]:
        return [json.loads(r[0]) for r in self._db.execute("SELECT body FROM trades WHERE run_id=? ORDER BY closed_at", (run_id,))]

    def record_risk_event(self, run_id: str, at: datetime, kind: str, data: dict[str, Any]) -> None:
        self._db.execute("INSERT INTO risk_events (run_id, at, kind, body) VALUES (?,?,?,?)",
                         (run_id, _iso(at), kind, json.dumps(data, default=str, ensure_ascii=False)))

    def risk_events(self, run_id: str) -> list[dict[str, Any]]:
        return [{"at": r[0], "kind": r[1], **json.loads(r[2])} for r in self._db.execute(
            "SELECT at, kind, body FROM risk_events WHERE run_id=? ORDER BY id", (run_id,))]

    def record_equity(self, run_id: str, at: datetime, equity: str) -> None:
        self._db.execute("INSERT INTO equity VALUES (?,?,?)", (run_id, _iso(at), equity))

    # --- payloads (replay) ---------------------------------------------------------------------------------------
    def put_payload(self, data: bytes) -> str:
        ref = sha256_hex(data)
        self._db.execute("INSERT OR IGNORE INTO payloads VALUES (?,?)", (ref, data))
        return ref

    def get_payload(self, ref: str) -> bytes | None:
        row = self._db.execute("SELECT data FROM payloads WHERE sha256=?", (ref,)).fetchone()
        return bytes(row[0]) if row else None

    def put_ref(self, run_id: str, key: str, obj: Any) -> str:
        ref = self.put_payload(canonical_json(obj))
        self._db.execute("INSERT OR REPLACE INTO refs VALUES (?,?,?)", (run_id, key, ref))
        return ref

    def get_ref(self, run_id: str, key: str) -> Any | None:
        row = self._db.execute("SELECT sha256 FROM refs WHERE run_id=? AND key=?", (run_id, key)).fetchone()
        if not row:
            return None
        data = self.get_payload(row[0])
        return json.loads(data) if data is not None else None

    # --- kill switch -----------------------------------------------------------------------------------------------
    def kill_switch(self) -> KillSwitch:
        def read() -> KillState:
            row = self._db.execute("SELECT latched, reason, at, by FROM kill WHERE id=1").fetchone()
            return KillState(bool(row[0]), row[1], datetime.fromisoformat(row[2]) if row[2] else None, row[3]) if row else KillState(False, None, None, None)

        def save(st: KillState) -> None:
            self._db.execute("INSERT OR REPLACE INTO kill VALUES (1,?,?,?,?)", (int(st.latched), st.reason, _iso(st.at), st.by))

        return KillSwitch(read(), on_change=save, on_read=read)

    def kill_state(self) -> KillState:
        return self.kill_switch().state

    # --- approval tickets --------------------------------------------------------------------------------------------
    def save_ticket(self, run_id: str, ticket: ApprovalTicket, bundle: dict[str, Any]) -> None:
        self._db.execute("INSERT INTO tickets VALUES (?,?,?,?,?,?,?)",
                         (ticket.ticket_id, run_id, ticket.intent_hash, ticket.status.value, _iso(ticket.expires_at),
                          ticket.model_dump_json(), json.dumps(bundle, default=str)))

    def ticket(self, ticket_id: str, *, run_id: str | None = None) -> tuple[ApprovalTicket, dict[str, Any]] | None:
        query, args = "SELECT ticket, bundle FROM tickets WHERE ticket_id=?", [ticket_id]
        if run_id is not None:
            query, args = query + " AND run_id=?", [*args, run_id]
        row = self._db.execute(query, args).fetchone()
        return (ApprovalTicket.model_validate_json(row[0]), json.loads(row[1])) if row else None

    def tickets(self, status: ApprovalStatus | None = None, *, run_id: str | None = None) -> list[ApprovalTicket]:
        q = "SELECT ticket FROM tickets WHERE 1=1"
        args: list[str] = []
        if status is not None:
            q, args = q + " AND status=?", [*args, status.value]
        if run_id is not None:
            q, args = q + " AND run_id=?", [*args, run_id]
        return [ApprovalTicket.model_validate_json(r[0]) for r in self._db.execute(q, args)]

    def transition_ticket(self, ticket_id: str, expect: ApprovalStatus, new: ApprovalTicket) -> bool:
        """Atomic compare-and-set; returns False if the ticket was not in `expect` (already used, rejected...)."""
        with self._tx() as db:
            cur = db.execute("UPDATE tickets SET status=?, ticket=? WHERE ticket_id=? AND status=?",
                             (new.status.value, new.model_dump_json(), ticket_id, expect.value))
            return cur.rowcount == 1

    # --- state log (restart recovery) ------------------------------------------------------------------------------
    def append_state(self, run_id: str, seq: int, at: str | None, kind: str, body: str, prev: str, digest: str) -> None:
        """Plain INSERT: a second writer for the same (run, seq) fails loudly instead of forking the chain."""
        self._db.execute("INSERT INTO state_log VALUES (?,?,?,?,?,?,?)", (run_id, seq, at, kind, body, prev, digest))

    def state_rows(self, run_id: str) -> list[tuple[int, str, str, str, str]]:
        """(seq, kind, body, prev, hash) in sequence order."""
        return [(int(r[0]), r[1], r[2], r[3], r[4]) for r in self._db.execute(
            "SELECT seq, kind, body, prev, hash FROM state_log WHERE run_id=? ORDER BY seq", (run_id,))]

    def last_state(self, run_id: str) -> tuple[int, str] | None:
        row = self._db.execute("SELECT seq, hash FROM state_log WHERE run_id=? ORDER BY seq DESC LIMIT 1", (run_id,)).fetchone()
        return (int(row[0]), row[1]) if row else None

    # --- runtime status ------------------------------------------------------------------------------------------------
    def set_status(self, run_id: str, at: datetime, body: dict[str, Any]) -> None:
        self._db.execute("INSERT OR REPLACE INTO status VALUES (?,?,?)",
                         (run_id, _iso(at), json.dumps(body, default=str, ensure_ascii=False)))

    def get_status(self, run_id: str | None = None) -> dict[str, Any] | None:
        if run_id is None:
            row = self._db.execute("SELECT body FROM status ORDER BY updated_at DESC LIMIT 1").fetchone()
        else:
            row = self._db.execute("SELECT body FROM status WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None
