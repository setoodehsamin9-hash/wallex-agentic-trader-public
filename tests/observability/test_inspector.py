import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from .helpers import T, fixture
from wallex_agent.observability.inspector import inspect_run
from wallex_agent.observability.source import EvidenceError, read_run


def test_ledger_pnl_fees_and_drawdown_are_independently_aggregated(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    s = inspect_run(read_run(db, "sample"))
    assert s["environment"] == "BACKTEST"
    assert s["cash_tmn"] == Decimal("1003.9")
    assert s["realized_pnl_tmn"] == Decimal("3.9")
    assert s["unrealized_pnl_tmn"] == 0
    assert s["fees_tmn"] == Decimal("6.1")
    assert s["net_pnl_tmn"] == Decimal("3.9")  # fees are already in cost basis; never deduct twice
    assert s["return_pct"] == Decimal("0.39")
    assert s["peak_equity_tmn"] == 1100
    assert s["max_drawdown_pct"] == 20
    assert s["trades"] == 2 and s["wins"] == 1 and s["losses"] == 1
    assert s["win_rate_pct"] == 50
    assert s["profit_factor"] == Decimal("15.8") / Decimal("11.9")
    assert s["avg_trade_pnl_tmn"] == Decimal("1.95")
    assert s["real_wallex_write_count"] == 0


@pytest.mark.parametrize("environment", ["BACKTEST", "SHADOW"])
def test_simulator_zero_is_proved_by_execution_sources_and_log(tmp_path: Path, environment: str) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db, environment)
    s = inspect_run(read_run(db, "sample"))
    assert s["real_wallex_write_count"] == 0
    assert s["write_count_evidence"]["matched_state_events"] == 12
    assert s["activity_kind"] == "SIMULATED"


def test_live_lifecycle_is_not_a_complete_exchange_write_audit(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db, "LIVE")
    s = inspect_run(read_run(db, "sample"))
    assert s["environment"] == "LIVE" and s["activity_kind"] == "EXCHANGE"
    assert s["real_wallex_write_count"] == "UNKNOWN"  # cancellations are not audited in the legacy schema
    assert s["observed_exchange_submissions"] == 4


def test_zero_closed_trades_and_unrealized_profit_are_separate(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db, closed=False)
    s = inspect_run(read_run(db, "sample"))
    assert s["trades"] == 0 and s["wins"] == 0 and s["losses"] == 0
    assert s["win_rate_pct"] is None and s["profit_factor"] is None and s["avg_trade_pnl_tmn"] is None
    assert s["realized_pnl_tmn"] == 0 and s["unrealized_pnl_tmn"] == 18
    assert s["open_positions"][0]["qty"] == "2"
    assert s["exposure_tmn"] is None  # gross marks/reservations are not the liquidation equity


def test_all_wins_profit_factor_is_undefined(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db, second=False)
    assert inspect_run(read_run(db, "sample"))["profit_factor"] is None


def test_missing_sources_never_become_a_plausible_zero(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    with sqlite3.connect(db) as c:
        c.execute("DELETE FROM state_log")
        c.execute("DELETE FROM exec_events")
        c.execute("DELETE FROM trades")
        c.execute("DELETE FROM equity")
    s = inspect_run(read_run(db, "sample"))
    assert s["real_wallex_write_count"] == "UNKNOWN"
    assert s["cash_tmn"] is None and s["trades"] is None and s["fees_tmn"] is None
    assert s["open_positions"] is None and s["net_pnl_tmn"] is None
    assert s["breaker"] is None and s["news_context_count"] is None
    assert s["health"] == "UNKNOWN"


def test_unknown_order_and_multiple_restarts_remain_visible(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    with sqlite3.connect(db) as c:
        rows = c.execute("SELECT body FROM exec_events WHERE event_id='order-3-FILLED'").fetchone()
        body = json.loads(rows[0])
        body["event"]["state"] = "UNKNOWN"
        c.execute("UPDATE exec_events SET body=? WHERE event_id='order-3-FILLED'", (json.dumps(body),))
        for _ in range(2):
            c.execute("INSERT INTO risk_events(run_id,at,kind,body) VALUES(?,?,?,?)", ("sample", T.isoformat(), "RECOVERY_STARTED", '{}'))
    s = inspect_run(read_run(db, "sample"))
    assert len(s["unknown_orders"]) == 1
    assert s["restart_count"] == 2 and s["recovery_event_count"] == 2
    assert s["health"] == "ATTENTION"
    assert s["real_wallex_write_count"] == "UNKNOWN"  # event/log mismatch cannot prove zero


@pytest.mark.parametrize("damage", ["mixed_source", "manifest_label", "bad_json", "hash", "missing_fill", "missing_trade", "nan"])
def test_corrupt_or_contaminated_evidence_fails_honestly(tmp_path: Path, damage: str) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    with sqlite3.connect(db) as c:
        if damage == "mixed_source":
            body = json.loads(c.execute("SELECT body FROM exec_events LIMIT 1").fetchone()[0])
            body["event"]["source"] = "EXCHANGE"
            c.execute("UPDATE exec_events SET body=? WHERE rowid=1", (json.dumps(body),))
        elif damage == "manifest_label":
            body = json.loads(c.execute("SELECT manifest FROM runs").fetchone()[0])
            body["environment"] = "LIVE"
            c.execute("UPDATE runs SET manifest=?", (json.dumps(body),))
        elif damage == "bad_json":
            c.execute("UPDATE exec_events SET body='{' WHERE rowid=1")
        elif damage == "hash":
            c.execute("UPDATE state_log SET hash='broken' WHERE seq=2")
        elif damage == "missing_fill":
            c.execute("DELETE FROM state_log WHERE kind='FILL' AND seq=(SELECT MIN(seq) FROM state_log WHERE kind='FILL')")
        elif damage == "missing_trade":
            c.execute("DELETE FROM trades WHERE rowid=1")
        else:
            c.execute("UPDATE equity SET equity='NaN'")
    with pytest.raises(EvidenceError):
        inspect_run(read_run(db, "sample"))


def test_reader_cannot_create_or_write_source(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(EvidenceError):
        read_run(missing, "sample")
    assert not missing.exists()
    db = tmp_path / "run.sqlite"
    fixture(db)
    before = db.read_bytes()
    inspect_run(read_run(db, "sample"))
    assert db.read_bytes() == before
    with pytest.raises(EvidenceError):
        read_run(db, "another-run")


def test_fresh_persisted_status_exposes_valuation_and_exposure(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db, closed=False)
    with sqlite3.connect(db) as c:
        status = {"run_id": "sample", "environment": "BACKTEST", "autonomy": "AUTONOMOUS", "active": False,
                  "equity_tmn": "1020", "cash_tmn": "798", "exposure_tmn": "224.24", "execution_errors": 2,
                  "positions": [{"symbol": "BTCTMN", "qty": "2"}], "kill_switch": False}
        c.execute("INSERT INTO status VALUES(?,?,?)", ("sample", "2026-10-20T08:13:00+00:00", json.dumps(status)))
    s = inspect_run(read_run(db, "sample"))
    assert s["final_equity_tmn"] == 1020
    assert s["unrealized_pnl_tmn"] == 20
    assert s["exposure_tmn"] == Decimal("224.24")
    assert s["execution_errors"] == 2
    assert s["valuation_at"] == "2026-10-20T08:13:00+00:00"


def test_stale_status_cannot_replace_more_recent_fill_evidence(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db, closed=False)
    with sqlite3.connect(db) as c:
        status: dict[str, Any] = {"run_id": "sample", "environment": "BACKTEST", "autonomy": "AUTONOMOUS", "active": True,
                  "equity_tmn": "9999", "cash_tmn": "1000", "exposure_tmn": "0", "positions": []}
        c.execute("INSERT INTO status VALUES(?,?,?)", ("sample", T.isoformat(), json.dumps(status)))
    s = inspect_run(read_run(db, "sample"))
    assert s["final_equity_tmn"] == 1018 and s["exposure_tmn"] is None


def test_status_positions_must_match_reconstructed_ledger(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db, closed=False)
    with sqlite3.connect(db) as c:
        status: dict[str, Any] = {"run_id": "sample", "environment": "BACKTEST", "autonomy": "AUTONOMOUS", "cash_tmn": "798",
                  "exposure_tmn": "1000000", "equity_tmn": "1000000", "positions": []}
        c.execute("INSERT INTO status VALUES(?,?,?)", ("sample", "2026-10-20T08:13:00+00:00", json.dumps(status)))
    with pytest.raises(EvidenceError):
        inspect_run(read_run(db, "sample"))


def test_news_shock_is_not_invented_as_a_veto(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    with sqlite3.connect(db) as c:
        for kind, payload in [("NEWS_SHOCK", {"action": "NO_EFFECT"}), ("NEWS_GATE", {"action": "BLOCK_NEW_ENTRIES"}), ("NEWS_GATE", {"action": "BLOCK_NEW_ENTRIES"})]:
            c.execute("INSERT INTO risk_events(run_id,at,kind,body) VALUES(?,?,?,?)", ("sample", T.isoformat(), kind, json.dumps(payload)))
    s = inspect_run(read_run(db, "sample"))
    assert s["news_event_count"] == 3 and s["news_veto_count"] == 2


def test_state_enum_is_validated_even_if_no_exchange_event_matches_it(tmp_path: Path) -> None:
    from wallex_agent.contracts.hashing import canonical_json, sha256_hex
    db = tmp_path / "run.sqlite"
    fixture(db)
    with sqlite3.connect(db) as c:
        row = c.execute("SELECT seq,body,prev FROM state_log ORDER BY seq DESC LIMIT 1").fetchone()
        body = canonical_json({"v": 1, "kind": "STATE", "at": T, "data": {"cid": "order-3", "state": "INVALID", "cum_filled_qty": "1"}}).decode()
        prev = c.execute("SELECT hash FROM state_log WHERE seq=?", (row[0] - 1,)).fetchone()[0]
        c.execute("UPDATE state_log SET kind='STATE',body=?,prev=?,hash=? WHERE seq=?", (body, prev, sha256_hex((prev+body).encode()), row[0]))
    with pytest.raises(EvidenceError):
        inspect_run(read_run(db, "sample"))


def test_news_and_data_status_follow_persisted_timestamps_not_reference_names(tmp_path: Path) -> None:
    from wallex_agent.journal.store import JournalStore
    db = tmp_path / "run.sqlite"
    fixture(db)
    store = JournalStore(db)
    store.put_ref("sample", "news:z-earlier", {"as_of": T.isoformat(), "enabled": True, "flags": ["OLD"]})
    store.put_ref("sample", "news:a-later", {"as_of": "2026-10-20T08:10:00Z", "enabled": True, "flags": ["LATEST"]})
    store.put_ref("sample", "news:gate", {"action": "NO_EFFECT", "reason": "DISABLED"})
    store.close()
    s = inspect_run(read_run(db, "sample"))
    assert s["news_context_count"] == 2
    assert s["news_status"]["flags"] == ["LATEST"]


def test_environment_name_and_manually_supplied_zero_never_prove_no_writes(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    with sqlite3.connect(db) as c:
        c.execute("DELETE FROM exec_events")
        c.execute("INSERT INTO risk_events(run_id,at,kind,body) VALUES(?,?,?,?)",
                  ("sample", T.isoformat(), "AUDIT", '{"real_wallex_write_count":0}'))
    s = inspect_run(read_run(db, "sample"))
    assert s["real_wallex_write_count"] == "UNKNOWN"


def test_reader_creates_no_source_sidecars_and_sees_committed_active_wal(tmp_path: Path) -> None:
    from wallex_agent.journal.store import JournalStore
    db = tmp_path / "source" / "run.sqlite"
    fixture(db)
    names_before = {p.name for p in db.parent.iterdir()}
    inspect_run(read_run(db, "sample"))
    assert {p.name for p in db.parent.iterdir()} == names_before
    writer = JournalStore(db)
    writer.record_risk_event("sample", T, "RECOVERY_STARTED", {})
    before = {p.name: p.read_bytes() for p in db.parent.iterdir()}
    assert inspect_run(read_run(db, "sample"))["restart_count"] == 1
    after = {p.name: p.read_bytes() for p in db.parent.iterdir()}
    assert after == before
    writer.close()


def test_legacy_completed_orders_are_not_reported_as_open(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    with sqlite3.connect(db) as c:
        c.execute("DELETE FROM state_log")
    s = inspect_run(read_run(db, "sample"))
    assert s["open_orders"] == []
    assert s["real_wallex_write_count"] == "UNKNOWN"
    assert s["cash_tmn"] is None


def test_missing_tables_leave_model_risk_and_execution_counts_undefined(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    with sqlite3.connect(db) as c:
        c.execute("DROP TABLE decisions")
        c.execute("DROP TABLE exec_events")
    s = inspect_run(read_run(db, "sample"))
    assert s["ai_calls"] is None and s["model_timeouts"] is None and s["model_failures"] is None
    assert s["ai_invalid_by_status"] is None
    assert s["risk_counts"] is None and s["risk_blocks"] is None
    assert s["execution_errors"] is None and s["observed_exchange_submissions"] is None
