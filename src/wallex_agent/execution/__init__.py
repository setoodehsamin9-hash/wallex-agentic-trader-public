"""Execution Engine (DETERMINISTIC + EXTERNAL). Order state machine, persist-before-send client ids,
UNKNOWN -> reconcile (never blind resubmit), exit coordinator (one owner of exits per position),
adapters: backtest simulator, shadow simulator, live Wallex REST. Phase 03.
"""
