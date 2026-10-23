"""Versioned run configuration loaded from TOML. Host-owned: the model never sees or changes it.

Every numeric threshold lives here (ADR-002 §7); there are no hidden defaults for risk limits. Simulation configs may
leave limits unset where a check is not exercised; `missing_live_requirements` lists what LIVE still needs.
"""

import tomllib
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from wallex_agent.contracts import AutonomyMode, Contract, Environment, UtcDatetime
from wallex_agent.contracts.hashing import content_id
from wallex_agent.policy import RunConfigurationError, check_run_allowed

Weekday = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class RunSection(Contract):
    environment: Environment
    autonomy: AutonomyMode
    live_autonomous_enabled: bool = False
    symbols: tuple[str, ...] = Field(min_length=1)
    bar_interval_seconds: int = Field(gt=0)
    starting_capital_tmn: Decimal = Field(gt=0)
    seed: int = 0
    label: str = ""


class RiskLimits(Contract):
    per_trade_budget_fraction: Decimal | None = None
    total_loss_budget_tmn: Decimal | None = None
    session_loss_budget_tmn: Decimal | None = None
    max_position_notional_tmn: Decimal | None = None
    max_total_exposure_tmn: Decimal | None = None
    max_open_positions: int | None = None
    drawdown_pause_fraction: Decimal | None = None
    drawdown_emergency_fraction: Decimal | None = None
    loss_streak_count: int | None = None
    loss_streak_window_s: int | None = None
    cooldown_s: int | None = None
    max_spread_bps: Decimal | None = None
    max_depth_fraction: Decimal | None = None
    stress_cost_bps: Decimal | None = None
    verdict_ttl_s: int | None = None
    cautious_size_fraction: Decimal | None = None


class DataLimits(Contract):
    max_bar_age_s: int | None = None
    max_book_age_s: int | None = None


class ApprovalLimits(Contract):
    ticket_ttl_s: int | None = None


class CutoffLimits(Contract):
    no_new_entry_before_end_s: int | None = None
    liquidation_start_before_end_s: int | None = None


class KillLimits(Contract):
    max_consecutive_execution_errors: int | None = None
    reconciliation_tolerance_tmn: Decimal | None = None


class Limits(Contract):
    version: str
    approved_by_owner: bool = False
    """LIVE refuses to start until the owner has approved these exact values."""
    risk: RiskLimits = RiskLimits()
    data: DataLimits = DataLimits()
    approval: ApprovalLimits = ApprovalLimits()
    cutoff: CutoffLimits = CutoffLimits()
    kill: KillLimits = KillLimits()


class CostModel(Contract):
    """Assumed costs for BACKTEST/SHADOW. LIVE uses the account fee read by preflight; these are then stress only."""

    fee_rate: Decimal = Field(ge=0)
    fee_source: Literal["ASSUMED", "ACCOUNT"] = "ASSUMED"
    backtest_slippage_bps: Decimal = Field(ge=0)


class ExecutionSettings(Contract):
    entry_order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    entry_limit_slippage_bps: Decimal = Field(ge=0)
    """Marketable-limit cap above the reference price for entries."""
    exit_limit_slippage_bps: Decimal = Field(ge=0)
    entry_ttl_s: int = Field(gt=0)
    exit_order_ttl_s: int | None = Field(default=None, gt=0, le=300)
    """Phase 05: a resting exit SELL older than this is cancelled and, once the cancel is confirmed, re-issued at the
    current price; a stop breach cancels a resting non-stop exit at once. Required by LIVE preflight; unset keeps the
    earlier behaviour (simulators expire their own orders)."""


class FeatureSettings(Contract):
    atr_bars: int = Field(gt=1)
    trend_bars: int = Field(gt=1)
    volume_bars: int = Field(gt=1)
    regime_trend_atr: Decimal = Field(gt=0)
    """|close - SMA(trend_bars)| / ATR above this = trending."""
    regime_shock_atr_pct: Decimal = Field(gt=0)
    """ATR/close above this fraction = SHOCK."""
    ema_bars: int | None = Field(default=None, gt=1)
    """Phase 04 round 2 (F2): adds `ema.{n}` and `close_lag.{n}`. Unset = feature set unchanged (features-1.0.0)."""
    regime_er_bars: int | None = Field(default=None, gt=1)
    regime_rank_bars: int | None = Field(default=None, gt=1)
    """regime-r2: Kaufman efficiency ratio over regime_er_bars and ATR(atr_bars)/close, each ranked against the
    previous regime_rank_bars values (causal) -> `er_rank`, `vol_rank`, `close_lag.{regime_er_bars}`."""

    @model_validator(mode="after")
    def _regime_pair(self) -> "FeatureSettings":
        if (self.regime_er_bars is None) != (self.regime_rank_bars is None):
            raise ValueError("regime_er_bars and regime_rank_bars must be set together")
        return self


class C1Params(Contract):
    version: str
    lookback_bars: int = Field(gt=1)
    buffer_atr: Decimal = Field(ge=0)
    stop_atr: Decimal = Field(gt=0)
    tp_atr: Decimal | None = Field(default=None, gt=0)
    max_holding_s: int = Field(gt=0)
    candidate_ttl_s: int = Field(gt=0)
    min_edge_over_cost_bps: Decimal = Field(ge=0)
    min_volume_ratio: Decimal | None = None
    exit_mode: Literal["TARGET", "TIME"] = "TARGET"
    """TARGET: fixed take-profit, cost-checked as target edge (c1-0.1.x). TIME: no take-profit; exit by stop or
    max holding; cost-awareness = ATR must be at least `min_atr_cost_ratio` x estimated round-trip cost (Phase 04)."""
    min_atr_cost_ratio: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _exit_mode(self) -> "C1Params":
        if self.exit_mode == "TARGET" and self.tp_atr is None:
            raise ValueError("TARGET exit mode requires tp_atr")
        if self.exit_mode == "TIME" and (self.tp_atr is not None or self.min_atr_cost_ratio is None):
            raise ValueError("TIME exit mode forbids tp_atr and requires min_atr_cost_ratio")
        return self


class C2Params(Contract):
    """C2 trend-hold (Phase 04): long while close is >= entry_trend_atr ATR above SMA(features.trend_bars) and the
    trend_bars return is positive; exit by ATR stop or max holding (no take-profit). trend_gate = false is the
    DRIFT CONTROL (enter whenever flat): a benchmark for 'does the signal beat simply being long', not a strategy.
    The flag is required so a control can never arise from an omitted value (TOML has no null)."""

    version: str
    trend_gate: bool
    entry_trend_atr: Decimal | None = Field(default=None, gt=0)
    stop_atr: Decimal = Field(gt=0)
    max_holding_s: int = Field(gt=0)
    candidate_ttl_s: int = Field(gt=0)
    min_atr_cost_ratio: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _gate(self) -> "C2Params":
        if self.trend_gate != (self.entry_trend_atr is not None):
            raise ValueError("trend_gate = true requires entry_trend_atr; trend_gate = false forbids it")
        return self


class F2Params(Contract):
    """F2 time-series momentum with hysteresis (Phase 04 round 2, reports/phase-04/round2/PREREGISTRATION_R2.md):
    enter when close >= EMA(features.ema_bars) + entry_band_atr x ATR and close > close ema_bars bars ago; exit by the
    chandelier trail (initial stop = ref - stop_atr x ATR, trail = trail_atr x ATR fixed at entry), by the signal
    close < EMA at a decision close, or by max holding. regime_filter = true also requires the regime-r2 state
    TREND_UP or RANGE (features.regime_er_bars / regime_rank_bars)."""

    version: str
    entry_band_atr: Decimal = Field(ge=0)
    stop_atr: Decimal = Field(gt=0)
    trail_atr: Decimal = Field(gt=0)
    max_holding_s: int = Field(gt=0)
    candidate_ttl_s: int = Field(gt=0)
    regime_filter: bool


StrategyId = Literal["C1_breakout", "C2_trend_hold", "F2_tsm"]


class StrategySettings(Contract):
    c1: C1Params
    c2: C2Params | None = None
    f2: F2Params | None = None
    active: StrategyId = "C1_breakout"

    @model_validator(mode="after")
    def _active_configured(self) -> "StrategySettings":
        if self.active == "C2_trend_hold" and self.c2 is None:
            raise ValueError("strategy.active = C2_trend_hold requires [strategy.c2]")
        if self.active == "F2_tsm" and self.f2 is None:
            raise ValueError("strategy.active = F2_tsm requires [strategy.f2]")
        return self


class AgentSettings(Contract):
    decision_provider: Literal["BASELINE", "LLM", "REPLAY"] = "BASELINE"
    role: Literal["SELECTOR", "VETO"] = "SELECTOR"
    """SELECTOR: the model chooses a candidate or WAITs. VETO (Phase 05): the deterministic candidate stands unless the
    model vetoes it for a permitted, evidenced reason (agent/validator.py VETO_REASONS); failures still WAIT."""
    model_id: str | None = None
    """NineRouter model id, e.g. 'av/claude-sonnet-5-5'. Overridable with NINEROUTER_MODEL."""
    decision_deadline_s: int = Field(default=60, gt=0)
    max_model_requests: int = Field(default=4, gt=0)
    max_tool_calls: int = Field(default=4, ge=0)
    output_retries: int = Field(default=1, ge=0)
    allow_exit_position: bool = False
    invalid_streak_breaker: int = Field(default=3, gt=0)
    use_wallex_mcp_tools: bool = False
    """Give the decision agent the 3 allowlisted public Wallex MCP reads (SHADOW/LIVE only)."""
    prompt_version: str = "decision_v2"
    model_settings: dict[str, Any] = {}
    replay_run_id: str | None = None

    @model_validator(mode="after")
    def _role_matches_prompt(self) -> "AgentSettings":
        # A selector prompt invites WAITs the VETO role would overrule into entries; veto prompts are `veto_*`.
        if (self.role == "VETO") != self.prompt_version.startswith("veto_"):
            raise ValueError(f"agent.role={self.role} does not match prompt_version={self.prompt_version} (VETO needs a veto_* prompt)")
        return self


class WindowSpec(Contract):
    days: tuple[Weekday, ...] = Field(min_length=1)
    """Weekday on which the window STARTS (local time)."""
    start: str
    end: str
    """HH:MM local. end < start means the window crosses midnight."""

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        time.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def _non_empty(self) -> "WindowSpec":
        if self.start == self.end:
            raise ValueError("window start and end must differ")
        return self


class TradingWindowConfig(Contract):
    version: str
    timezone: str
    """IANA name. Required; never assumed."""
    windows: tuple[WindowSpec, ...] = Field(min_length=1)
    no_new_entry_before_close_s: int = Field(ge=0)
    flatten_before_close_s: int = Field(ge=0)

    @model_validator(mode="after")
    def _tz(self) -> "TradingWindowConfig":
        from zoneinfo import ZoneInfo

        ZoneInfo(self.timezone)
        return self


class CompetitionConfig(Contract):
    start_utc: UtcDatetime | None = None
    end_utc: UtcDatetime | None = None


class BacktestSettings(Contract):
    dataset: str
    """Directory under data/datasets/ (hash-pinned manifest)."""
    start_utc: UtcDatetime | None = None
    end_utc: UtcDatetime | None = None
    approval_policy: Literal["APPROVE_ALL", "REJECT_ALL"] = "APPROVE_ALL"
    split: Literal["ALL", "TRAIN", "VALIDATION", "TEST"] = "ALL"


class ShadowSettings(Contract):
    poll_interval_s: int = Field(default=10, gt=0)
    duration_s: int | None = None
    history_bars: int = Field(default=600, gt=0)
    bar_settle_max_s: int = Field(default=120, gt=0)
    """Wallex publishes the last 1m bar a little after the boundary: wait up to this long for the bucket to complete
    before deciding with a STALE flag."""
    warmup_cache_dir: str = "var/warmup"
    """Phase 05 warm-up cache (hash-checked native 1h history, an ignored local path) for strategies needing > 2 days."""
    warmup_seed_dir: str | None = None
    """Optional validated 1h dataset directory (manifest + hashes, e.g. the historical pack) used when no cache exists."""


class RunConfig(Contract):
    run: RunSection
    limits: Limits
    costs: CostModel = CostModel(fee_rate=Decimal("0"), backtest_slippage_bps=Decimal("0"))
    execution: ExecutionSettings = ExecutionSettings(
        entry_limit_slippage_bps=Decimal("0"), exit_limit_slippage_bps=Decimal("0"), entry_ttl_s=60
    )
    features: FeatureSettings | None = None
    strategy: StrategySettings | None = None
    agent: AgentSettings = AgentSettings()
    trading_window: TradingWindowConfig | None = None
    competition: CompetitionConfig = CompetitionConfig()
    backtest: BacktestSettings | None = None
    shadow: ShadowSettings | None = None

    @property
    def config_sha256(self) -> str:
        return content_id(self)


def _nulls(prefix: str, section: Contract) -> list[str]:
    return [f"{prefix}.{name}" for name, value in section.model_dump().items() if value is None]


def missing_live_requirements(cfg: RunConfig) -> list[str]:
    missing: list[str] = []
    lim = cfg.limits
    for name in ("risk", "data", "approval", "cutoff", "kill"):
        missing += _nulls(f"limits.{name}", getattr(lim, name))
    if not lim.approved_by_owner:
        missing.append("limits.approved_by_owner")
    if cfg.trading_window is None:
        missing.append("trading_window")
    if cfg.competition.end_utc is None:
        missing.append("competition.end_utc")
    if cfg.features is None:
        missing.append("features")
    if cfg.strategy is None:
        missing.append("strategy")
    if cfg.execution.exit_order_ttl_s is None:
        missing.append("execution.exit_order_ttl_s")  # a resting LIVE exit must expire, be cancelled and re-issued
    return missing


def parse_run_config(text: str, *, enforce_live: bool = False) -> RunConfig:
    raw = tomllib.loads(text)
    cfg = RunConfig.model_validate(raw)
    if enforce_live and cfg.run.environment is Environment.LIVE:
        check_run_allowed(cfg.run.autonomy, cfg.run.environment, live_autonomous_enabled=cfg.run.live_autonomous_enabled)
        missing = missing_live_requirements(cfg)
        if missing:
            raise RunConfigurationError("LIVE start refused; missing: " + ", ".join(missing))
    return cfg


def load_run_config(path: Path, *, enforce_live: bool = True) -> RunConfig:
    return parse_run_config(path.read_text(), enforce_live=enforce_live)
