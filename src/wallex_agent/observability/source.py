"""Take one consistent SQLite read snapshot, including committed WAL data, without creating a journal.

Only selected structured payloads are retained. Config values, raw model/tool responses, endpoints,
headers and arbitrary payloads are never copied. Source hashes refer to original persisted bytes.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, ValidationError

from wallex_agent.contracts import DecisionRecord, ExecutionEvent, FeatureSet, MarketSnapshot, RunManifest, SignalCandidate
from wallex_agent.contracts.hashing import canonical_json, sha256_hex


class EvidenceError(ValueError):
    """Insufficient, inconsistent or malformed evidence. Messages must never include source values."""


def timestamp(value: Any) -> datetime:
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if out.utcoffset() != timedelta(0):
            raise ValueError
        return out.astimezone(UTC)
    except (TypeError, ValueError):
        raise EvidenceError("invalid or non-UTC evidence timestamp") from None


def number(value: Any) -> Decimal:
    try:
        if isinstance(value, (bool, float)) or value is None:
            raise ValueError
        out = Decimal(value)
        if not out.is_finite():
            raise ValueError
        return out
    except (ValueError, TypeError, ArithmeticError):
        raise EvidenceError("invalid finite decimal in evidence") from None


def obj(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError("expected structured evidence object")
    return cast(dict[str, Any], value)


def parse(raw: str | bytes) -> dict[str, Any]:
    try:
        return obj(json.loads(raw))
    except (ValueError, TypeError, UnicodeDecodeError):
        raise EvidenceError("malformed structured journal evidence") from None


def pick(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: data[k] for k in keys if k in data}


def reference_view(key: str, data: dict[str, Any]) -> dict[str, Any] | None:
    if key == "run_config":
        return {"run": pick(obj(data.get("run", {})), ("environment", "autonomy", "symbols", "starting_capital_tmn")),
                "costs": pick(obj(data.get("costs", {})), ("fee_rate", "backtest_slippage_bps")),
                "strategy": pick(obj(data.get("strategy", {})), ("active",))}
    if key.startswith("ctx:"):
        snapshot = MarketSnapshot.model_validate(data["snapshot"])
        features = FeatureSet.model_validate(data["features"])
        candidates = [SignalCandidate.model_validate(c) for c in data["candidates"]]
        if features.snapshot_id != snapshot.snapshot_id or any(c.snapshot_id != snapshot.snapshot_id for c in candidates):
            raise EvidenceError("context snapshot/features/candidates do not agree")
        return {"snapshot": parse(canonical_json(snapshot)), "features": features.model_dump(mode="json"),
                "candidates": [c.model_dump(mode="json") for c in candidates]}
    if key.startswith("recovery:"):
        return pick(data, ("run_id", "at", "environment", "autonomy", "outcome", "phase_reached", "state_records",
                           "checkpoints_verified", "kill_switch_latched", "blockers", "warnings"))
    if key.startswith("news:"):
        return pick(data, ("context_id", "as_of", "enabled", "assets", "source_count", "provider_count", "cluster_count",
                           "omitted_story_count", "flags", "action", "reason", "story_ids", "version", "asset"))
    return None


class RunEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    format_version: int = 1
    manifest: RunManifest
    ended_at: datetime | None
    decisions: tuple[DecisionRecord, ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    trades: tuple[dict[str, Any], ...] = ()
    risk_events: tuple[dict[str, Any], ...] = ()
    equity: tuple[dict[str, Any], ...] = ()
    states: tuple[dict[str, Any], ...] = ()
    status: dict[str, Any] | None = None
    references: dict[str, dict[str, Any]] = {}
    source_hashes: dict[str, str] = {}
    available_tables: tuple[str, ...] = ()

    @property
    def run_id(self) -> str:
        return self.manifest.run_id


def read_run(path: Path, run_id: str | None = None) -> RunEvidence:
    """No JournalStore, schema migration, WAL pragma, config loader, credentials or network."""
    if not path.is_file():
        raise EvidenceError("journal file does not exist")
    db: sqlite3.Connection | None = None
    scratch: tempfile.TemporaryDirectory[str] | None = None
    try:
        original = path.resolve()
        wal = Path(str(original) + "-wal")

        def capture() -> tuple[bytes, bytes | None]:
            return original.read_bytes(), wal.read_bytes() if wal.exists() else None

        captured = capture()
        if capture() != captured:
            raise EvidenceError("journal changed while capturing DB/WAL; retry inspection")
        # Even mode=ro can create/update source -shm/-wal. Open only an isolated copy.
        # Never use immutable=1 on the original: that would silently ignore active WAL commits.
        scratch = tempfile.TemporaryDirectory(prefix="wallex-observer-")
        snapshot = Path(scratch.name) / "snapshot.sqlite"
        snapshot.write_bytes(captured[0])
        if captured[1] is not None:
            Path(str(snapshot) + "-wal").write_bytes(captured[1])
        db = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True, timeout=5)
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "runs" not in tables:
            raise EvidenceError("journal has no runs table")
        if run_id is None:
            latest = db.execute("SELECT run_id FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
            if latest is None:
                raise EvidenceError("journal contains no runs")
            run_id = str(latest[0])
        row = db.execute("SELECT manifest,started_at,ended_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise EvidenceError("run id is absent from this journal")
        manifest = RunManifest.model_validate(parse(row[0]))
        if manifest.run_id != run_id or timestamp(row[1]) != manifest.started_at:
            raise EvidenceError("manifest/run identity mismatch")
        ended = timestamp(row[2]) if row[2] else None
        if manifest.ended_at is not None and manifest.ended_at != ended:
            raise EvidenceError("manifest/run end time mismatch")
        if ended is not None and ended < manifest.started_at:
            raise EvidenceError("run ends before it starts")
        hashes: dict[str, str] = {"manifest": sha256_hex(row[0].encode())}

        def rows(table: str, columns: str, ordering: str = "rowid") -> list[Any]:
            if table not in tables:
                return []
            result = db.execute(f"SELECT {columns} FROM {table} WHERE run_id=? ORDER BY {ordering}", (run_id,)).fetchall()  # type: ignore[union-attr]
            hashes[table] = sha256_hex(canonical_json([list(r) for r in result]))
            return result

        decisions: list[DecisionRecord] = []
        for identity, raw in rows("decisions", "decision_id,body", "recorded_at,rowid"):
            decision = DecisionRecord.model_validate(parse(raw))
            if decision.decision_id != identity:
                raise EvidenceError("decision identity mismatch")
            decisions.append(decision)
        events: list[dict[str, Any]] = []
        for identity, raw in rows("exec_events", "event_id,body"):
            data = parse(raw)
            ev = ExecutionEvent.model_validate(data["event"])
            if ev.event_id != identity:
                raise EvidenceError("execution event identity mismatch")
            events.append({"event": ev.model_dump(mode="json"), **pick(data, ("symbol", "side", "purpose", "qty", "limit_price"))})
        trades: list[dict[str, Any]] = []
        for identity, raw in rows("trades", "trade_id,body", "closed_at,rowid"):
            trade = parse(raw)
            if f"{run_id}:{trade['trade_id']}" != identity:
                raise EvidenceError("trade identity mismatch")
            trades.append(pick(trade, ("trade_id", "symbol", "decision_id", "strategy_version", "opened_at", "closed_at", "qty",
                                       "avg_entry_price", "avg_exit_price", "fees_tmn", "net_pnl_tmn", "exit_reason")))
        risk = [{"at": at, "kind": kind, "data": parse(raw), "journal_id": identity}
                for identity, at, kind, raw in rows("risk_events", "id,at,kind,body", "id")]
        equity = [{"at": at, "equity_tmn": val} for at, val in rows("equity", "at,equity", "at,rowid")]
        states = [{"seq": seq, "kind": kind, "body": raw, "prev": prev, "hash": digest}
                  for seq, kind, raw, prev, digest in rows("state_log", "seq,kind,body,prev,hash", "seq")]
        status_rows = rows("status", "updated_at,body")
        status = None
        if status_rows:
            updated, raw = status_rows[0]
            status = {**pick(parse(raw), ("run_id", "environment", "autonomy", "active", "entries_allowed", "window_reason",
                "next_boundary", "competition_state", "entry_gate", "recovery_outcome", "kill_switch", "ninerouter",
                "equity_tmn", "cash_tmn", "exposure_tmn", "positions", "execution_errors")),
                      "updated_at": updated}
        refs: dict[str, dict[str, Any]] = {}
        if "refs" in tables and "payloads" in tables:
            for key, digest in db.execute("SELECT key,sha256 FROM refs WHERE run_id=? ORDER BY key", (run_id,)):
                if key != "run_config" and not key.startswith(("ctx:", "news:", "recovery:")):
                    continue
                payload = db.execute("SELECT data FROM payloads WHERE sha256=?", (digest,)).fetchone()
                if payload is None or sha256_hex(bytes(payload[0])) != digest:
                    raise EvidenceError("missing or corrupted referenced evidence payload")
                view = reference_view(key, parse(payload[0]))
                if view is not None:
                    refs[key] = view
                    hashes[f"ref:{key}"] = digest
        return RunEvidence(manifest=manifest, ended_at=ended, decisions=tuple(decisions), events=tuple(events), trades=tuple(trades),
                           risk_events=tuple(risk), equity=tuple(equity), states=tuple(states), status=status,
                           references=refs, source_hashes=hashes, available_tables=tuple(sorted(tables)))
    except EvidenceError:
        raise
    except (sqlite3.Error, ValidationError, ValueError, KeyError, TypeError, OSError):
        raise EvidenceError("journal schema or structured evidence is malformed/incomplete") from None
    finally:
        if db is not None:
            db.close()
        if scratch is not None:
            scratch.cleanup()
