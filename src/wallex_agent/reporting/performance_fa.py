"""PERFORMANCE_FA.md: plain-Persian run report for a non-trader. Built only from the summary and stated context."""

from decimal import Decimal
from typing import Any

from .performance import PerformanceSummary

CHECK_FA = {
    "trading_window": "خارج از ساعت مجاز معامله",
    "candidate_fresh": "فرصت منقضی شده بود",
    "data_quality": "داده ناقص یا کهنه",
    "book_fresh": "دفتر سفارش تازه نبود",
    "spread": "فاصله قیمت خرید و فروش زیاد بود",
    "total_loss_budget": "سقف کل ضرر پر شده بود",
    "session_loss_budget": "سقف ضرر این نشست پر شده بود",
    "drawdown_pause": "افت حساب از حد توقف بیشتر بود",
    "drawdown_emergency": "افت اضطراری حساب",
    "loss_streak_cooldown": "مکث پس از چند ضرر پیاپی",
    "one_position_per_symbol": "روی همین نماد موقعیت باز داشتیم",
    "max_open_positions": "تعداد موقعیت‌های باز به سقف رسیده بود",
    "sizing": "حجم مجاز معامله از حداقل بازار کمتر می‌شد",
    "kill_switch": "کلید قطع اضطراری فعال بود",
    "symbol_eligible": "نماد مجاز نبود",
}
REASON_FA = {
    "BREAKOUT_CONFIRMED": "شکست سقف تأیید شد", "TREND_CONFIRMED": "روند صعودی تأیید شد", "VOLUME_CONFIRMED": "حجم تأیید کرد",
    "BEST_OF_CANDIDATES": "بهترین فرصت میان گزینه‌ها", "CONFLICTING_SIGNALS": "نشانه‌های متناقض", "COST_TOO_HIGH": "هزینه زیاد",
    "LOW_LIQUIDITY": "نقدشوندگی کم", "REGIME_UNFAVORABLE": "وضعیت بازار نامناسب", "DATA_QUALITY": "کیفیت داده",
    "WEAK_EVIDENCE": "شواهد ضعیف", "MOMENTUM_FADING": "ضعیف‌شدن حرکت", "PROTECT_PROFIT": "حفظ سود", "NO_CANDIDATES": "فرصتی نبود",
}


def _tmn(x: Decimal | None) -> str:
    return "—" if x is None else f"{x:,.0f} تومان"


def render_performance_fa(s: PerformanceSummary, ctx: dict[str, Any]) -> str:
    sign = "سود" if s.net_pnl_tmn > 0 else ("ضرر" if s.net_pnl_tmn < 0 else "بدون سود و ضرر")
    lines = [
        f"# گزارش عملکرد اجرا — {s.environment} / {s.autonomy}",
        "",
        f"شناسه اجرا: `{s.run_id}` · بازه: {s.period_start or '—'} تا {s.period_end or '—'} (UTC)",
        "",
        "> این گزارش فقط از معاملات ثبت‌شده همین اجرا ساخته شده است. "
        + ("در Backtest و Shadow هیچ پول واقعی جابه‌جا نشده و نتیجه، شبیه‌سازی با فرض‌های بیان‌شده است." if s.environment != "LIVE"
           else "این نتیجه از سفارش‌های واقعی ثبت‌شده است."),
        "",
        "## خلاصه مالی",
        "",
        "| مورد | مقدار | توضیح ساده |",
        "|---|---|---|",
        f"| سرمایه شروع | {_tmn(s.starting_capital_tmn)} | پولی که اجرا با آن شروع شد |",
        f"| سرمایه/ارزش پایان | {_tmn(s.final_equity_tmn)} | نقد + ارزش فروش دارایی‌های باز پس از کارمزد |",
        f"| سود یا ضرر محقق‌شده | {_tmn(s.realized_pnl_tmn)} | نتیجه معاملاتی که بسته شده‌اند (معیار اصلی مسابقه، به تومان) |",
        f"| سود/ضرر باز | {_tmn(s.unrealized_pnl_tmn)} | سود یا ضرر معاملاتی که هنوز بسته نشده‌اند |",
        f"| سود یا ضرر خالص | {_tmn(s.net_pnl_tmn)} ({sign}) | ارزش پایان منهای سرمایه شروع |",
        f"| درصد بازدهی | {s.return_pct}٪ (محقق‌شده: {s.realized_return_pct}٪) | سود خالص تقسیم بر سرمایه شروع |",
        f"| بیشترین افت حساب | {s.max_drawdown_pct}٪ | بیشترین کاهش ارزش حساب نسبت به بالاترین مقدار قبلی (Drawdown) |",
        f"| هزینه کارمزد | {_tmn(s.fees_tmn)} | مجموع کارمزد خرید و فروش |",
        "",
        "## معاملات",
        "",
        f"- تعداد معاملات بسته‌شده: **{s.trades}** — موفق (سودده): {s.wins} · ناموفق: {s.losses}"
        + (f" · نرخ موفقیت: {s.win_rate_pct}٪" if s.win_rate_pct is not None else ""),
        f"- میانگین سود/ضرر هر معامله: {_tmn(s.avg_trade_pnl_tmn)}",
        f"- موقعیت‌های باز در پایان: {len(s.open_positions)}" + (f" ({', '.join(p['symbol'] for p in s.open_positions)})" if s.open_positions else ""),
        f"- باقی‌مانده‌های خیلی کوچک (dust): {s.dust or 'ندارد'}",
        "",
        "## تصمیم‌ها",
        "",
        f"- کل چرخه‌های تصمیم: {s.decisions_total} · بدون فرصت: {s.no_candidate_cycles} · صبر (WAIT): {s.wait_decisions}"
        f" · رد توسط موتور ریسک: {s.risk_rejected} · مسدود: {s.blocked} · ورود ارسال‌شده: {s.entries_submitted}"
        + (f" · پیشنهاد (Advisor): {s.proposed}" if s.proposed else "")
        + (f" · منتظر تأیید: {s.awaiting_approval}" if s.awaiting_approval else ""),
        f"- چرخه‌های متوقف به علت ساعت معامله یا پایان مسابقه: {s.outside_window_or_cutoff}",
    ]
    if s.top_reason_codes:
        lines.append("- مهم‌ترین دلایل تصمیم AI: " + "، ".join(f"{REASON_FA.get(k, k)} ({n})" for k, n in s.top_reason_codes))
    if s.top_risk_blocks:
        lines.append("- مهم‌ترین موانع ریسک: " + "، ".join(f"{CHECK_FA.get(k, k)} ({n})" for k, n in s.top_risk_blocks))
    lines += [
        "",
        "## استراتژی و هوش مصنوعی",
        "",
        f"- استراتژی/نسخه: {', '.join(s.strategy_versions) or ctx.get('strategy', '—')}",
        f"- مسیر مدل AI: {ctx.get('model_route', 'بدون مدل (انتخاب‌گر قاعده‌مند B0)')}",
        f"- مدل‌هایی که واقعاً پاسخ دادند: {', '.join(s.responding_models) or '—'}",
        f"- نقش AI: {ctx.get('ai_role', 'AI از میان فرصت‌هایی که استراتژی ساخته یکی را انتخاب می‌کند یا صبر می‌کند؛ حجم، قیمت و حد ضرر را کد قاعده‌مند تعیین می‌کند و موتور ریسک می‌تواند رد کند.')}",
        f"- فراخوانی‌های AI: {s.ai_calls} · پاسخ معتبر: {s.ai_valid} · نامعتبر/خطا: {s.ai_invalid_by_status or 'ندارد'}"
        + (f" · زمان پاسخ میانه {s.ai_latency_ms_p50} میلی‌ثانیه، p95 {s.ai_latency_ms_p95}" if s.ai_latency_ms_p50 is not None else ""),
        f"- خطاهای اجرا: {s.execution_errors} · سفارش‌های حل‌نشده: {s.unresolved_orders} · کلید قطع: {'فعال' if s.kill_switch_latched else 'غیرفعال'}",
        "",
        "## محدودیت‌های این نتیجه",
        "",
    ]
    for c in ctx.get("caveats", []):
        lines.append(f"- {c}")
    lines += ["", "سود گذشته یا شبیه‌سازی‌شده تضمین سود آینده نیست. این گزارش توصیه سرمایه‌گذاری نیست.", ""]
    return "\n".join(lines)
