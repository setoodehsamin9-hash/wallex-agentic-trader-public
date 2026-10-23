"""Persian operator and submission views of structured records; no generated model explanation."""
from decimal import Decimal
from typing import Any, cast

from wallex_agent.contracts.hashing import canonical_json

from .inspector import execution_events, inspect_run
from .source import EvidenceError, RunEvidence

MISSING = "نامعلوم / ناموجود"


def display(value: Any) -> str:
    if value is None:
        return MISSING
    if value is True:
        return "بله"
    if value is False:
        return "خیر"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, dict):
        return canonical_json(cast(dict[str, Any], value)).decode()
    if isinstance(value, (list, tuple)):
        return canonical_json(list(cast(list[Any], value))).decode()
    return str(value)


def explain_decisions(source: RunEvidence, *, last: int = 5) -> list[dict[str, Any]]:
    if last < 1 or last > 100:
        raise EvidenceError("--last must be between 1 and 100")
    events = execution_events(source)
    out: list[dict[str, Any]] = []
    for decision in source.decisions[-last:]:
        agent = decision.validated.decision if decision.validated else None
        risk = decision.risk_verdict
        context = source.references.get(f"ctx:{decision.decision_id}", {})
        selected = agent.selected_candidate_id if agent else None
        trades = [t for t in source.trades if t["decision_id"] == decision.decision_id]
        cid = decision.order_intent.client_order_id if decision.order_intent else None
        out.append({"decision_id": decision.decision_id, "timestamp": decision.recorded_at.isoformat(), "symbol": decision.symbol,
            "snapshot_id": decision.snapshot_id, "candidates": [c.model_dump(mode="json") for c in decision.candidates],
            "candidate_details": context.get("candidates"), "selected_candidate_id": selected,
            "market_context": {k: context[k] for k in ("snapshot", "features") if k in context} or None,
            "effective_action": decision.effective_action.value,
            "strategy_reason_codes": None,  # SignalCandidate v1 carries evidence_feature_ids, not reason codes
            "agent_reason_codes": [r.value for r in agent.reason_codes] if agent else None,
            "brief_rationale": agent.brief_rationale if agent else None,
            "decision_source": decision.provenance.source.value if decision.provenance else None,
            "validation": decision.validated.status.value if decision.validated else None,
            "block_reason": decision.block_reason, "policy_outcome": decision.policy_outcome.value if decision.policy_outcome else None,
            "risk_outcome": risk.outcome.value if risk else None,
            "risk_checks": [c.model_dump(mode="json") for c in risk.checks] if risk else None,
            "result_status": decision.status.value,
            "execution_events": [e.model_dump(mode="json") for e in events if e.client_order_id == cid] if cid else [],
            "trade_outcomes": trades or None})
    return out


def render_status_fa(summary: dict[str, Any], latest: dict[str, Any] | None = None) -> str:
    s = summary
    positions = s["open_positions"]
    return "\n".join([
        f"اجرا: {s['run_id']} | {s['environment']} / {s['autonomy']} | {s['activity_kind']}",
        f"سلامت ثبت‌شده: {s['health']} | آخرین شاهد UTC: {s['observed_at']} (این خروجی پایش زنده نیست)",
        f"موقعیت باز: {display(len(positions) if positions is not None else None)} | نمادها: {', '.join(s['symbols']) or MISSING}",
        f"ارزش حساب: {display(s['final_equity_tmn'])} تومان | نقد: {display(s['cash_tmn'])} | درگیری سرمایه: {display(s['exposure_tmn'])}",
        f"سود/ضرر محقق: {display(s['realized_pnl_tmn'])} | باز: {display(s['unrealized_pnl_tmn'])} | خالص: {display(s['net_pnl_tmn'])} | کارمزد: {display(s['fees_tmn'])}",
        f"Risk: {display(s['risk_counts'])} | موانع: {s['risk_blocks'] or MISSING}",
        f"مدل: {display(s['model_status'])} | فراخوانی AI: {display(s['ai_calls'])} | شکست/timeout: {display(s['model_failures'])}/{display(s['model_timeouts'])}",
        f"Breaker ثبت‌شده: {display(s['breaker'])} | کلید قطع: {display(s['kill_switch_latched'])} | سفارش UNKNOWN: {display(len(s['unknown_orders']) if s['unknown_orders'] is not None else None)}",
        f"داده (آخرین چرخه): {display(s['data_status'])} | خبر: {display(s['news_status'])} | بازیابی/شروع مجدد: {display(s['restart_count'])}",
        f"REAL WRITE: {s['real_wallex_write_count']} | شمارش نوشتن واقعی والکس؛ UNKNOWN یعنی شاهد کافی نداریم",
        (f"آخرین تصمیم: {latest['effective_action']} / {latest['result_status']} | دلیل ثبت‌شده: {display(latest['brief_rationale'] or latest['block_reason'] or latest['validation'])}"
         if latest else "آخرین تصمیم: ناموجود"),
    ]) + "\n"


def render_performance_fa(source: RunEvidence, summary: dict[str, Any] | None = None) -> str:
    s = summary if summary is not None else inspect_run(source)
    simulated = s["activity_kind"] == "SIMULATED"
    rows = [("سرمایه شروع", "starting_capital_tmn"), ("ارزش پایان/آخرین ارزش", "final_equity_tmn"),
        ("پول نقد", "cash_tmn"), ("سود/ضرر محقق‌شده (پس از کارمزد)", "realized_pnl_tmn"),
        ("سود/ضرر باز (تحقق‌نیافته)", "unrealized_pnl_tmn"), ("کارمزد پرداخت‌شده", "fees_tmn"),
        ("سود/ضرر خالص", "net_pnl_tmn"), ("بازده (%)", "return_pct"), ("بیشترین ارزش حساب", "peak_equity_tmn"),
        ("بیشترین افت از قله (%)", "max_drawdown_pct"), ("معاملات بسته‌شده", "trades"),
        ("معاملات سودده / زیان‌ده", None), ("درصد معاملات سودده", "win_rate_pct"),
        ("نسبت مجموع سودها به مجموع زیان‌ها", "profit_factor"), ("میانگین سود/ضرر معامله", "avg_trade_pnl_tmn")]
    lines = [f"# گزارش عملکرد {s['environment']} — {s['run_id']}", "",
        f"محیط: **{s['environment']}** | حالت: {s['autonomy']} | نوع فعالیت: **{s['activity_kind']}**", "",
        "نتیجهٔ شبیه‌سازی است؛ به سود و زیان واقعی اضافه نمی‌شود." if simulated else
        "این اجرا برچسب LIVE دارد؛ شواهد آن جدا از شبیه‌سازی گزارش می‌شود. شمارش کامل نوشتن‌ها ممکن است UNKNOWN باشد.", "",
        f"بازه UTC: {s['started_at']} تا {display(s['ended_at'])}؛ آخرین شاهد: {s['observed_at']}", "",
        "| شاخص | مقدار |", "|---|---|"]
    for label, key in rows:
        val = f"{display(s['wins'])} / {display(s['losses'])}" if key is None else display(s[key])
        lines.append(f"| {label} | {val} |")
    lines += ["", "تمام ارقام مالی به تومان است. سود محقق از فروش ثبت‌شده به‌دست می‌آید؛ سود باز مربوط به دارایی باقی‌مانده است. "
        "کارمزد داخل مبنای هزینه محاسبه شده و بار دوم از سود کم نمی‌شود. نسبت سود به زیان وقتی مجموع زیان صفر است، تعریف‌نشده می‌ماند.", "",
        f"- فعالیت ثبت‌شده: {display(s['decisions_total'])} تصمیم، {display(s['candidates_produced'])} رخداد کاندیدا، {display(s['wait_decisions'])} WAIT (صبر).",
        f"- Risk: {display(s['risk_counts'])}؛ موانع: {s['risk_blocks'] or MISSING}.",
        f"- مدل: {display(s['model_id'])} / {display(s['model_provider'])}؛ تصمیم AI: {display(s['ai_calls'])}؛ شکست: {display(s['model_failures'])}؛ timeout: {display(s['model_timeouts'])}؛ میانه تأخیر: {display(s['ai_latency_ms_p50'])}ms.",
        f"- خطاهای اجرا: {display(s['execution_errors'])} ({s['execution_error_basis']})؛ سفارش UNKNOWN: {display(s['unknown_orders'])}.",
        f"- NewsContext: {display(s['news_context_count'])}؛ رخداد خبر: {display(s['news_event_count'])}؛ veto خبر: {display(s['news_veto_count'])}.",
        f"- شروع مجدد: {display(s['restart_count'])}؛ کل رخدادهای بازیابی: {display(s['recovery_event_count'])}.",
        f"- REAL WRITE / real_wallex_write_count: **{s['real_wallex_write_count']}**؛ {s['write_count_evidence']['reason']}",
        f"- موقعیت‌های باز در زمان گزارش: {display(s['open_positions'])}.",
        f"- Breaker (وضعیت توقف): {display(s['breaker'])}؛ کلید قطع: {display(s['kill_switch_latched'])}.", "",
        "ارزش حساب از ارزش‌گذاری ثبت‌شده می‌آید؛ نقاط جدول equity ممکن است به تومان گرد شده باشند. داده یا وضعیت لحظه‌ای جدید دریافت نشده است. "
        "نامعلوم/ناموجود یعنی شاهد کافی نداریم؛ صفر به‌جای دادهٔ غایب قرار نمی‌گیرد.", ""]
    lines += [f"- {note}" for note in s["notes"]]
    return "\n".join(lines) + "\n"


def render_explanation_fa(explanation: dict[str, Any]) -> str:
    e = explanation
    return (f"{e['timestamp']} | {display(e['symbol'])} | {e['effective_action']} / {e['result_status']}\n"
        f"کاندیدای منتخب: {display(e['selected_candidate_id'])} | کاندیداها: {display(e['candidates'])}\n"
        f"کد دلیل استراتژی: {display(e['strategy_reason_codes'])} | دلیل تصمیم‌گیر: {display(e['agent_reason_codes'])}\n"
        f"توضیح کوتاه ذخیره‌شده ({display(e['decision_source'])}): {display(e['brief_rationale'])} | اعتبارسنجی: {display(e['validation'])}\n"
        f"Risk: {display(e['risk_outcome'])} | بررسی‌ها: {display(e['risk_checks'])} | مانع: {display(e['block_reason'])}\n"
        f"اجرای ثبت‌شده: {display([x['state'] for x in e['execution_events']])} | نتیجه معامله: {display(e['trade_outcomes'])}\n")


def render_demo_fa(source: RunEvidence) -> str:
    s = inspect_run(source)
    latest = explain_decisions(source, last=1)
    e = latest[-1] if latest else None
    return "\n".join([
        f"# دمو از شواهد ثبت‌شده — {s['run_id']} / {s['environment']} / {s['activity_kind']}", "",
        f"1. بازار/زمینه: {display(e['market_context'] if e else None)}",
        f"2. کاندیدای قطعی: {display(e['candidate_details'] if e else None)}",
        f"3. تصمیم‌گیر: {display(e['decision_source'] if e else None)}؛ تصمیم/توضیح ذخیره‌شده: {display(e['effective_action'] if e else None)} / {display(e['brief_rationale'] if e else None)}",
        f"4. Risk Engine: {display(e['risk_outcome'] if e else None)}؛ بررسی: {display(e['risk_checks'] if e else None)}",
        f"5. اجرای ثبت‌شده: {display([x['state'] for x in e['execution_events']] if e else None)}؛ وضعیت چرخه: {display(e['result_status'] if e else None)}؛ نتیجه: {display(e['trade_outcomes'] if e else None)}",
        f"6. اثبات journal/replay: {s['state_records']} رخداد زنجیره‌ای؛ {display(s['checkpoints_verified'])} checkpoint بازسازی‌شده؛ هش سر زنجیره: {source.states[-1]['hash'] if source.states else MISSING}؛ بررسی بسته: wallex-agent evidence verify DIR",
        f"7. عملکرد: خالص {display(s['net_pnl_tmn'])} تومان؛ محقق {display(s['realized_pnl_tmn'])}؛ باز {display(s['unrealized_pnl_tmn'])}؛ REAL WRITE={s['real_wallex_write_count']}", "",
        "WAIT/REJECT یا نبود معامله همان‌گونه که ثبت شده نمایش داده می‌شود. BASELINE و REPLAY اجرای مدل واقعی نیستند.",
    ]) + "\n"
