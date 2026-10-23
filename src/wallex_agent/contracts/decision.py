"""The AI's structured output and its provenance.

`AgentDecision` is the ONLY thing the reasoning agent produces that the trading path consumes.
It can select one of the candidates it was shown, wait, or ask to close an open position early.
It cannot name a symbol, price, quantity, stop, or limit, so it cannot widen risk.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import SCHEMA_VERSION, Contract, UtcDatetime
from .market import RegimeLabel


class AgentAction(StrEnum):
    ENTER = "ENTER"
    """Take `selected_candidate_id`."""
    WAIT = "WAIT"
    """No new exposure this cycle. Also the fallback for every AI failure."""
    EXIT_POSITION = "EXIT_POSITION"
    """Close `position_id` early. Risk-reducing only; disabled unless config enables it."""


class Confidence(StrEnum):
    """Categorical on purpose: there is no calibrated probability to report, and size never depends on it."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskPosture(StrEnum):
    """Reduce-only sizing hint. CAUTIOUS lets the risk engine scale size down; nothing scales it up."""

    NORMAL = "NORMAL"
    CAUTIOUS = "CAUTIOUS"


class ReasonCode(StrEnum):
    TREND_CONFIRMED = "TREND_CONFIRMED"
    BREAKOUT_CONFIRMED = "BREAKOUT_CONFIRMED"
    VOLUME_CONFIRMED = "VOLUME_CONFIRMED"
    BEST_OF_CANDIDATES = "BEST_OF_CANDIDATES"
    CONFLICTING_SIGNALS = "CONFLICTING_SIGNALS"
    COST_TOO_HIGH = "COST_TOO_HIGH"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    REGIME_UNFAVORABLE = "REGIME_UNFAVORABLE"
    DATA_QUALITY = "DATA_QUALITY"
    WEAK_EVIDENCE = "WEAK_EVIDENCE"
    MOMENTUM_FADING = "MOMENTUM_FADING"
    PROTECT_PROFIT = "PROTECT_PROFIT"
    NO_CANDIDATES = "NO_CANDIDATES"
    EXECUTION_STATE_UNSAFE = "EXECUTION_STATE_UNSAFE"
    """Unresolved order/recovery state or kill switch reported in the execution state (veto reason)."""


class AgentDecision(Contract):
    """Schema handed to the model as its output type. Extra fields are rejected."""

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    snapshot_id: str
    action: AgentAction
    selected_candidate_id: str | None = None
    position_id: str | None = None
    regime_assessment: RegimeLabel
    confidence: Confidence
    risk_posture: RiskPosture = RiskPosture.NORMAL
    reason_codes: tuple[ReasonCode, ...] = Field(min_length=1, max_length=5)
    evidence_feature_ids: tuple[str, ...] = Field(default=(), max_length=12)
    brief_rationale: str = Field(min_length=1, max_length=600)
    """Concise, presentable justification grounded in the provided evidence. Not chain-of-thought."""

    @model_validator(mode="after")
    def _action_shape(self) -> "AgentDecision":
        if self.action is AgentAction.ENTER and (self.selected_candidate_id is None or self.position_id is not None):
            raise ValueError("ENTER requires selected_candidate_id and no position_id")
        if self.action is AgentAction.WAIT and (self.selected_candidate_id is not None or self.position_id is not None):
            raise ValueError("WAIT carries no candidate or position")
        if self.action is AgentAction.EXIT_POSITION and (self.position_id is None or self.selected_candidate_id is not None):
            raise ValueError("EXIT_POSITION requires position_id and no candidate")
        return self


class DecisionSource(StrEnum):
    LLM = "LLM"
    """Live inference."""
    REPLAY = "REPLAY"
    """Recorded model output re-fed with no inference (reproducibility)."""
    BASELINE = "BASELINE"
    """Deterministic B0 selector, used for ablation and as the non-AI benchmark."""


class ValidationStatus(StrEnum):
    VALID = "VALID"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    UNKNOWN_REFERENCE = "UNKNOWN_REFERENCE"
    """Cited a candidate, position, feature or snapshot that was not in the input."""
    STALE = "STALE"
    """Arrived after the candidate or snapshot TTL."""
    TIMEOUT = "TIMEOUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    ACTION_DISABLED = "ACTION_DISABLED"
    VETO_UNSUPPORTED = "VETO_UNSUPPORTED"
    """VETO role: the model declined the deterministic candidate without a permitted, evidenced veto reason, so the
    deterministic candidate stands (the model's own output is kept in the journal)."""

    @property
    def answered(self) -> bool:
        """The model returned a usable, timely structured answer. An overruled veto is not a model failure."""
        return self in (ValidationStatus.VALID, ValidationStatus.VETO_UNSUPPORTED)


class ToolCallRecord(Contract):
    """One tool call made during the bounded reasoning loop, recorded for audit and replay."""

    seq: int = Field(ge=0)
    tool_name: str
    args_sha256: str
    result_sha256: str | None
    result_ref: str | None
    """Pointer to the stored result payload so replay can serve it without a network call."""
    is_error: bool
    latency_ms: int = Field(ge=0)


class DecisionProvenance(Contract):
    source: DecisionSource
    model_id: str | None
    """Provider-qualified model name, e.g. 'anthropic:claude-sonnet-5-5'. None for BASELINE."""
    model_settings_sha256: str | None
    prompt_version: str | None
    prompt_sha256: str | None
    tool_calls: tuple[ToolCallRecord, ...] = ()
    model_requests: int = Field(ge=0)
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = Field(ge=0)
    raw_output_ref: str | None
    started_at: UtcDatetime
    finished_at: UtcDatetime


class ValidatedDecision(Contract):
    """What the deterministic validator lets through. On any failure `effective_action` is WAIT."""

    status: ValidationStatus
    decision: AgentDecision | None
    effective_action: AgentAction
    detail: str | None = None
