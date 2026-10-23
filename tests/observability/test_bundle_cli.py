import hashlib
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from .helpers import T, fixture
from wallex_agent.contracts.hashing import canonical_json
from wallex_agent.interface import cli
from wallex_agent.observability.bundle import export_evidence, verify_bundle
from wallex_agent.observability.inspector import inspect_run
from wallex_agent.observability.presentation import explain_decisions, render_demo_fa
from wallex_agent.observability.source import EvidenceError, read_run


def test_export_hashes_and_observable_reconstruction_are_deterministic(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    source = read_run(db, "sample")
    before = db.read_bytes()
    out = export_evidence(source, tmp_path / "one", generated_at=T)
    other = export_evidence(source, tmp_path / "two", generated_at=T)
    assert db.read_bytes() == before
    assert {p.name: p.read_bytes() for p in out.iterdir()} == {p.name: p.read_bytes() for p in other.iterdir()}
    expected = {"SUMMARY.json", "PERFORMANCE_FA.md", "TRADES.jsonl", "DECISIONS.jsonl", "RISK_EVENTS.jsonl",
                "MODEL_EVENTS.jsonl", "NEWS_EVENTS.jsonl", "RECOVERY_EVENTS.jsonl", "RUN_MANIFEST.json",
                "REPRODUCIBILITY.json", "FILE_HASHES.json", "SOURCE.json", "EXECUTION_EVENTS.jsonl", "EQUITY.jsonl", "GENERATION.json", "DEMO_FA.md"}
    assert {p.name for p in out.iterdir()} == expected
    hashes = json.loads((out / "FILE_HASHES.json").read_text())
    for name, sha in hashes["files"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == sha
    verified = verify_bundle(out)
    assert verified["reconstruction_matches"] is True
    assert verified["summary"]["net_pnl_tmn"] == "3.9"
    assert verified["summary"] == json.loads(canonical_json(inspect_run(source)))
    assert "SIMULATED" in (out / "PERFORMANCE_FA.md").read_text()


def test_generation_time_does_not_change_reproducible_content_hash(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    source = read_run(db, "sample")
    a = export_evidence(source, tmp_path / "a", generated_at=T)
    b = export_evidence(source, tmp_path / "b", generated_at=T + timedelta(days=1))
    ha = json.loads((a / "FILE_HASHES.json").read_text())
    hb = json.loads((b / "FILE_HASHES.json").read_text())
    assert ha["reproducible_content_sha256"] == hb["reproducible_content_sha256"]
    assert ha["files"]["GENERATION.json"] != hb["files"]["GENERATION.json"]
    assert (a / "REPRODUCIBILITY.json").read_bytes() == (b / "REPRODUCIBILITY.json").read_bytes()


def test_secret_values_config_credentials_and_hidden_reasoning_do_not_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    sentinel = "test-credential-" + "not-a-real-key"
    monkeypatch.setenv("WALLEX_API_KEY", sentinel)
    with sqlite3.connect(db) as c:
        data = {"api_key": sentinel, "headers": {"Authorization": sentinel}, "account_id": "private-account",
                "session_id": "private-session", "chain_of_thought": "private-reasoning", "message": "safe evidence"}
        c.execute("INSERT INTO risk_events(run_id,at,kind,body) VALUES(?,?,?,?)", ("sample", T.isoformat(), "TEST_EVENT", json.dumps(data)))
    out = export_evidence(read_run(db, "sample"), tmp_path / "out", generated_at=T)
    text = "".join(p.read_text() for p in out.iterdir())
    for forbidden in (sentinel, "private-account", "private-session", "private-reasoning", "Authorization"):
        assert forbidden not in text
    assert "safe evidence" in text
    assert verify_bundle(out)["reconstruction_matches"] is True


@pytest.mark.parametrize("text", ["api_key=plain-secret-value", "Authorization: Bearer dummy" + "q" * 24, "sk-" + "x" * 32])
def test_unstructured_secret_material_fails_before_writing_bundle(tmp_path: Path, text: str) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO risk_events(run_id,at,kind,body) VALUES(?,?,?,?)", ("sample", T.isoformat(), "TEST_EVENT", json.dumps({"message": text})))
    with pytest.raises(EvidenceError, match="secret"):
        export_evidence(read_run(db, "sample"), tmp_path / "out", generated_at=T)
    assert not (tmp_path / "out").exists()


def test_bundle_tampering_and_existing_destination_are_refused(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    source = read_run(db, "sample")
    out = export_evidence(source, tmp_path / "out", generated_at=T)
    with pytest.raises(EvidenceError):
        export_evidence(source, out, generated_at=T)
    (out / "TRADES.jsonl").write_text("")
    with pytest.raises(EvidenceError):
        verify_bundle(out)


def test_cli_commands_are_read_only_and_unknowns_are_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    monkeypatch.setattr(cli, "STATE", tmp_path)
    before = db.read_bytes()
    for command in (["status", "sample"], ["performance", "sample"], ["explain", "sample", "--last", "2"], ["demo-evidence", "sample"]):
        assert cli.main([*command, "--db", "run.sqlite"]) == 0
    text = capsys.readouterr().out
    assert "BACKTEST" in text and "REAL WRITE" in text and "3.9" in text
    assert "نامعلوم" in text or "ناموجود" in text
    assert db.read_bytes() == before
    assert cli.main(["evidence", "export", "sample", "--db", "run.sqlite", "--out", str(tmp_path / "out"), "--generated-at", T.isoformat()]) == 0
    assert cli.main(["evidence", "verify", str(tmp_path / "out")]) == 0
    assert cli.main(["status", "sample", "--db", "nonexistent.sqlite"]) != 0
    assert not (tmp_path / "nonexistent.sqlite").exists()
    assert cli.main(["explain", "sample", "--db", "run.sqlite", "--last", "0"]) != 0


def test_demo_shows_missing_evidence_without_inventing_success(tmp_path: Path) -> None:
    db = tmp_path / "run.sqlite"
    fixture(db)
    source = read_run(db, "sample")
    assert explain_decisions(source, last=2) == []
    demo = render_demo_fa(source)
    assert "ناموجود" in demo and "BACKTEST" in demo
    assert "موفق" not in demo


def test_real_runtime_backtest_and_shadow_journals_are_supported(tmp_path: Path) -> None:
    from support.candidate_path import _cycle, _runtime, dataset, shadow_cfg  # pyright: ignore[reportPrivateUsage]
    from wallex_agent.agent.baseline import BaselineSelector
    from wallex_agent.contracts import Environment
    from wallex_agent.journal.store import JournalStore
    from wallex_agent.runtime.backtest import run_backtest

    ds = dataset(tmp_path)
    cfg = shadow_cfg()
    shadow_db = tmp_path / "shadow.sqlite"
    store = JournalStore(shadow_db)
    runtime, _ = _runtime(cfg, ds, store, "recorded-shadow", BaselineSelector())
    decision = _cycle(runtime, ds)
    runtime.mark_equity(decision.recorded_at, record=True)
    runtime.checkpoint("INTERRUPTED", decision.recorded_at)
    store.close()
    observed = read_run(shadow_db, "recorded-shadow")
    s = inspect_run(observed)
    assert s["environment"] == "SHADOW" and s["real_wallex_write_count"] == 0
    explanations = explain_decisions(observed, last=1)
    assert explanations[0]["selected_candidate_id"] == decision.validated.decision.selected_candidate_id  # type: ignore[union-attr]
    assert explanations[0]["brief_rationale"] == decision.validated.decision.brief_rationale  # type: ignore[union-attr]
    assert explanations[0]["strategy_reason_codes"] is None  # no stored reason code in the candidate schema
    assert explanations[0]["execution_events"]
    assert "BASELINE" in render_demo_fa(observed)
    native_bundle = export_evidence(observed, tmp_path / "native-context-bundle", generated_at=T)
    assert verify_bundle(native_bundle)["reconstruction_matches"] is True
    backtest_db = tmp_path / "backtest.sqlite"
    store = JournalStore(backtest_db)
    bt_cfg = cfg.model_copy(update={"run": cfg.run.model_copy(update={"environment": Environment.BACKTEST})})
    result = run_backtest(bt_cfg, ds, store, BaselineSelector(), run_id="recorded-backtest")
    store.close()
    backtest = inspect_run(read_run(backtest_db, "recorded-backtest"))
    assert backtest["environment"] == "BACKTEST" and backtest["real_wallex_write_count"] == 0
    assert backtest["realized_pnl_tmn"] == result.runtime.ledger.realized_pnl_tmn
    assert backtest["fees_tmn"] == result.runtime.ledger.fees_paid_tmn


def test_observer_cli_never_constructs_writable_store_or_contacts_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    from typing import Any
    from wallex_agent.journal.store import JournalStore
    db = tmp_path / "run.sqlite"
    fixture(db)
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("observer attempted a write-capable or network path")
    monkeypatch.setattr(JournalStore, "__init__", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(cli, "STATE", tmp_path)
    assert cli.main(["status", "--run-id", "sample", "--db", "run.sqlite"]) == 0
    assert cli.main(["evidence", "export", "sample", "--db", "run.sqlite", "--out", str(tmp_path / "out")]) == 0
    assert cli.main(["evidence", "verify", str(tmp_path / "out")]) == 0


def test_secret_fields_in_context_are_not_mistaken_for_publishable_structured_evidence(tmp_path: Path) -> None:
    from wallex_agent.journal.store import JournalStore
    db = tmp_path / "run.sqlite"
    fixture(db)
    store = JournalStore(db)
    store.put_ref("sample", "ctx:untrusted", {"snapshot": {"account_id": "sensitive-account"},
                                              "candidates": [{"hidden_chain_of_thought": "private-rationale"}]})
    store.close()
    with pytest.raises(EvidenceError):
        export_evidence(read_run(db, "sample"), tmp_path / "out", generated_at=T)
    assert not (tmp_path / "out").exists()


def test_decision_context_must_match_its_recorded_ref_and_snapshot(tmp_path: Path) -> None:
    from support.candidate_path import _cycle, _runtime, dataset, shadow_cfg  # pyright: ignore[reportPrivateUsage]
    from wallex_agent.agent.baseline import BaselineSelector
    from wallex_agent.journal.store import JournalStore
    ds = dataset(tmp_path)
    db = tmp_path / "run.sqlite"
    store = JournalStore(db)
    runtime, _ = _runtime(shadow_cfg(), ds, store, "sample", BaselineSelector())
    decision = _cycle(runtime, ds)
    key = f"ctx:{decision.decision_id}"
    context = store.get_ref("sample", key)
    assert context is not None
    context["snapshot"]["snapshot_id"] = "unrelated-snapshot"
    context["features"]["snapshot_id"] = "unrelated-snapshot"
    for candidate in context["candidates"]:
        candidate["snapshot_id"] = "unrelated-snapshot"
    store.put_ref("sample", key, context)
    store.close()
    with pytest.raises(EvidenceError):
        inspect_run(read_run(db, "sample"))


def test_valuation_before_a_same_timestamp_fill_is_undefined(tmp_path: Path) -> None:
    from support.candidate_path import T as cycle_time, _cycle, _runtime, dataset, shadow_cfg  # pyright: ignore[reportPrivateUsage]
    from wallex_agent.agent.baseline import BaselineSelector
    from wallex_agent.journal.store import JournalStore
    ds = dataset(tmp_path)
    db = tmp_path / "run.sqlite"
    store = JournalStore(db)
    runtime, _ = _runtime(shadow_cfg(), ds, store, "sample", BaselineSelector())
    runtime.mark_equity(cycle_time, record=True)
    _cycle(runtime, ds)
    store.close()
    s = inspect_run(read_run(db, "sample"))
    assert s["open_positions"] and s["fees_tmn"] > 0
    assert s["final_equity_tmn"] is None and s["unrealized_pnl_tmn"] is None
    assert s["net_pnl_tmn"] is None and s["return_pct"] is None


def test_wait_explanation_shows_stored_validation_failure_without_llm_text(tmp_path: Path) -> None:
    from support.candidate_path import _cycle, _runtime, dataset, shadow_cfg  # pyright: ignore[reportPrivateUsage]
    from wallex_agent.agent.context import DecisionContext
    from wallex_agent.contracts import AgentAction, DecisionProvenance, DecisionSource, ValidatedDecision, ValidationStatus
    from wallex_agent.journal.store import JournalStore
    from wallex_agent.observability.presentation import render_explanation_fa

    class RecordedTimeout:
        source_name = "SCRIPTED"
        def decide(self, ctx: DecisionContext) -> tuple[ValidatedDecision, DecisionProvenance]:
            return (ValidatedDecision(status=ValidationStatus.TIMEOUT, decision=None, effective_action=AgentAction.WAIT),
                    DecisionProvenance(source=DecisionSource.LLM, model_id="scripted-timeout-fixture", model_settings_sha256=None,
                        prompt_version=None, prompt_sha256=None, model_requests=1, latency_ms=10, raw_output_ref=None,
                        started_at=ctx.now, finished_at=ctx.now))
    ds = dataset(tmp_path)
    db = tmp_path / "run.sqlite"
    store = JournalStore(db)
    runtime, _ = _runtime(shadow_cfg(), ds, store, "sample", RecordedTimeout())
    _cycle(runtime, ds)
    store.close()
    source = read_run(db, "sample")
    explanation = explain_decisions(source, last=1)[0]
    assert explanation["brief_rationale"] is None
    assert "TIMEOUT" in render_explanation_fa(explanation)
    assert explanation["trade_outcomes"] is None
    assert inspect_run(source)["model_timeouts"] == 1
