"""Small persisted fixtures; literal P&L expectations live in tests, not this writer."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from wallex_agent.contracts import CodeVersion, Environment, AutonomyMode, RunManifest
from wallex_agent.contracts.hashing import canonical_json, content_id, sha256_hex
from wallex_agent.journal.store import JournalStore
from wallex_agent.portfolio.ledger import Fill, Ledger

T = datetime(2026, 10, 20, 8, tzinfo=UTC)


def fixture(path: Path, env: str = "BACKTEST", *, closed: bool = True, second: bool = True) -> None:
    store = JournalStore(path)
    manifest = RunManifest(run_id="sample", started_at=T, environment=Environment(env), autonomy=AutonomyMode.AUTONOMOUS,
        code=CodeVersion(git_commit="7" * 40, dirty=False), config_sha256="a" * 64, limits_version="test",
        prompt_bundle_sha256=None, model_id=None, model_settings_sha256=None, dependency_lock_sha256="b" * 64,
        starting_capital_tmn="1000", universe_version="test", clock="WALL" if env != "BACKTEST" else "VIRTUAL",
        seed=0, strategy_versions=("fixture-1",))
    store.record_run(manifest)
    store.put_ref("sample", "run_config", {"run": {"environment": env, "autonomy": "AUTONOMOUS", "symbols": ["BTCTMN"]},
                                           "costs": {"fee_rate": "0.01"}, "strategy": {"active": "fixture"}})
    ledger = Ledger(Decimal(1000))
    seq, prev = 0, "0" * 64

    def state(kind: str, data: dict[str, Any], at: datetime) -> None:
        nonlocal seq, prev
        seq += 1
        body = canonical_json({"v": 1, "kind": kind, "at": at, "data": data}).decode()
        digest = sha256_hex((prev + body).encode())
        store.append_state("sample", seq, at.isoformat(), kind, body, prev, digest)
        prev = digest

    state("RISK", {"peak_equity_tmn": "1000", "max_drawdown_fraction": "0", "ai_invalid_streak": 0,
                   "drawdown_pause_until": None}, T)
    operations = [("BUY", "2", "100", "2"), ("SELL", "2", "110", "2.2")]
    if not closed:
        operations = operations[:1]
    elif second:
        operations += [("BUY", "1", "100", "1"), ("SELL", "1", "90", "0.9")]
    source = {"BACKTEST": "BACKTEST_SIM", "SHADOW": "SHADOW_SIM", "LIVE": "EXCHANGE"}[env]
    for n, (side, qty, price, fee) in enumerate(operations):
        at = T + timedelta(minutes=n + 1)
        cid = f"order-{n}"
        raw: dict[str, Any] = {"intent_id": cid, "decision_id": "entry" if n < 2 else "entry2", "client_order_id": cid,
            "symbol": "BTCTMN", "side": side, "order_type": "LIMIT", "qty": qty, "limit_price": price,
            "purpose": "ENTRY" if side == "BUY" else "EXIT_TIME", "environment": env, "autonomy": "AUTONOMOUS", "created_at": at}
        intent = {**raw, "intent_hash": content_id(raw)}
        state("ORDER", {"intent": intent, "plan": None, "strategy_version": "fixture-1", "verdict": {}}, at)
        for order_state, cumulative in [("INTENT", "0"), ("SUBMITTING", "0")]:
            state("STATE", {"cid": cid, "state": order_state, "cum_filled_qty": cumulative, "raw": None}, at)
            store.record_event("sample", _event(cid, order_state, cumulative, source, at), {"symbol": "BTCTMN", "side": side})
        trade = ledger.apply_fill(Fill("BTCTMN", side, Decimal(qty), Decimal(price), Decimal(fee), "TMN", at, cid,
                                      str(raw["purpose"]), str(raw["decision_id"]), "fixture-1"), plan=None)
        state("FILL", {"cid": cid, "fill_id": f"fill-{n}", "qty": qty, "price": price, "fee": fee, "fee_asset": "TMN",
                       "at": at.isoformat(), "dust_qty": "0", "cash_after": str(ledger.cash_tmn),
                       "position_qty_after": str(ledger.positions["BTCTMN"].qty) if ledger.positions else "0"}, at)
        state("STATE", {"cid": cid, "state": "FILLED", "cum_filled_qty": qty, "raw": "FILLED"}, at)
        store.record_event("sample", _event(cid, "FILLED", qty, source, at), {"symbol": "BTCTMN", "side": side})
        if trade:
            store.record_trade("sample", trade)
    for n, value in enumerate(["1000", "1100", "880", str(ledger.cash_tmn if closed else Decimal("1018"))]):
        store.record_equity("sample", T + timedelta(minutes=10 + n), value)
    state("CHECKPOINT", {"reason": "SHUTDOWN", "digest": {
        "cash": format(ledger.cash_tmn.normalize(), "f"), "realized": format(ledger.realized_pnl_tmn.normalize(), "f"),
        "fees": format(ledger.fees_paid_tmn.normalize(), "f"), "reservations": {},
        "positions": {s: {"id": p.position_id, "qty": format(p.qty.normalize(), "f"), "cost": format(p.cost_tmn.normalize(), "f"), "exit": None}
                      for s, p in ledger.positions.items()}, "dust": {}, "trades": len(ledger.trades)}}, T + timedelta(minutes=13))
    store.seal_run("sample", T + timedelta(minutes=13))
    store.close()


def _event(cid: str, state: str, qty: str, source: str, at: datetime) -> Any:
    from wallex_agent.contracts import ExecutionEvent, FillSource, OrderState
    return ExecutionEvent(event_id=f"{cid}-{state}", intent_id=cid, client_order_id=cid, state=OrderState(state),
                          exchange_status_raw=None, cum_filled_qty=Decimal(qty), source=FillSource(source), at=at)
