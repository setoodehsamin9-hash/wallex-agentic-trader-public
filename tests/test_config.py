from pathlib import Path

import pytest

from wallex_agent.policy import RunConfigurationError
from wallex_agent.runtime.config import load_run_config, missing_live_requirements, parse_run_config

REPO = Path(__file__).resolve().parents[1]

MINIMAL = """
[run]
environment = "{env}"
autonomy = "{autonomy}"
live_autonomous_enabled = {gate}
symbols = ["BTCTMN"]
bar_interval_seconds = 300
starting_capital_tmn = "5000000"

[limits]
version = "limits-test"
approved_by_owner = {approved}

[limits.risk]
per_trade_budget_fraction = "0.005"
{extra_risk}
"""


def _cfg(env: str = "BACKTEST", autonomy: str = "AUTONOMOUS", gate: str = "false", approved: str = "false",
         extra_risk: str = "") -> str:
    return MINIMAL.format(env=env, autonomy=autonomy, gate=gate, approved=approved, extra_risk=extra_risk)


def test_backtest_config_parses_with_partial_limits() -> None:
    cfg = parse_run_config(_cfg())
    assert cfg.run.symbols == ("BTCTMN",)
    assert str(cfg.limits.risk.per_trade_budget_fraction) == "0.005"
    assert cfg.limits.risk.max_open_positions is None


def test_live_requires_all_limits_window_cutoff_and_owner_approval() -> None:
    missing = missing_live_requirements(parse_run_config(_cfg(env="LIVE", autonomy="APPROVAL_REQUIRED")))
    assert "limits.risk.max_open_positions" in missing
    assert "limits.approved_by_owner" in missing
    assert "trading_window" in missing
    assert "competition.end_utc" in missing
    with pytest.raises(RunConfigurationError):
        parse_run_config(_cfg(env="LIVE", autonomy="APPROVAL_REQUIRED"), enforce_live=True)


def test_live_autonomous_needs_gate() -> None:
    with pytest.raises(RunConfigurationError):
        parse_run_config(_cfg(env="LIVE", autonomy="AUTONOMOUS", gate="false"), enforce_live=True)


def test_unknown_key_rejected() -> None:
    with pytest.raises(ValueError):
        parse_run_config(_cfg(extra_risk='max_leverage = "3"'))


def test_config_hash_stable_and_sensitive() -> None:
    a = parse_run_config(_cfg())
    b = parse_run_config(_cfg())
    c = parse_run_config(_cfg(extra_risk="max_open_positions = 2"))
    assert a.config_sha256 == b.config_sha256 != c.config_sha256


@pytest.mark.parametrize("name", ["backtest.toml", "shadow.toml"])
def test_shipped_configs_load(name: str) -> None:
    cfg = load_run_config(REPO / "config" / name)
    assert cfg.run.environment.value in ("BACKTEST", "SHADOW")
    assert missing_live_requirements(cfg)  # shipped simulation configs are NOT live-ready by design


def test_live_template_is_fail_closed_until_the_owner_fills_it() -> None:
    text = (REPO / "config" / "live-f2.toml").read_text()
    cfg = parse_run_config(text)
    assert cfg.run.environment.value == "LIVE"
    assert (cfg.agent.decision_provider, cfg.agent.model_id, cfg.agent.role, cfg.agent.prompt_version) == (
        "LLM", "cx/gpt-6.1-sol", "VETO", "veto_v1")
    if cfg.run.live_autonomous_enabled and cfg.limits.approved_by_owner:  # owner-filled: every LIVE requirement present
        assert missing_live_requirements(cfg) == []
    else:
        with pytest.raises(RunConfigurationError):
            parse_run_config(text, enforce_live=True)
    # F2 strategy/features/execution are byte-for-byte the SHADOW values (no parameter drift into LIVE)
    shadow = load_run_config(REPO / "config" / "shadow-f2.toml")
    assert (cfg.strategy, cfg.features, cfg.execution) == (shadow.strategy, shadow.features, shadow.execution)
    assert cfg.costs.fee_source == "ACCOUNT" or cfg.costs == shadow.costs  # LIVE may only replace the assumed fee by the read one
