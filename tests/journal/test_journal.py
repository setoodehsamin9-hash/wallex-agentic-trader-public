import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from wallex_agent.contracts import AgentAction, AutonomyMode, CodeVersion, CycleStatus, DecisionRecord, Environment
from wallex_agent.agent.ninerouter import REPO_ROOT, NineRouterNotConfigured
from wallex_agent.journal.export import SecretLeak, export_run, known_secret_literals, scan_for_secrets
from wallex_agent.journal.store import JournalStore
from wallex_agent.reporting.performance import PerformanceSummary

T = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
CODE = CodeVersion(git_commit="a" * 40, dirty=False)


def _rec(i: int, status: CycleStatus = CycleStatus.WAITED) -> DecisionRecord:
    return DecisionRecord(decision_id=f"d{i}", run_id="r1", cycle_id=f"c{i}", recorded_at=T, environment=Environment.BACKTEST,
                          autonomy=AutonomyMode.AUTONOMOUS, symbol="BTCTMN", snapshot_id="s", data_refs=(), features_ref=None,
                          deterministic_regime=None, candidates=(), provenance=None, validated=None,
                          effective_action=AgentAction.WAIT, policy_outcome=None, risk_verdict=None, approval=None,
                          order_intent=None, status=status, config_sha256="c", code=CODE)


def test_records_are_append_only(tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    store.record_decision(_rec(1))
    with pytest.raises(sqlite3.IntegrityError):
        store.record_decision(_rec(1))
    assert [r.decision_id for r in store.decisions("r1")] == ["d1"]


def test_every_cycle_status_roundtrips(tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    for i, st in enumerate(CycleStatus):
        store.record_decision(_rec(i, st))
    assert [r.status for r in store.decisions("r1")] == list(CycleStatus)


def test_payload_roundtrip_by_hash(tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    ref = store.put_payload(b'{"x":1}')
    assert len(ref) == 64 and store.get_payload(ref) == b'{"x":1}'
    assert store.put_payload(b'{"x":1}') == ref


def test_kill_state_persists_across_reopen(tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    ks = store.kill_switch()
    ks.latch("drill", at=T)
    assert JournalStore(tmp_path / "j.sqlite").kill_switch().latched


def test_active_kill_handle_observes_cli_latch_and_reset(tmp_path: Path) -> None:
    runtime_store = JournalStore(tmp_path / "j.sqlite")
    operator_store = JournalStore(tmp_path / "j.sqlite")
    active = runtime_store.kill_switch()
    operator_store.kill_switch().latch("operator stop", at=T, by="owner")
    assert active.latched and active.state.reason == "operator stop"
    operator_store.kill_switch().reset("resume after check", at=T, operator="owner")
    assert not active.latched


def test_secret_scan_blocks_known_patterns(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text('{"h": "X-API-Key: abcdefghijklmnop1234"}')
    with pytest.raises(SecretLeak):
        scan_for_secrets(tmp_path, extra_literals=())
    (tmp_path / "a.json").write_text('{"note": "contains my-secret-literal"}')
    with pytest.raises(SecretLeak):
        scan_for_secrets(tmp_path, extra_literals=("my-secret-literal",))


def test_export_credential_lookup_refuses_repository_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Synthetic text only. The key loader must refuse the path before reading it, including during export scans.
    with tempfile.TemporaryDirectory(prefix="codex-key-path-test-", dir=REPO_ROOT) as folder:
        path = Path(folder) / "placeholder.txt"
        path.write_text("synthetic-placeholder-value")
        monkeypatch.delenv("NINEROUTER_API_KEY", raising=False)
        monkeypatch.setenv("NINEROUTER_API_KEY_FILE", str(path))
        with pytest.raises(NineRouterNotConfigured, match="inside the repository"):
            known_secret_literals()
        summary = PerformanceSummary.empty(run_id="r1", environment="BACKTEST", autonomy="AUTONOMOUS",
                                           starting_capital_tmn=Decimal(5000000))
        out = tmp_path / "refused-export"
        with pytest.raises(NineRouterNotConfigured, match="inside the repository"):
            export_run(JournalStore(tmp_path / "j.sqlite"), "r1", out, summary=summary, context={})
        assert not out.exists()


def test_export_writes_required_artefacts(tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")
    store.record_decision(_rec(1))
    store.record_risk_event("r1", T, "RISK_REJECTED", {"checks": ["spread"]})
    summary = PerformanceSummary.empty(run_id="r1", environment="BACKTEST", autonomy="AUTONOMOUS",
                                       starting_capital_tmn=Decimal(5_000_000))
    out = export_run(store, "r1", tmp_path / "out", summary=summary, context={"strategy": "c1-0.1.0"})
    for name in ("SUMMARY.json", "TRADES.jsonl", "DECISIONS.jsonl", "RISK_EVENTS.jsonl", "PERFORMANCE_FA.md"):
        assert (out / name).exists(), name
    assert json.loads((out / "SUMMARY.json").read_text())["starting_capital_tmn"] == "5000000"
    assert "سرمایه شروع" in (out / "PERFORMANCE_FA.md").read_text()
