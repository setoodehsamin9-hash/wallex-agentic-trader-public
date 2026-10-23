"""LIVE runner on FAKES only (no key, no network): nothing is sent before every start check passes; the same kernel
trades through the exchange venue; any post-send failure is UNKNOWN; balances are reconciled; a session never seals
over open positions and a resumed run continues without resubmitting."""

import http.client
import subprocess
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from support.live_fakes import FakeExchange, FakePublicClient

from wallex_agent.agent.baseline import BaselineSelector
from wallex_agent.contracts import CodeVersion, Environment
from wallex_agent.execution.wallex_live import AccountReadError, HttpResult, UrllibPrivateTransport, WallexAccountReader
from wallex_agent.journal.store import JournalStore
from wallex_agent.portfolio.ledger import Ledger, Position
from wallex_agent.risk.kill_switch import KillSwitch
from wallex_agent.runtime.config import RunConfig, ShadowSettings
from wallex_agent.runtime.engine import RecoveryRequired
from wallex_agent.runtime.live import (
    BalanceWatch,
    LiveEndedNotFlat,
    LivePreflightFailed,
    account_evidence,
    live_preflight,
    resume_live,
    run_live,
)
from wallex_agent.runtime.provenance import live_release_problems

T0 = datetime(2026, 10, 3, 6, 0, tzinfo=UTC)
END = T0 + timedelta(hours=20)
WINDOW = {"version": "tw-test", "timezone": "UTC", "no_new_entry_before_close_s": 900, "flatten_before_close_s": 600,
          "windows": [{"days": ["SAT", "SUN", "MON", "TUE", "WED", "THU", "FRI"], "start": "00:00", "end": "23:59"}]}


def _live(make_cfg: Callable[..., RunConfig]) -> RunConfig:
    cfg = make_cfg(**{"run.environment": Environment.LIVE, "run.live_autonomous_enabled": True, "trading_window": WINDOW,
                      "competition.end_utc": END, "execution.exit_order_ttl_s": 60,
                      "limits.cutoff": {"no_new_entry_before_end_s": 3600, "liquidation_start_before_end_s": 1800},
                      "shadow": ShadowSettings(poll_interval_s=60, history_bars=200).model_dump()})
    data = cfg.model_dump(mode="python")
    data["limits"]["approved_by_owner"] = True
    return RunConfig.model_validate(data)


def _parts(make_cfg: Callable[..., RunConfig], **ex: Any) -> tuple[RunConfig, dict[str, datetime], FakePublicClient, FakeExchange]:
    cfg = _live(make_cfg)
    clock = {"t": T0}
    return cfg, clock, FakePublicClient(lambda: clock["t"]), FakeExchange(cfg.run.starting_capital_tmn, now=lambda: clock["t"], **ex)


def _tick(clock: dict[str, datetime]) -> Callable[[float], None]:
    def sleep(s: float) -> None:
        clock["t"] += timedelta(seconds=s)
    return sleep


# ------------------------------------------------------------------------------------------- adapter fail-closed
@pytest.mark.parametrize("error", [ConnectionResetError("reset"), http.client.IncompleteRead(b""), ValueError("<html>")])
def test_any_failure_after_sending_is_an_unknown_outcome(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def urlopen(*a: Any, **k: Any) -> Any:
        raise error

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(TimeoutError):
        UrllibPrivateTransport(key_provider=lambda: "k", min_interval_s=0).request("POST", "/v1/account/orders", {})


def test_a_malformed_order_answer_is_unknown_not_a_crash() -> None:
    from wallex_agent.execution.venue import SubmitTimeout
    from wallex_agent.execution.wallex_live import WallexLiveVenue

    class Answer:
        def __init__(self, body: Any) -> None:
            self.body = body

        def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> HttpResult:
            return HttpResult(200, self.body)

    from wallex_agent.contracts import AutonomyMode, OrderIntent, OrderPurpose
    from wallex_agent.contracts.hashing import content_id

    fields: dict[str, Any] = {"intent_id": "in-1", "decision_id": "d", "client_order_id": "wa1", "symbol": "BTCTMN", "side": "BUY",
                              "order_type": "LIMIT", "qty": Decimal("0.01"), "limit_price": Decimal(100), "purpose": OrderPurpose.ENTRY,
                              "environment": Environment.LIVE, "autonomy": AutonomyMode.AUTONOMOUS, "created_at": T0}
    intent = OrderIntent(intent_hash=content_id(fields), **fields)
    for body in (["x"], {"result": {"status": "FILLED", "fills": [{"price": "1"}]}}):
        with pytest.raises(SubmitTimeout):
            WallexLiveVenue(Answer(body)).submit(intent, T0)


def test_account_reader_fails_closed_on_http_errors_and_odd_shapes() -> None:
    class Answer:
        def __init__(self, res: HttpResult) -> None:
            self.res = res

        def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> HttpResult:
            return self.res

    for res in (HttpResult(401, {"success": False}), HttpResult(200, {"result": None}),
                HttpResult(200, {"result": {"balances": {"TMN": {"value": "abc"}}, "orders": 5}})):
        reader = WallexAccountReader(Answer(res))
        for call in (reader.balances, reader.open_orders):
            with pytest.raises(AccountReadError):
                call()


# ------------------------------------------------------------------------------------------- start checks
def test_preflight_passes_on_a_clean_account_and_records_evidence(make_cfg: Callable[..., RunConfig]) -> None:
    cfg, clock, public, ex = _parts(make_cfg)
    rep, metas, eligible, _ = live_preflight(cfg, account=WallexAccountReader(ex), client=public, now=clock["t"],  # type: ignore[arg-type]
                                             require_clean_tree=False)
    assert rep.ok, [c for c in rep.checks if not c[1]]
    assert {"BTCTMN", "ETHTMN"} <= eligible and set(cfg.run.symbols) <= set(metas)
    ev = account_evidence(WallexAccountReader(ex), cfg, now=clock["t"])
    assert ev.spendable_tmn == cfg.run.starting_capital_tmn and ev.open_orders == 0 and ev.fee_rates["BTCTMN"] == Decimal("0.0025")
    assert all(m == "GET" for m, _ in ex.calls)  # preflight is read-only


@pytest.mark.parametrize("ex_kw", [{"open_orders": 1}, {"locked": Decimal(1000)}, {"fee": "0.0035"}, {"fail_reads": True}])
def test_runner_sends_nothing_when_preflight_fails(make_cfg: Callable[..., RunConfig], tmp_path: Path, ex_kw: dict[str, Any]) -> None:
    cfg, clock, public, ex = _parts(make_cfg, **ex_kw)
    store = JournalStore(tmp_path / "j.sqlite")
    with pytest.raises(LivePreflightFailed):
        run_live(cfg, store, BaselineSelector(), run_id="lv", duration_s=3600, transport=ex, client=public,  # type: ignore[arg-type]
                 clock=lambda: clock["t"], sleep=lambda s: None, require_clean_tree=False)
    assert all(m == "GET" for m, _ in ex.calls) and store.manifest("lv") is None


def test_runner_refuses_non_live_configs_and_unfinished_runs(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    cfg, clock, public, ex = _parts(make_cfg)
    store = JournalStore(tmp_path / "j.sqlite")
    shadow = make_cfg(**{"run.environment": Environment.SHADOW, "shadow": ShadowSettings().model_dump()})
    with pytest.raises(ValueError):
        run_live(shadow, store, BaselineSelector(), run_id="x", duration_s=60, transport=ex, client=public,  # type: ignore[arg-type]
                 clock=lambda: clock["t"], sleep=lambda s: None, require_clean_tree=False)
    unapproved = cfg.model_copy(update={"limits": cfg.limits.model_copy(update={"approved_by_owner": False})})
    with pytest.raises(LivePreflightFailed):
        run_live(unapproved, store, BaselineSelector(), run_id="x", duration_s=60, transport=ex, client=public,  # type: ignore[arg-type]
                 clock=lambda: clock["t"], sleep=lambda s: None, require_clean_tree=False)
    assert ex.calls == []

    def stop(s: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):  # leaves an unfinished run behind
        run_live(cfg, store, BaselineSelector(), run_id="first", duration_s=3600, transport=ex, client=public,  # type: ignore[arg-type]
                 clock=lambda: clock["t"], sleep=stop, require_clean_tree=False)
    before = len(ex.calls)
    with pytest.raises(RecoveryRequired):
        run_live(cfg, store, BaselineSelector(), run_id="second", duration_s=60, transport=ex, client=public,  # type: ignore[arg-type]
                 clock=lambda: clock["t"], sleep=lambda s: None, require_clean_tree=False)
    assert len(ex.calls) == before


# ------------------------------------------------------------------------------------------- the session
def test_live_session_trades_to_the_end_flat_and_reconciled(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    cfg, clock, public, ex = _parts(make_cfg)
    store = JournalStore(tmp_path / "j.sqlite")
    duration = int((END - T0).total_seconds()) + 900
    rt = run_live(cfg, store, BaselineSelector(), run_id="lv", duration_s=duration, transport=ex, client=public,  # type: ignore[arg-type]
                  clock=lambda: clock["t"], sleep=_tick(clock), require_clean_tree=False, balance_check_s=600)
    posts = [p for m, p in ex.calls if m == "POST"]
    assert rt.venue.environment is Environment.LIVE and posts and all(p == "/v1/account/orders" for p in posts)
    assert not rt.ledger.positions and store.run_ended_at("lv") is not None  # TMN-flat at the end, run sealed
    assert not rt.kill.latched, [e for e in store.risk_events("lv") if e["kind"].startswith("BALANCE")]
    assert abs(ex.bal["TMN"][0] + ex.bal["TMN"][1] - rt.ledger.cash_tmn) <= Decimal(1)


def test_session_never_seals_over_open_positions_and_resume_continues(make_cfg: Callable[..., RunConfig], tmp_path: Path) -> None:
    cfg, clock, public, ex = _parts(make_cfg)
    store = JournalStore(tmp_path / "j.sqlite")
    seen_position = {"at": None}

    def sleep(s: float) -> None:
        clock["t"] += timedelta(seconds=s)

    try:
        rt = run_live(cfg, store, BaselineSelector(), run_id="lv", duration_s=4 * 3600, transport=ex, client=public,  # type: ignore[arg-type]
                      clock=lambda: clock["t"], sleep=sleep, require_clean_tree=False, code=CodeVersion(git_commit="a" * 40, dirty=False))
        ended_flat = True
        assert not rt.ledger.positions
    except LiveEndedNotFlat:
        ended_flat = False
        seen_position["at"] = clock["t"]  # type: ignore[assignment]
        assert store.run_ended_at("lv") is None and any(e["kind"] == "LIVE_ENDED_NOT_FLAT" for e in store.risk_events("lv"))
    if ended_flat:
        pytest.skip("no position open at the 4h mark on this synthetic series")
    rest = int((END - clock["t"]).total_seconds()) + 900
    rt = resume_live(cfg, store, BaselineSelector(), run_id="lv", duration_s=rest, transport=ex, client=public,  # type: ignore[arg-type]
                     clock=lambda: clock["t"], sleep=sleep, code=CodeVersion(git_commit="a" * 40, dirty=False))
    sent = Counter(p for m, p in ex.calls if m == "POST")
    assert sent and store.run_ended_at("lv") is not None and not rt.ledger.positions
    client_ids = list(ex.orders)
    assert len(client_ids) == len(set(client_ids)) == sent["/v1/account/orders"]  # no client id was ever submitted twice


# ------------------------------------------------------------------------------------------- reconciliation
def test_balance_watch_needs_confirmation_and_checks_orders_and_base_assets(tmp_path: Path) -> None:
    store = JournalStore(tmp_path / "j.sqlite")

    def watch(ledger: Ledger, ex: FakeExchange, run: str, open_orders: list[object] | None = None) -> tuple[BalanceWatch, KillSwitch]:
        kill = KillSwitch()
        return BalanceWatch(ledger=ledger, kill=kill, open_orders=lambda: open_orders or [], account=WallexAccountReader(ex), store=store,
                            run_id=run, every_s=0, tolerance=Decimal(1000)), kill

    ex = FakeExchange(Decimal(5_000_500), now=lambda: T0)
    w, kill = watch(Ledger(Decimal(5_000_000)), ex, "a")
    w(T0)
    assert not kill.latched  # within tolerance
    ex.bal["TMN"][0] = Decimal(4_900_000)
    w(T0)
    assert not kill.latched  # first sighting only
    w(T0)
    assert kill.latched and any(e["kind"] == "BALANCE_MISMATCH" for e in store.risk_events("a"))

    busy, kill_b = watch(Ledger(Decimal(5_000_000)), FakeExchange(Decimal(1), now=lambda: T0), "b", open_orders=[object()])
    busy(T0)
    busy(T0)
    assert not kill_b.latched  # an order in flight: no check

    ghost, kill_g = watch(Ledger(Decimal(5_000_000)), FakeExchange(Decimal(5_000_000), now=lambda: T0, open_orders=1), "g")
    ghost(T0)
    ghost(T0)
    assert kill_g.latched  # an exchange order the engine does not know

    led = Ledger(Decimal(5_000_000))
    led.positions["BTCTMN"] = Position(position_id="p", symbol="BTCTMN", qty=Decimal("0.01"), cost_tmn=Decimal(1), opened_at=T0,
                                       decision_id="d", strategy_version="v", plan=None)
    base_ex = FakeExchange(Decimal(5_000_000), now=lambda: T0)
    base_ex.bal["BTC"] = [Decimal("0.0099"), Decimal(0)]  # 1% short: e.g. a base-asset fee the mapping missed
    base, kill_base = watch(led, base_ex, "c")
    base(T0)
    base(T0)
    assert kill_base.latched

    failing, kill_f = watch(Ledger(Decimal(0)), FakeExchange(Decimal(0), now=lambda: T0, fail_reads=True), "f")
    failing(T0)
    failing(T0)
    assert not kill_f.latched
    failing(T0)
    assert kill_f.latched


def test_live_release_requires_committed_tagged_code(tmp_path: Path) -> None:
    def git(*a: str) -> None:
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "c")
    assert live_release_problems(tmp_path) == ["HEAD has no live-YYYYMMDD-N tag"]
    git("tag", "live-20261023-1")
    assert live_release_problems(tmp_path) == []
    (tmp_path / "src" / "new.py").write_text("y = 2\n")
    assert any("untracked" in p for p in live_release_problems(tmp_path))
