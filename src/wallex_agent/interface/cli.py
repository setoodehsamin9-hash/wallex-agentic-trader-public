"""Command-line surface. There is deliberately NO manual buy/sell command: operators can only approve/reject an
algorithm-built ticket, latch/reset the kill switch, inspect state and run backtest/shadow/replay/export.

  wallex-agent backtest --config config/backtest.toml [--split TRAIN|VALIDATION|TEST|ALL] [--decider BASELINE|LLM|REPLAY]
  wallex-agent shadow   --config config/shadow.toml --duration 3600 [--decider BASELINE|LLM] [--resume RUN_ID]
  wallex-agent recover RUN_ID [--db state.sqlite]   (read-only restart-recovery inspection; resume with shadow --resume)
  wallex-agent abandon RUN_ID --reason R --operator NAME   (seal an unfinished SHADOW/BACKTEST run instead of resuming)
  wallex-agent run      --env LIVE ...            (refused in Phase 03; prints what is missing)
  wallex-agent status   |  approve TICKET  |  reject TICKET  |  kill --reason R  |  reset-kill --reason R --operator NAME
  wallex-agent replay RUN_ID --config ...   |  ask "سوال"   |  preflight --config ...   |  eval-ninerouter
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from wallex_agent.contracts import AutonomyMode, Environment
from wallex_agent.policy import ActionKind, PolicyContext, decide
from wallex_agent.tools import Consumer, exposed_tools

STATE = Path(os.environ.get("WALLEX_AGENT_STATE_DIR", "var"))
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


def _now() -> datetime:
    return datetime.now(UTC)


def _store(name: str = "state.sqlite") -> Any:
    from wallex_agent.journal.store import JournalStore

    return JournalStore(STATE / name)


def _policy_matrix() -> None:
    for env in Environment:
        print(f"\n== {env} (live_autonomous_enabled=False, kill=False)")
        for mode in AutonomyMode:
            ctx = PolicyContext(mode, env, kill_switch_latched=False, live_autonomous_enabled=False)
            print(f"  {mode.value:<18} " + ", ".join(f"{a.value}={decide(a, ctx).outcome.value}" for a in ActionKind))


def _tool_exposure() -> None:
    for env in Environment:
        print(f"\n== {env}")
        for consumer in Consumer:
            names = sorted(exposed_tools(consumer, env))
            print(f"  {consumer.value:<20} {len(names):>2}  {', '.join(names) if names else '-'}")


def _load_for_report(path: str) -> Any:
    """Parse without the LIVE start check: for commands that only REPORT (readiness, screen); `run` keeps the check."""
    from wallex_agent.runtime.config import load_run_config

    return load_run_config(Path(path), enforce_live=False)


def _load(path: str, **overrides: Any) -> Any:
    from wallex_agent.runtime.config import RunConfig, load_run_config

    cfg = load_run_config(Path(path))
    if not overrides:
        return cfg
    data = cfg.model_dump(mode="python")
    for dotted, value in overrides.items():
        if value is None:
            continue
        node = data
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
    return RunConfig.model_validate(data)


def _export(store: Any, run_id: str, summary: Any, context: dict[str, Any], out: Path | None) -> Path:
    from wallex_agent.journal.export import export_run

    store.put_ref(run_id, "summary", summary.model_dump(mode="json"))
    store.put_ref(run_id, "context", context)
    return export_run(store, run_id, out or Path("runs/published") / run_id, summary=summary, context=context)


def cmd_backtest(a: argparse.Namespace) -> int:
    from wallex_agent.market.dataset import load_dataset
    from wallex_agent.runtime.backtest import run_backtest
    from wallex_agent.runtime.compose import build_decider
    from wallex_agent.runtime.reporter import ConsoleReporter

    cfg = _load(a.config, **{"backtest.split": a.split, "agent.decision_provider": a.decider, "agent.replay_run_id": a.replay_of,
                             "run.autonomy": AutonomyMode(a.autonomy) if a.autonomy else None})
    store = _store("backtest.sqlite")
    decider, meta = build_decider(cfg, store)
    run_id = a.run_id or f"bt-{_now():%Y%m%dT%H%M%S}-{cfg.agent.decision_provider.lower()}-{cfg.backtest.split.lower()}"
    rep = ConsoleReporter(verbose=a.verbose, log_path=STATE / f"{run_id}.events.jsonl")
    ds = load_dataset(Path("data/datasets") / cfg.backtest.dataset)
    res = run_backtest(cfg, ds, store, decider, run_id=run_id, reporter=rep, model_meta=meta)
    s = res.summary
    print(json.dumps({"run_id": run_id, "period": [p.isoformat() for p in res.period], "trades": s.trades, "wins": s.wins,
                      "realized_pnl_tmn": str(s.realized_pnl_tmn), "fees_tmn": str(s.fees_tmn), "return_pct": str(s.return_pct),
                      "max_drawdown_pct": str(s.max_drawdown_pct), "ai_calls": s.ai_calls, "ai_valid": s.ai_valid,
                      "decisions": s.decisions_total}, ensure_ascii=False, indent=1))
    if a.export:
        print("exported:", _export(store, run_id, s, res.context, None))
    return 0


def cmd_shadow(a: argparse.Namespace) -> int:
    from wallex_agent.reporting.performance import compute_summary
    from wallex_agent.runtime.compose import build_decider
    from wallex_agent.runtime.reporter import ConsoleReporter
    from wallex_agent.recovery.coordinator import RecoveryBlocked
    from wallex_agent.runtime.engine import RecoveryRequired
    from wallex_agent.runtime.shadow import resume_shadow, run_shadow

    cfg = _load(a.config, **{"agent.decision_provider": a.decider, "run.autonomy": AutonomyMode(a.autonomy) if a.autonomy else None})
    if cfg.run.environment is not Environment.SHADOW:
        print("config environment must be SHADOW", file=sys.stderr)
        return 2
    store = _store()
    decider, meta = build_decider(cfg, store)
    run_id = a.resume or a.run_id or f"sh-{_now():%Y%m%dT%H%M%S}-{cfg.agent.decision_provider.lower()}"
    rep = ConsoleReporter(verbose=True, log_path=STATE / f"{run_id}.events.jsonl")
    try:
        if a.resume:
            rt = resume_shadow(cfg, store, decider, run_id=run_id, duration_s=a.duration, reporter=rep, model_meta=meta,
                               summary_every_s=a.summary_every, on_runtime=lambda r: print(r.recovery.summary_fa() if r.recovery else ""))
        else:
            rt = run_shadow(cfg, store, decider, run_id=run_id, duration_s=a.duration, reporter=rep, model_meta=meta,
                            summary_every_s=a.summary_every)
    except RecoveryBlocked as exc:
        print(exc.report.summary_fa())
        return 3
    except RecoveryRequired as exc:
        unfinished = [m.run_id for m in store.unfinished_runs() if m.environment is Environment.SHADOW]
        print(f"{exc}\nاجرای ناتمام: {unfinished or '—'} · بررسی: wallex-agent recover RUN_ID · ادامه: wallex-agent shadow --resume RUN_ID",
              file=sys.stderr)
        return 3
    s = compute_summary(run_id=run_id, environment="SHADOW", autonomy=cfg.run.autonomy.value, label=cfg.run.label,
                        starting_capital_tmn=cfg.run.starting_capital_tmn, ledger=rt.ledger, marks=rt.marks(), fee_rate=cfg.costs.fee_rate,
                        decisions=store.decisions(run_id), execution_errors=rt.execution.error_count,
                        unresolved_orders=len(rt.execution.unresolved()), kill_latched=rt.kill.latched, model_id=meta.get("model_id"),
                        model_provider=meta.get("provider"), strategy_versions=[rt.strategy.version],
                        period=(rt.started_at.isoformat() if rt.started_at else None, _now().isoformat()))
    rep("SUMMARY", store.get_status(run_id) or rt.status_summary(_now(), active=False, ninerouter=meta.get("provider")))
    context = {"strategy": rt.strategy.version, "model_route": meta.get("route"), "caveats": [
        "Shadow: داده زنده عمومی والکس، سفارش‌ها فقط شبیه‌سازی (هیچ سفارش واقعی ثبت نشده).",
        "پرشدن سفارش با عمق قابل‌مشاهده همان لحظه شبیه‌سازی شده (IOC)؛ صف و تأخیر مدل نشده است.",
        f"مدت اجرای کوتاه ({a.duration} ثانیه)؛ برای قضاوت درباره سودآوری کافی نیست.",
        f"کارمزد فرضی {cfg.costs.fee_rate} هر طرف؛ نرخ واقعی حساب هنوز خوانده نشده است."]}
    print("exported:", _export(store, run_id, s, context, None))
    print(f"real Wallex order writes: 0 (venue={rt.venue.environment.value}, submissions simulated={len(rt.venue.submitted)})")  # type: ignore[attr-defined]
    return 0


def cmd_recover(a: argparse.Namespace) -> int:
    """Read-only: verify the state log and show what restart recovery would rebuild. Adds no journal record."""
    from pydantic import ValidationError

    from wallex_agent.contracts import TERMINAL_ORDER_STATES, ApprovalStatus
    from wallex_agent.recovery.coordinator import inspect_run
    from wallex_agent.recovery.rebuild import RebuildError
    from wallex_agent.recovery.state_log import StateLogCorrupt
    from wallex_agent.runtime.config import RunConfig

    if not (STATE / a.db).is_file():
        print(f"BLOCKED: no journal at {STATE / a.db}")
        return 3
    store = _store(a.db)
    manifest, recorded = store.manifest(a.run_id), store.get_ref(a.run_id, "run_config")
    if manifest is None or recorded is None:
        print(f"BLOCKED: run {a.run_id} or its recorded config is not in {a.db}")
        return 3
    try:
        cfg = RunConfig.model_validate(recorded)
        ledger, rebuilt = inspect_run(store, a.run_id, cfg)
    except (StateLogCorrupt, RebuildError, ValidationError) as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}\nوضعیت ذخیره‌شده قابل اعتماد نیست؛ ادامهٔ این اجرا ممکن نیست.")
        return 3
    sealed = store.run_ended_at(a.run_id) is not None
    kill = store.kill_state()
    body = {"run_id": a.run_id, "environment": manifest.environment.value, "autonomy": manifest.autonomy.value,
            "sealed": sealed, "chain_ok": True, "records": rebuilt.records, "checkpoints_verified": rebuilt.checkpoints_verified,
            "cash_tmn": str(ledger.cash_tmn), "reservations": {k: str(v) for k, v in ledger.reservations.items()},
            "positions": {s: {"qty": str(p.qty), "stop_price": str(p.plan.stop_price) if p.plan else None,
                              "take_profit_price": str(p.plan.take_profit_price) if p.plan and p.plan.take_profit_price else None,
                              "deadline": p.plan.deadline.isoformat() if p.plan else None, "pending_exit": p.exit_order_id}
                          for s, p in ledger.positions.items()},
            "open_orders": {cid: r.state.value for cid, r in rebuilt.orders.items() if r.state not in TERMINAL_ORDER_STATES},
            "trades": len(ledger.trades), "kill_switch": kill.latched,
            "approvals_to_invalidate": [t.ticket_id for st in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
                                        for t in store.tickets(st, run_id=a.run_id)]}
    print(json.dumps(body, ensure_ascii=False, indent=1))
    if sealed:
        print("این اجرا بسته (sealed) شده است و ادامه داده نمی‌شود؛ برای معامله، اجرای جدید شروع کنید.")
    else:
        print(f"زنجیرهٔ وضعیت سالم است. سفارش‌های باز هنگام ادامه «نامعلوم» علامت می‌خورند و هرگز دوباره ارسال نمی‌شوند؛"
              f" تأییدهای منتظر باطل می‌شوند. ادامه: wallex-agent shadow --resume {a.run_id}")
    return 0


def cmd_abandon(a: argparse.Namespace) -> int:
    """Operator decision to NOT resume an unfinished run (e.g. code/config changed): journal who/why, then seal it.
    Refused for LIVE: real positions/orders must be reconciled and flattened, not walked away from."""
    from wallex_agent.contracts import TERMINAL_ORDER_STATES
    from wallex_agent.recovery.coordinator import inspect_run
    from wallex_agent.runtime.config import RunConfig

    if not (STATE / a.db).is_file():
        print(f"no journal at {STATE / a.db}", file=sys.stderr)
        return 3
    store = _store(a.db)
    manifest = store.manifest(a.run_id)
    if manifest is None or store.run_ended_at(a.run_id) is not None:
        print(f"run {a.run_id} does not exist or is already sealed", file=sys.stderr)
        return 3
    if manifest.environment is Environment.LIVE:
        print("LIVE runs cannot be abandoned from the CLI; reconcile and flatten through recovery first.", file=sys.stderr)
        return 3
    positions: list[str] | None = None
    orders: list[str] | None = None
    recorded = store.get_ref(a.run_id, "run_config")
    try:
        if recorded is not None:
            ledger, rebuilt = inspect_run(store, a.run_id, RunConfig.model_validate(recorded))
            positions = sorted(ledger.positions)
            orders = sorted(c for c, r in rebuilt.orders.items() if r.state not in TERMINAL_ORDER_STATES)
    except Exception as exc:  # noqa: BLE001 - abandoning must still work on unreadable state; record that it was unreadable
        print(f"state unreadable ({type(exc).__name__}); abandoning without a position list", file=sys.stderr)
    now = _now()
    store.record_risk_event(a.run_id, now, "RUN_ABANDONED", {"operator": a.operator, "reason": a.reason,
                                                            "open_positions": positions, "open_orders": orders})
    store.seal_run(a.run_id, now)
    print(f"اجرای {a.run_id} بسته شد (ادامه داده نمی‌شود). دلیل: {a.reason} · اپراتور: {a.operator} · موقعیت‌های باز شبیه‌سازی: {positions}")
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    """LIVE session, started by the owner on the run host. The key comes only from WALLEX_API_KEY in this process."""
    if a.env != "LIVE":
        print("use `wallex-agent backtest` or `wallex-agent shadow`", file=sys.stderr)
        return 2
    if not a.config:
        print("--config is required for LIVE", file=sys.stderr)
        return 2
    from wallex_agent.policy import RunConfigurationError
    from wallex_agent.runtime.compose import build_decider
    from wallex_agent.runtime.engine import RecoveryRequired
    from wallex_agent.recovery.coordinator import RecoveryBlocked
    from wallex_agent.runtime.live import LiveEndedNotFlat, LivePreflightFailed, resume_live, run_live
    from wallex_agent.runtime.reporter import ConsoleReporter

    try:
        cfg = _load(a.config)  # LIVE enforcement: every requirement + U04 gate, before anything else
    except RunConfigurationError as exc:
        print(f"LIVE start refused: {exc}", file=sys.stderr)
        return 2
    if cfg.run.environment is not Environment.LIVE:
        print("config environment must be LIVE", file=sys.stderr)
        return 2
    if not os.environ.get("WALLEX_API_KEY"):
        print("WALLEX_API_KEY is not set in this shell (set it on the run host only; never in a file of the repo)", file=sys.stderr)
        return 2
    from wallex_agent.execution.wallex_live import UrllibPrivateTransport

    assert cfg.competition.end_utc is not None
    duration = max(0, int((cfg.competition.end_utc - _now()).total_seconds()) + 900)  # always until the end: never stop holding
    store = _store()
    decider, meta = build_decider(cfg, store)
    run_id = a.resume or a.run_id or f"live-{_now():%Y%m%dT%H%M%S}"
    rep = ConsoleReporter(verbose=True, log_path=STATE / f"{run_id}.events.jsonl")
    try:
        if a.resume:
            rt = resume_live(cfg, store, decider, run_id=run_id, duration_s=duration, transport=UrllibPrivateTransport(), reporter=rep,
                             model_meta=meta, summary_every_s=a.summary_every,
                             on_runtime=lambda r: print(r.recovery.summary_fa() if r.recovery else ""))
        else:
            rt = run_live(cfg, store, decider, run_id=run_id, duration_s=duration, transport=UrllibPrivateTransport(), reporter=rep,
                          model_meta=meta, summary_every_s=a.summary_every)
    except LivePreflightFailed as exc:
        for name, ok, detail in exc.report.checks:
            print(f"{'PASS' if ok else 'FAIL'}  {name:<22} {detail}")
        print("LIVE start refused: nothing was sent to Wallex.", file=sys.stderr)
        return 3
    except RecoveryRequired as exc:
        print(f"{exc}\nunfinished: {[m.run_id for m in store.unfinished_runs()]}", file=sys.stderr)
        return 3
    except RecoveryBlocked as exc:
        print(exc.report.summary_fa(), file=sys.stderr)
        return 3
    except LiveEndedNotFlat as exc:
        print(f"NOT FLAT: {exc}", file=sys.stderr)
        return 4
    print(f"LIVE session {run_id} ended · kill switch latched={rt.kill.latched} · open positions={len(rt.ledger.positions)}")
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    from wallex_agent.observability.cli import command

    return command(a, STATE)


def cmd_approve(a: argparse.Namespace, approve: bool) -> int:
    from wallex_agent.approval.service import ApprovalError, ApprovalService

    try:
        t = ApprovalService(_store(), ttl_s=0).decide(a.ticket, approve=approve, operator=a.operator, now=_now(), note=a.note)
    except ApprovalError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(f"{t.ticket_id}: {t.status.value} by {t.decided_by} (the running engine re-checks risk before any order)")
    return 0


def cmd_kill(a: argparse.Namespace, reset: bool) -> int:
    ks = _store().kill_switch()
    if reset:
        ks.reset(a.reason, at=_now(), operator=a.operator)
        print("kill switch reset by", a.operator)
    else:
        ks.latch(a.reason, at=_now(), by=a.operator or "operator")
        print("kill switch LATCHED: no new entries; exits and liquidation continue")
    return 0


def cmd_replay(a: argparse.Namespace) -> int:
    from wallex_agent.agent.replay import ReplayDecisionProvider
    from wallex_agent.market.dataset import load_dataset
    from wallex_agent.runtime.backtest import run_backtest
    from wallex_agent.runtime.config import RunConfig

    store = _store("backtest.sqlite")
    src = store.manifest(a.run_id)
    if src is None:
        print("unknown run id", file=sys.stderr)
        return 1
    recorded_cfg = store.get_ref(a.run_id, "run_config")
    if a.config:
        cfg = _load(a.config, **{"backtest.split": a.split})
    elif recorded_cfg is not None:
        cfg = RunConfig.model_validate(recorded_cfg)
        if a.split and (cfg.backtest is None or cfg.backtest.split != a.split):
            print("replay split must match the recorded source run", file=sys.stderr)
            return 2
    else:
        print("source has no recorded config; supply --config with the original config and --split", file=sys.stderr)
        return 2
    if cfg.run.environment is not Environment.BACKTEST or cfg.backtest is None or cfg.config_sha256 != src.config_sha256:
        print("replay requires the source BACKTEST config (hash must match)", file=sys.stderr)
        return 2
    ds = load_dataset(Path("data/datasets") / cfg.backtest.dataset)
    rid = f"replay-of-{a.run_id}-{uuid4().hex[:8]}"
    provider = ReplayDecisionProvider(store, a.run_id, exit_enabled=cfg.agent.allow_exit_position, role=cfg.agent.role)
    res = run_backtest(cfg, ds, store, provider, run_id=rid)
    orig, again = store.decisions(a.run_id), store.decisions(rid)
    same_actions = [(d.symbol, d.recorded_at, d.effective_action, d.status) for d in orig] == \
        [(d.symbol, d.recorded_at, d.effective_action, d.status) for d in again]
    same_trades = [t["net_pnl_tmn"] for t in store.trades(a.run_id)] == [t["net_pnl_tmn"] for t in store.trades(rid)]
    print(json.dumps({"replay_run": rid, "decisions": len(again), "identical_decisions": same_actions,
                      "identical_trades": same_trades, "missing_recordings": provider.missing,
                      "realized_pnl_tmn": str(res.summary.realized_pnl_tmn)}, indent=1))
    return 0 if same_actions and same_trades and provider.missing == 0 else 1


def cmd_ask(a: argparse.Namespace) -> int:
    from wallex_agent.agent.assistant import ask
    from wallex_agent.agent.ninerouter import NineRouterNotConfigured, build_model, load_settings, resolve_api_key

    try:
        settings = load_settings(model_id=a.model or os.environ.get("NINEROUTER_MODEL"), timeout_s=90)
        model = build_model(settings, resolve_api_key())
    except NineRouterNotConfigured as exc:
        print(f"دستیار به NineRouter نیاز دارد: {exc}\nبه‌جای آن از `wallex-agent status` استفاده کنید.", file=sys.stderr)
        return 2
    store = _store(a.db)
    ans = ask(model, store, a.question, run_id=a.run_id)
    print(ans.answer_fa)
    if ans.cited_decision_ids:
        print("\n(استناد: " + ", ".join(ans.cited_decision_ids) + ")")
    return 0


def cmd_readiness(a: argparse.Namespace) -> int:
    """Deterministic readiness gate (no network, no model). Owner inputs are optional files written by the owner."""
    import tomllib

    from wallex_agent.observability.bundle import verify_bundle
    from wallex_agent.observability.source import EvidenceError
    from wallex_agent.runtime.readiness import AccountEvidence, AiEvalEvidence, OwnerAttestations, readiness_gate

    cfg = _load_for_report(a.config)  # the gate REPORTS missing LIVE inputs; it never starts anything
    att = OwnerAttestations.model_validate(tomllib.loads(Path(a.attestations).read_text())) if a.attestations else None
    acct = AccountEvidence.model_validate_json(Path(a.account).read_text()) if a.account else None
    qa = json.loads(Path(a.ai_qa).read_text()).get("passed") is True if a.ai_qa else None
    ai_eval = AiEvalEvidence.model_validate_json(Path(a.ai_eval).read_text()) if a.ai_eval else None
    metas = None
    if a.public_metadata:  # PUBLIC market metadata only (precision / min order); no key, no private call
        from wallex_agent.market.wallex_public import WallexPublicClient
        from wallex_agent.runtime.shadow import live_universe

        metas, _, _ = live_universe(WallexPublicClient())
    evidence_ok = None
    if a.evidence:
        try:
            evidence_ok = verify_bundle(Path(a.evidence))["summary"].get("real_wallex_write_count") == 0
        except (EvidenceError, OSError):
            evidence_ok = False
    rep = readiness_gate(cfg, store=_store(), attestations=att, account=acct, ai_qa_passed=qa, now=_now(), metas=metas,
                         ai_eval=ai_eval, evidence_ok=evidence_ok, resolved_model_id=os.environ.get("NINEROUTER_MODEL"))
    if a.format == "table":
        for i in rep.items:
            print(f"{i.status:<8} {i.scope:<6} {i.name:<26} {i.detail}")
        print(f"LEVEL {rep.level}")
    else:
        print(rep.model_dump_json(indent=1))
    return 1 if rep.level == "BLOCKED" else 0


def cmd_screen(a: argparse.Namespace) -> int:
    """Rank eligible spot TMN markets from PUBLIC data (Freqtrade pairlist concept). Proposal only; changes nothing."""
    from wallex_agent.market.book import parse_depth
    from wallex_agent.market.meta import eligible_symbols, unavailable_markets
    from wallex_agent.market.wallex_public import WallexPublicClient
    from wallex_agent.universe import render_fa, screen, settings_for, spread_candidates

    cfg = _load_for_report(a.config)  # read-only ranking; the LIVE start check stays in `run`
    client = WallexPublicClient()
    v1, hector, package = client.markets_v1(), client.markets_hector(), client.package(183)
    spot = {m["symbol"] for m in hector["result"]["markets"] if m.get("is_spot")}
    eligible = eligible_symbols(spot, set(v1["result"]["symbols"]), unavailable_markets(package), set())
    settings = settings_for(cfg, top_n=a.top)
    now = _now()
    first = screen(hector, eligible, now=now, settings=settings)
    spreads: dict[str, Any] = {}
    for symbol in spread_candidates(first, limit=2 * a.top):  # one public depth read per shortlisted symbol
        try:
            spreads[symbol] = parse_depth(client.depth(symbol), received_at=_now()).spread_bps()
        except (OSError, ValueError, KeyError):
            spreads[symbol] = None  # unknown spread fails closed (SPREAD_UNKNOWN)
    report = screen(hector, eligible, now=now, settings=settings, spreads=spreads)
    if a.out:
        Path(a.out).write_text(report.model_dump_json(indent=1) + "\n")
    print(render_fa(report), end="")
    return 0


def cmd_preflight(a: argparse.Namespace) -> int:
    """Read-only. With WALLEX_API_KEY set in this shell it also reads the account (GET only) and can write the
    AccountEvidence file for `readiness --account`; without it the account item fails closed."""
    from wallex_agent.execution.wallex_live import AccountReadError, UrllibPrivateTransport, WallexAccountReader
    from wallex_agent.market.wallex_public import WallexPublicClient
    from wallex_agent.runtime.live import account_evidence, live_preflight

    cfg = _load_for_report(a.config)
    account = WallexAccountReader(UrllibPrivateTransport()) if os.environ.get("WALLEX_API_KEY") else None
    now = _now()
    rep, _, _, _ = live_preflight(cfg, account=account, client=WallexPublicClient(), now=now)
    if account is not None and a.account_out:
        try:
            Path(a.account_out).write_text(account_evidence(account, cfg, now=now).model_dump_json(indent=1) + "\n")
            print(f"account evidence written: {a.account_out}")
        except AccountReadError as exc:
            print(f"account evidence NOT written: {exc}", file=sys.stderr)
    for name, ok, detail in rep.checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<22} {detail}")
    print("LIVE READY" if rep.ok else "NOT READY FOR LIVE")
    return 0 if rep.ok else 1


def cmd_drills(a: argparse.Namespace) -> int:
    import tempfile

    from wallex_agent.agent.ninerouter import NineRouterNotConfigured
    from wallex_agent.journal.store import JournalStore
    from wallex_agent.market.wallex_public import WallexPublicClient
    from wallex_agent.runtime.drills import MarketCapture, run_drills
    from wallex_agent.runtime.shadow import LiveFeed, live_universe

    cfg = _load(a.config)
    client = WallexPublicClient()
    metas, eligible, _ = live_universe(client)
    now = _now()
    interval = cfg.run.bar_interval_seconds
    as_of = datetime.fromtimestamp(int(now.timestamp()) // interval * interval, UTC)
    feed = LiveFeed(client, history_minutes=2 * 1440)
    from wallex_agent.market.bars import Bar
    from wallex_agent.market.book import OrderBook

    bars: dict[str, list[Bar]] = {}
    books: dict[str, OrderBook] = {}
    for sym in cfg.run.symbols:
        feed.refresh_candles(sym, now)
        bars[sym] = feed.strategy_bars(sym, interval, as_of)
        book = feed.refresh_book(sym, as_of)
        if book is not None:
            books[sym] = book
    nr = None
    try:
        from wallex_agent.runtime.compose import build_decider

        llm_cfg = _load(a.config, **{"agent.decision_provider": "LLM", "agent.decision_deadline_s": 1, "agent.use_wallex_mcp_tools": False})
        provider, _ = build_decider(llm_cfg, JournalStore(Path(tempfile.mkdtemp()) / "nr.sqlite"))
        nr = provider.decide
    except NineRouterNotConfigured:
        nr = None
    tmp = Path(tempfile.mkdtemp())
    results = run_drills(cfg, MarketCapture(bars, books, as_of, "wallex-live-public-capture"), metas=metas, eligible=eligible,
                         store_factory=lambda n: JournalStore(tmp / f"{n}.sqlite"), ninerouter_timeout=nr)
    report: dict[str, Any] = {"captured_at": now.isoformat(), "as_of": as_of.isoformat(), "symbols": list(books),
              "bars": {k: len(v) for k, v in bars.items()}, "real_wallex_writes": 0,
              "results": [{"drill": r.name, "passed": r.passed, "evidence": r.evidence} for r in results]}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    for r in results:
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.name:<30} {json.dumps(r.evidence, ensure_ascii=False, default=str)[:150]}")
    return 0 if all(r.passed for r in results) else 1


def cmd_eval(a: argparse.Namespace) -> int:
    from wallex_agent.agent.evaluation import run_eval

    out = run_eval(models=a.models, repeats=a.repeats, out_path=Path(a.out), mcp=a.mcp)
    print(json.dumps(out["aggregate"], ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="wallex-agent")
    sub = p.add_subparsers(dest="command", required=True)
    from wallex_agent.experiments.cli import add_parser

    add_parser(sub)
    from wallex_agent.observability.cli import add_parsers

    add_parsers(sub)
    sub.add_parser("policy-matrix")
    sub.add_parser("tool-exposure")
    b = sub.add_parser("backtest")
    b.add_argument("--config", default="config/backtest.toml")
    b.add_argument("--split", choices=["ALL", "TRAIN", "VALIDATION", "TEST"])
    b.add_argument("--decider", choices=["BASELINE", "LLM", "REPLAY"])
    b.add_argument("--autonomy", choices=[m.value for m in AutonomyMode])
    b.add_argument("--replay-of")
    b.add_argument("--run-id")
    b.add_argument("--export", action="store_true")
    b.add_argument("--verbose", action="store_true")
    s = sub.add_parser("shadow")
    s.add_argument("--config", default="config/shadow.toml")
    s.add_argument("--duration", type=int, default=3600)
    s.add_argument("--decider", choices=["BASELINE", "LLM"])
    s.add_argument("--autonomy", choices=[m.value for m in AutonomyMode])
    s.add_argument("--run-id")
    s.add_argument("--summary-every", type=int, default=300)
    s.add_argument("--resume", metavar="RUN_ID", help="resume an unfinished SHADOW run through restart recovery")
    rc = sub.add_parser("recover")
    rc.add_argument("run_id")
    rc.add_argument("--db", default="state.sqlite")
    ab = sub.add_parser("abandon")
    ab.add_argument("run_id")
    ab.add_argument("--reason", required=True)
    ab.add_argument("--operator", required=True)
    ab.add_argument("--db", default="state.sqlite")
    r = sub.add_parser("run")
    r.add_argument("--env", required=True, choices=[e.value for e in Environment])
    r.add_argument("--config")
    r.add_argument("--run-id")
    r.add_argument("--resume", metavar="RUN_ID", help="continue an unfinished LIVE run through restart recovery")
    r.add_argument("--summary-every", type=int, default=300)
    st = sub.add_parser("status")
    st.add_argument("run_id", nargs="?")
    st.add_argument("--run-id", dest="run_id_option")
    st.add_argument("--db", default="state.sqlite")
    for name in ("approve", "reject"):
        x = sub.add_parser(name)
        x.add_argument("ticket")
        x.add_argument("--operator", default=os.environ.get("USER", "operator"))
        x.add_argument("--note")
    k = sub.add_parser("kill")
    k.add_argument("--reason", required=True)
    k.add_argument("--operator", default=os.environ.get("USER", "operator"))
    rk = sub.add_parser("reset-kill")
    rk.add_argument("--reason", required=True)
    rk.add_argument("--operator", required=True)
    rp = sub.add_parser("replay")
    rp.add_argument("run_id")
    rp.add_argument("--config", help="original config for older runs without a recorded config")
    rp.add_argument("--split", choices=["ALL", "TRAIN", "VALIDATION", "TEST"])
    q = sub.add_parser("ask")
    q.add_argument("question")
    q.add_argument("--run-id")
    q.add_argument("--db", default="state.sqlite")
    q.add_argument("--model")
    rd = sub.add_parser("readiness")
    rd.add_argument("--config", required=True)
    rd.add_argument("--attestations", help="owner-written TOML (OwnerAttestations)")
    rd.add_argument("--account", help="JSON from a read-only account preflight (AccountEvidence)")
    rd.add_argument("--ai-qa", dest="ai_qa", help="JSON with {\"passed\": bool} from the NineRouter QA")
    rd.add_argument("--ai-eval", dest="ai_eval", help="JSON AiEvalEvidence from the frozen F2 agent-alignment evaluation")
    rd.add_argument("--public-metadata", action="store_true", help="read precision/min-order metadata from the PUBLIC API")
    rd.add_argument("--evidence", help="evidence bundle directory to verify offline (wallex-agent evidence export)")
    rd.add_argument("--format", choices=("json", "table"), default="json")
    pf = sub.add_parser("preflight")
    pf.add_argument("--config", required=True)
    pf.add_argument("--account-out", help="write AccountEvidence JSON (read-only account pass) for `readiness --account`")
    scr = sub.add_parser("screen", help="rank eligible markets from public data (proposal only)")
    scr.add_argument("--config", default="config/live-f2.toml")
    scr.add_argument("--top", type=int, default=10)
    scr.add_argument("--out", help="also write the JSON report here")
    dr = sub.add_parser("shadow-drills")
    dr.add_argument("--config", default="config/shadow.toml")
    dr.add_argument("--out", default="reports/phase-03/shadow/SHADOW_DRILLS.json")
    ev = sub.add_parser("eval-ninerouter")
    ev.add_argument("--models", nargs="+", default=[os.environ.get("NINEROUTER_MODEL", "cx/gpt-6.1-sol")])
    ev.add_argument("--repeats", type=int, default=3)
    ev.add_argument("--out", default="var/ninerouter_eval.json")
    ev.add_argument("--mcp", action="store_true")
    args = p.parse_args(argv)

    match args.command:
        case "performance" | "explain" | "demo-evidence" | "evidence":
            from wallex_agent.observability.cli import command

            return command(args, STATE)
        case "experiment":
            from wallex_agent.experiments.cli import command

            return command(args)
        case "policy-matrix":
            _policy_matrix()
            return 0
        case "tool-exposure":
            _tool_exposure()
            return 0
        case "backtest":
            return cmd_backtest(args)
        case "shadow":
            return cmd_shadow(args)
        case "run":
            return cmd_run(args)
        case "recover":
            return cmd_recover(args)
        case "abandon":
            return cmd_abandon(args)
        case "status":
            return cmd_status(args)
        case "approve":
            return cmd_approve(args, True)
        case "reject":
            return cmd_approve(args, False)
        case "kill":
            return cmd_kill(args, False)
        case "reset-kill":
            return cmd_kill(args, True)
        case "replay":
            return cmd_replay(args)
        case "ask":
            return cmd_ask(args)
        case "preflight":
            return cmd_preflight(args)
        case "screen":
            return cmd_screen(args)
        case "readiness":
            return cmd_readiness(args)
        case "eval-ninerouter":
            return cmd_eval(args)
        case "shadow-drills":
            return cmd_drills(args)
        case _:
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
