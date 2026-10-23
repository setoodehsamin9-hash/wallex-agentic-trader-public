"""Risk Engine (DETERMINISTIC). Pre-trade checks, deterministic sizing, reservations, protections
(drawdown pause, loss-streak cooldown, session loss), kill-switch latch, circuit breakers. Can approve,
reduce or reject; nothing upstream can override it. Phase 03.
"""
