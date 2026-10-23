from wallex_agent.runtime.reporter import ConsoleReporter


def test_persian_summary_shows_model_latency_and_failure_count() -> None:
    text = ConsoleReporter.format("SUMMARY", {"active": True, "environment": "SHADOW", "autonomy": "AUTONOMOUS",
        "entries_allowed": False, "window_reason": "OPEN", "equity_tmn": "5000000", "realized_pnl_tmn": "0",
        "return_pct": "0", "drawdown_pct": "0", "open_positions": 0, "kill_switch": False,
        "ninerouter": "OK", "ninerouter_latency_ms": 1234, "ai_calls": 3, "ai_errors": 1})
    assert text is not None and "SHADOW" in text and "1234" in text and "تأخیر" in text
    assert "خطای مدل=1" in text
