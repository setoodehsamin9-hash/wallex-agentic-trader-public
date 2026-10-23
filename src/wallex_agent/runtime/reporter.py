"""Runtime status reporting for the owner: event-level Persian-friendly lines + periodic summaries.

Raw structured records stay in the journal; this only prints what a person needs to follow the bot. It never prints
secrets (it only sees runtime events and status dicts).
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

STATUS_FA = {
    "NO_CANDIDATES": "فرصتی نبود", "WAITED": "صبر (WAIT)", "BLOCKED": "مسدود", "RISK_REJECTED": "رد توسط موتور ریسک",
    "PROPOSED": "پیشنهاد (بدون اجرا)", "AWAITING_APPROVAL": "منتظر تأیید", "FILLED": "خرید انجام شد (شبیه‌سازی)",
    "PARTIALLY_FILLED": "بخشی خریده شد", "SUBMITTED": "سفارش ثبت شد", "EXECUTION_FAILED": "اجرا ناموفق",
    "EXECUTION_UNKNOWN": "وضعیت سفارش نامعلوم (در حال تطبیق)", "DATA_UNUSABLE": "داده قابل استفاده نبود",
}
QUIET = {"DECISION"}  # decisions are summarised unless they are actionable


class ConsoleReporter:
    def __init__(self, *, stream: TextIO = sys.stdout, log_path: Path | None = None, verbose: bool = True) -> None:
        self.stream = stream
        self.verbose = verbose
        self.log = log_path.open("a") if log_path else None

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        if self.log:
            self.log.write(json.dumps({"at": datetime.now(UTC).isoformat(), "kind": kind, **data}, ensure_ascii=False, default=str) + "\n")
            self.log.flush()
        if not self.verbose:
            return
        line = self.format(kind, data)
        if line:
            print(line, file=self.stream, flush=True)

    @staticmethod
    def format(kind: str, d: dict[str, Any]) -> str | None:
        t = datetime.now(UTC).strftime("%H:%M:%S")
        if kind == "RUN_STARTED":
            return f"[{t}] ▶ اجرا شروع شد: محیط {d['environment']} · حالت {d['autonomy']} · تصمیم‌گیر {d['decider']} · نمادها {', '.join(d['symbols'])}"
        if kind == "DECISION":
            st = d["status"]
            if st == "NO_CANDIDATES":
                return None
            extra = f" · دلیل: {d['block_reason']}" if d.get("block_reason") else ""
            nxt = f" · اجازه بعدی ورود: {d['next_entry_at']}" if d.get("next_entry_at") else ""
            why = f" · AI: {d['rationale']}" if d.get("rationale") else ""
            return f"[{t}] {d['symbol']}: {STATUS_FA.get(st, st)} ({d['action']}){extra}{nxt}{why}"
        if kind == "PROPOSAL":
            return f"[{t}] 💡 پیشنهاد خرید {d['symbol']}: مقدار {d['qty']} تا قیمت {d['limit']} · حد ضرر {d['stop']} · حد سود {d['tp']}"
        if kind == "APPROVAL_REQUESTED":
            return (f"[{t}] ⏳ تأیید لازم است ({d['ticket_id']}): خرید {d['symbol']} مقدار {d['qty']} تا {d['limit']} · "
                    f"حد ضرر {d['stop']} · حد سود {d['tp']} · مهلت تا {d['expires_at']}  → wallex-agent approve {d['ticket_id']}")
        if kind in ("ENTRY_SUBMITTED", "APPROVED_ENTRY_SUBMITTED"):
            return f"[{t}] ✅ سفارش خرید {d['symbol']}: وضعیت {d.get('state')}"
        if kind == "EXIT_SUBMITTED":
            return f"[{t}] ↩ خروج {d['symbol']} ({d['purpose']}): {d.get('state')}"
        if kind == "TRADE_CLOSED":
            return f"[{t}] 📒 معامله بسته شد {d['symbol']}: نتیجه {d['pnl']} تومان ({d['reason']})"
        if kind == "RISK_REJECTED":
            return f"[{t}] 🛑 موتور ریسک رد کرد {d['symbol']} ({d['what']}): {'; '.join(d['failed_checks'][:3])}"
        if kind == "ENTRIES_NOT_ALLOWED":
            return f"[{t}] ⏸ ورود جدید مجاز نیست: {d['reason']} · زمان بعدی: {d.get('next_open') or '—'}"
        if kind in ("AI_BREAKER", "AI_DECISION_INVALID"):
            return f"[{t}] ⚠ AI: {kind} {d.get('status', '')} {d.get('detail') or ''}"[:220]
        if kind == "SUMMARY":
            return (f"[{t}] 📊 خلاصه: فعال={d['active']} · {d['environment']}/{d['autonomy']} · ورود مجاز={d['entries_allowed']} ({d['window_reason']})"
                    f" · ارزش حساب {d['equity_tmn']} · سود محقق {d['realized_pnl_tmn']} · بازده {d['return_pct']}٪"
                    f" · افت {d['drawdown_pct']}٪ · موقعیت باز {d['open_positions']} · کلید قطع={d['kill_switch']}"
                    f" · NineRouter={d.get('ninerouter', '—')} · تأخیر={d.get('ninerouter_latency_ms') or '—'}ms"
                    f" · تصمیم مدل={d.get('ai_calls', 0)} · خطای مدل={d.get('ai_errors', 0)} · مرز بعدی {d.get('next_boundary') or '—'}")
        return f"[{t}] {kind}: {json.dumps(d, ensure_ascii=False, default=str)[:200]}"
