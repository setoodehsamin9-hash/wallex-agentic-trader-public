"""Canonical, network-free observable result. Missing values stay None (JSON null).

Financial values are Decimal. Fees are already incorporated into realized trade P&L and are
reported separately, never deducted again. Equity/drawdown use recorded valuations only.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Any

from wallex_agent.contracts import ExecutionEvent, RiskVerdict

from .reconstruct import Reconstruction, reconstruct
from .source import EvidenceError, RunEvidence, number, obj, parse, timestamp

ZERO = Decimal(0)
TERMINAL = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED"})
EXPECTED_SOURCE = {"BACKTEST": "BACKTEST_SIM", "SHADOW": "SHADOW_SIM", "LIVE": "EXCHANGE"}


def execution_events(source: RunEvidence) -> list[ExecutionEvent]:
    """Standalone events and embedded decision events overlap; count an event_id exactly once."""
    events: dict[str, ExecutionEvent] = {}
    raw = [ExecutionEvent.model_validate(e["event"]) for e in source.events]
    raw += [e for d in source.decisions for e in d.execution_events]
    for event in raw:
        prior = events.get(event.event_id)
        if prior is not None and prior != event:
            raise EvidenceError("same event id has conflicting persisted values")
        events[event.event_id] = event
    return list(events.values())


def _check_identity(source: RunEvidence, events: list[ExecutionEvent]) -> None:
    m = source.manifest
    config = source.references.get("run_config", {}).get("run", {})
    for name in ("environment", "autonomy"):
        value = config.get(name)
        if value is not None and value != getattr(m, name):
            raise EvidenceError("config/manifest environment or autonomy mismatch")
        if source.status is not None and source.status.get(name, getattr(m, name)) != getattr(m, name):
            raise EvidenceError("status/manifest environment or autonomy mismatch")
    if source.status is not None and source.status.get("run_id", m.run_id) != m.run_id:
        raise EvidenceError("status belongs to a different run")
    for decision in source.decisions:
        if decision.run_id != m.run_id or decision.environment != m.environment or decision.autonomy != m.autonomy:
            raise EvidenceError("decision from a different run/environment/autonomy")
        if decision.config_sha256 != m.config_sha256 or decision.code != m.code:
            raise EvidenceError("decision provenance differs from run manifest")
        context_key = f"ctx:{decision.decision_id}"
        context = source.references.get(context_key)
        if context is not None:
            if source.source_hashes.get(f"ref:{context_key}") != decision.features_ref:
                raise EvidenceError("decision context does not match its recorded payload reference")
            if context["snapshot"]["snapshot_id"] != decision.snapshot_id or context["snapshot"]["symbol"] != decision.symbol:
                raise EvidenceError("decision context snapshot is unrelated to the decision")
            expected = [(c.candidate_id, c.strategy_id, c.strategy_version, c.symbol) for c in decision.candidates]
            actual = [(c["candidate_id"], c["strategy_id"], c["strategy_version"], c["symbol"]) for c in context["candidates"]]
            if actual != expected:
                raise EvidenceError("decision candidate summaries disagree with stored context")
        if decision.order_intent is not None:
            if decision.order_intent.environment != m.environment or decision.order_intent.autonomy != m.autonomy:
                raise EvidenceError("decision intent environment/autonomy mismatch")
    expected = EXPECTED_SOURCE[m.environment]
    if any(e.source != expected for e in events):
        raise EvidenceError("simulation/exchange source contamination; refusing financial report")
    for event in source.risk_events:
        data = obj(event["data"])
        if "venue_source" in data and data["venue_source"] != expected:
            raise EvidenceError("recovery venue source differs from manifest")
        for name in ("environment", "autonomy"):
            if name in data and data[name] != getattr(m, name):
                raise EvidenceError("audit environment/autonomy mismatch")


def _write_evidence(source: RunEvidence, replay: Reconstruction | None, events: list[ExecutionEvent]) -> tuple[int | str, dict[str, Any]]:
    # A lifecycle is not a write audit: repeated OPEN/PARTIAL records are not more submissions,
    # and cancel calls can be absent entirely. Never present these as a complete LIVE count.
    persisted = [(e.client_order_id, e.state.value, e.cum_filled_qty) for e in events]
    logged = replay.state_events if replay is not None else []
    matches = bool(logged) and Counter(logged) == Counter(persisted)
    orders: set[str] = set(replay.orders) if replay is not None else set()
    submitted = {e.client_order_id for e in events if e.state.value == "SUBMITTING"}
    evidence: dict[str, Any] = {"method": "state-chain/execution-source cross-check v1",
        "matched_state_events": len(logged) if matches else 0, "orders": len(orders),
        "sources": sorted({e.source.value for e in events}), "complete_operation_audit": False}
    if source.manifest.environment.value in ("BACKTEST", "SHADOW") and matches and orders == submitted:
        evidence["reason"] = "Every persisted order/state is matched to this run's simulator source; scope is the observed journal prefix."
        return 0, evidence
    evidence["reason"] = ("Legacy exchange lifecycle does not audit every write/cancel operation."
                          if source.manifest.environment.value == "LIVE" else "Missing or unmatched persisted simulator/order evidence.")
    return "UNKNOWN", evidence


def _inspect_run(source: RunEvidence) -> dict[str, Any]:
    """Inspect exactly one run. No environment override or manually supplied write count exists."""
    m = source.manifest
    events = execution_events(source)
    _check_identity(source, events)
    capital = number(m.starting_capital_tmn) if m.starting_capital_tmn is not None else None
    if capital is not None and capital <= 0:
        raise EvidenceError("starting capital must be positive")
    if source.states and capital is None:
        raise EvidenceError("state replay needs persisted starting capital")
    replay = reconstruct(source, capital) if capital is not None else None
    ledger = replay.ledger if replay is not None and replay.available else None
    decision_evidence = "decisions" in source.available_tables
    execution_evidence = "exec_events" in source.available_tables
    fill_times = [e.at for e in events if e.cum_filled_qty > 0]
    if replay is not None and replay.last_fill_at is not None:
        fill_times.append(replay.last_fill_at)
    last_fill_observed_at = max(fill_times) if fill_times else None
    notes: list[str] = []
    if ledger is None:
        notes.append("No complete ledger reconstruction evidence; account/position metrics are undefined.")
    valuations: list[tuple[datetime, Decimal]] = []
    for point in source.equity:
        at, eq = timestamp(point["at"]), number(point["equity_tmn"])
        if at < m.started_at or (source.ended_at is not None and at > source.ended_at):
            raise EvidenceError("equity point outside recorded run period")
        valuations.append((at, eq))
    if valuations != sorted(valuations, key=lambda x: x[0]):
        raise EvidenceError("equity journal is out of order")
    equity: Decimal | None = None
    valuation_at: str | None = None
    unreal: Decimal | None = None
    exposure: Decimal | None = None
    if ledger is not None:
        if not ledger.positions:
            equity, unreal, exposure = ledger.cash_tmn, ZERO, ledger.reserved_tmn
        elif valuations and replay is not None and (last_fill_observed_at is None or valuations[-1][0] > last_fill_observed_at):
            equity = valuations[-1][1]
            valuation_at = valuations[-1][0].isoformat()
            unreal = equity - ledger.cash_tmn - sum((p.cost_tmn for p in ledger.positions.values()), ZERO)
            notes.append("Open equity/unrealized P&L use the last persisted liquidation valuation (rounded TMN); gross exposure is unavailable.")
    status = source.status or {}
    status_at = timestamp(status["updated_at"]) if status.get("updated_at") else None
    status_fresh = (ledger is not None and replay is not None and status_at is not None
                    and (last_fill_observed_at is None or status_at >= last_fill_observed_at))
    if status_fresh and status_at is not None and ledger is not None and "positions" in status and "cash_tmn" in status:
        recorded_positions = {p["symbol"]: number(p["qty"]) for p in status["positions"]}
        if recorded_positions != {sym: p.qty for sym, p in ledger.positions.items()} or abs(number(status["cash_tmn"]) - ledger.cash_tmn) > 1:
            raise EvidenceError("fresh status positions/cash disagree with the reconstructed ledger")
        if "exposure_tmn" in status:
            exposure = number(status["exposure_tmn"])
        if "equity_tmn" in status and (not valuations or status_at >= valuations[-1][0]):
            recorded_equity = number(status["equity_tmn"])
            if ledger.positions:
                equity = recorded_equity
                unreal = equity - ledger.cash_tmn - sum((p.cost_tmn for p in ledger.positions.values()), ZERO)
            elif abs(recorded_equity - ledger.cash_tmn) > 1:
                raise EvidenceError("fresh status equity disagrees with a flat ledger")
            valuation_at = status_at.isoformat()
            valuations.append((status_at, recorded_equity))
    peak: Decimal | None = capital if valuations else None
    drawdown: Decimal | None = ZERO if valuations else None
    for _, value in valuations:
        peak = max(peak, value) if peak is not None else value
        dd = (peak - value) / peak * 100 if peak > 0 else None
        if dd is not None:
            drawdown = max(drawdown, dd) if drawdown is not None else dd
    if replay is not None and replay.last_risk:
        risk_state = replay.last_risk
        if "peak_equity_tmn" in risk_state:
            recorded_peak = number(risk_state["peak_equity_tmn"])
            peak = max(peak, recorded_peak) if peak is not None else recorded_peak
        if "max_drawdown_fraction" in risk_state:
            recorded_dd = number(risk_state["max_drawdown_fraction"]) * 100
            if not ZERO <= recorded_dd <= 100:
                raise EvidenceError("invalid recorded drawdown")
            drawdown = max(drawdown, recorded_dd) if drawdown is not None else recorded_dd
    recorded_trades = source.trades
    for trade in recorded_trades:
        if timestamp(trade["closed_at"]) < timestamp(trade["opened_at"]):
            raise EvidenceError("trade closes before it opens")
        number(trade["net_pnl_tmn"])
        number(trade["fees_tmn"])
    trade_evidence = ledger is not None or bool(recorded_trades)
    pnls = [number(t["net_pnl_tmn"]) for t in recorded_trades]
    wins = sum(1 for x in pnls if x > 0) if trade_evidence else None
    losses = sum(1 for x in pnls if x < 0) if trade_evidence else None
    profit = sum((x for x in pnls if x > 0), ZERO)
    loss = -sum((x for x in pnls if x < 0), ZERO)
    risk_verdicts: dict[str, Any] = {d.risk_verdict.verdict_id: d.risk_verdict for d in source.decisions if d.risk_verdict is not None}
    # Protective exits are persisted in ORDER even when there is no AI decision for that exit.
    for state in source.states:
        if state["kind"] == "ORDER":
            verdict_data = parse(state["body"])["data"].get("verdict")
            if verdict_data and "verdict_id" in verdict_data:
                v = RiskVerdict.model_validate(verdict_data)
                if v.verdict_id in risk_verdicts and risk_verdicts[v.verdict_id] != v:
                    raise EvidenceError("conflicting persisted risk verdict")
                risk_verdicts[v.verdict_id] = v
    risk_counts = Counter(v.outcome.value for v in risk_verdicts.values())
    blocks = Counter(c.name for v in risk_verdicts.values() for c in v.checks if not c.passed)
    llm = [d for d in source.decisions if d.provenance is not None and d.provenance.source.value == "LLM"]
    model_errors = Counter(d.validated.status.value for d in llm if d.validated is not None and not d.validated.status.answered)
    latencies = sorted(d.provenance.latency_ms for d in llm if d.provenance is not None)
    orders: dict[str, dict[str, Any]] = {}
    if replay is not None:
        for cid, data in replay.orders.items():
            orders[cid] = {k: str(v) if isinstance(v, Decimal) else v for k, v in data.items()}
    reconstructed_orders = set(orders)
    for event in events:
        cid = event.client_order_id
        if cid not in reconstructed_orders:
            orders[cid] = {"client_order_id": cid, "state": event.state.value, "filled": str(event.cum_filled_qty)}
        orders[cid]["event_state"] = event.state.value
    open_orders = [v for _, v in sorted(orders.items()) if v["state"] not in TERMINAL or v.get("event_state", v["state"]) not in TERMINAL]
    unknown = [v for v in open_orders if "UNKNOWN" in (v["state"], v.get("event_state"))]
    restarts = [r for r in source.risk_events if r["kind"] == "RECOVERY_STARTED"]
    recovery = [r for r in source.risk_events if r["kind"].startswith(("RECOVERY_", "RESTART_"))]
    news_refs = [v for k, v in source.references.items() if k.startswith("news:")]
    news_contexts = [v for v in news_refs if "as_of" in v and "enabled" in v]
    latest_news = max(news_contexts, key=lambda v: timestamp(v["as_of"])) if news_contexts else None
    news_events = [r for r in source.risk_events if r["kind"].startswith("NEWS_")]
    real_writes, write_proof = _write_evidence(source, replay, events)
    breaker = None
    if replay is not None and replay.last_risk is not None:
        breaker = {k: replay.last_risk[k] for k in ("drawdown_pause_until", "ai_invalid_streak", "pause_trigger_drawdown")
                   if k in replay.last_risk}
    if status.get("entry_gate"):
        breaker = {**(breaker or {}), "entry_gate": status["entry_gate"]}
    kill = status.get("kill_switch")  # global kill table belongs to the host, never attribute it to a historical run
    attention = bool(unknown or kill or status.get("entry_gate"))
    seen = [m.started_at, *(d.recorded_at for d in source.decisions), *(e.at for e in events), *(a for a, _ in valuations)]
    seen += [timestamp(r["at"]) for r in source.risk_events if r["at"] is not None]
    if status.get("updated_at"):
        seen.append(timestamp(status["updated_at"]))
    if source.ended_at:
        seen.append(source.ended_at)
    health = "ATTENTION" if attention else "RECORDED_STOPPED" if source.ended_at else "UNKNOWN"
    if ledger is None and not events and not source.decisions and not attention:
        health = "UNKNOWN"
    if not source.ended_at and status.get("active") is True and not attention:
        health = "RECORDED_ACTIVE"  # no claim about current liveness or private exchange state
    net = equity - capital if equity is not None and capital is not None else None
    symbols = set(source.references.get("run_config", {}).get("run", {}).get("symbols", []))
    symbols.update(d.symbol for d in source.decisions if d.symbol)
    symbols.update(t["symbol"] for t in recorded_trades)
    if replay is not None:
        symbols.update(o["symbol"] for o in replay.orders.values())
    return {"schema_version": "observability-1", "run_id": m.run_id, "environment": m.environment.value,
        "autonomy": m.autonomy.value, "activity_kind": "EXCHANGE" if m.environment.value == "LIVE" else "SIMULATED",
        "started_at": m.started_at.isoformat(), "ended_at": source.ended_at.isoformat() if source.ended_at else None,
        "observed_at": max(seen).isoformat(), "status_at": status.get("updated_at"), "health": health,
        "active_recorded": status.get("active"), "model_id": m.model_id, "model_provider": m.model_provider,
        "responding_models": sorted({d.provenance.model_id for d in source.decisions if d.provenance and d.provenance.model_id}),
        "symbols": sorted(symbols), "starting_capital_tmn": capital, "final_equity_tmn": equity,
        "valuation_at": valuation_at,
        "cash_tmn": ledger.cash_tmn if ledger else None, "realized_pnl_tmn": ledger.realized_pnl_tmn if ledger else None,
        "unrealized_pnl_tmn": unreal, "fees_tmn": ledger.fees_paid_tmn if ledger else None, "net_pnl_tmn": net,
        "return_pct": net / capital * 100 if net is not None and capital is not None else None,
        "peak_equity_tmn": peak, "max_drawdown_pct": drawdown, "equity_basis": "recorded liquidation valuations; equity table may be rounded",
        "trades": len(pnls) if trade_evidence else None, "wins": wins, "losses": losses,
        "win_rate_pct": Decimal(wins) / len(pnls) * 100 if pnls and wins is not None else None,
        "profit_factor": profit / loss if loss else None,
        "avg_trade_pnl_tmn": sum(pnls, ZERO) / len(pnls) if pnls else None,
        "open_positions": [{"position_id": p.position_id, "symbol": s, "qty": str(p.qty), "cost_tmn": str(p.cost_tmn),
                            "decision_id": p.decision_id} for s, p in sorted(ledger.positions.items())] if ledger else None,
        "dust": {k: str(v) for k, v in ledger.dust.items()} if ledger else None, "exposure_tmn": exposure,
        "open_orders": open_orders if events or ledger else None, "unknown_orders": unknown if events or ledger else None,
        "decisions_total": len(source.decisions) if "decisions" in source.available_tables else None,
        "wait_decisions": sum(d.effective_action.value == "WAIT" for d in source.decisions) if "decisions" in source.available_tables else None,
        "candidates_produced": sum(len(d.candidates) for d in source.decisions) if "decisions" in source.available_tables else None,
        "risk_counts": {k: risk_counts[k] for k in ("APPROVE", "REDUCE", "REJECT")} if decision_evidence else None,
        "risk_blocks": dict(sorted(blocks.items())) if decision_evidence else None,
        "risk_count_basis": "unique persisted decision and ORDER verdicts; absent decisions table means incomplete coverage",
        "kill_switch_latched": kill, "breaker": breaker, "entries_allowed_recorded": status.get("entries_allowed"),
        "window_reason": status.get("window_reason"), "ai_calls": len(llm) if decision_evidence else None,
        "ai_invalid_by_status": dict(sorted(model_errors.items())) if decision_evidence else None,
        "model_timeouts": model_errors["TIMEOUT"] if decision_evidence else None,
        "model_failures": sum(model_errors.values()) if decision_evidence else None,
        "model_status": llm[-1].validated.status.value if llm and llm[-1].validated else None,
        "ai_latency_ms_p50": latencies[(len(latencies) - 1) // 2] if latencies else None,
        "ai_latency_ms_max": max(latencies) if latencies else None,
        "execution_errors": status.get("execution_errors") if status_fresh and "execution_errors" in status else
                            sum(e.state.value == "REJECTED" for e in events) if execution_evidence else None,
        "execution_error_basis": "recorded runtime counter" if status_fresh and "execution_errors" in status else "observed REJECTED execution events",
        "data_status": source.decisions[-1].status.value if source.decisions else None,
        "news_context_count": len(news_contexts) if news_contexts else None, "news_event_count": len(news_events) if news_events else None,
        "news_veto_count": sum(r["kind"] == "NEWS_VETO" or r["data"].get("action") == "BLOCK_NEW_ENTRIES" for r in news_events) if news_events else None,
        "news_status": latest_news,
        "restart_count": len(restarts) if "risk_events" in source.available_tables else None,
        "recovery_event_count": len(recovery) if "risk_events" in source.available_tables else None,
        "real_wallex_write_count": real_writes, "write_count_evidence": write_proof,
        "observed_exchange_submissions": len({e.client_order_id for e in events if e.source.value == "EXCHANGE" and e.state.value == "SUBMITTING"}) if execution_evidence else None,
        "state_records": len(source.states), "checkpoints_verified": replay.checkpoints_verified if replay else None,
        "notes": notes}


def inspect_run(source: RunEvidence) -> dict[str, Any]:
    """Expose safe failures, even if a malformed payload would make a contract print its input."""
    try:
        return _inspect_run(source)
    except EvidenceError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, ArithmeticError):
        raise EvidenceError("persisted evidence is malformed or incomplete; observation refused") from None
