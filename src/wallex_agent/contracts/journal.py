"""Decision Journal record and run manifest: the audit and replay backbone.

One `DecisionRecord` is written for EVERY cycle that reached the decision stage, including
WAIT, rejected and failed ones. "Why did you not trade?" must be answerable from the journal.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from .base import SCHEMA_VERSION, Contract, UtcDatetime
from .decision import AgentAction, DecisionProvenance, ValidatedDecision
from .execution import ApprovalTicket, ExecutionEvent, OrderIntent
from .market import DataRef, RegimeLabel
from .modes import AutonomyMode, Environment
from .risk import RiskVerdict


class PolicyOutcome(StrEnum):
    """What the permission policy allows an AI-originated, risk-relevant action to become."""

    PROPOSE_ONLY = "PROPOSE_ONLY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    EXECUTE = "EXECUTE"
    BLOCKED = "BLOCKED"


class CycleStatus(StrEnum):
    NO_CANDIDATES = "NO_CANDIDATES"
    WAITED = "WAITED"
    PROPOSED = "PROPOSED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    RISK_REJECTED = "RISK_REJECTED"
    BLOCKED = "BLOCKED"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    DATA_UNUSABLE = "DATA_UNUSABLE"


class CodeVersion(Contract):
    git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    dirty: bool
    """LIVE refuses to start when dirty: the delivered code must equal the code that traded."""


class CandidateSummary(Contract):
    candidate_id: str
    strategy_id: str
    strategy_version: str
    symbol: str


class DecisionRecord(Contract):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    decision_id: str
    run_id: str
    cycle_id: str
    recorded_at: UtcDatetime
    environment: Environment
    autonomy: AutonomyMode
    symbol: str | None
    snapshot_id: str | None
    data_refs: tuple[DataRef, ...]
    features_ref: str | None
    deterministic_regime: RegimeLabel | None
    candidates: tuple[CandidateSummary, ...]
    provenance: DecisionProvenance | None
    validated: ValidatedDecision | None
    effective_action: AgentAction
    policy_outcome: PolicyOutcome | None
    risk_verdict: RiskVerdict | None
    approval: ApprovalTicket | None
    order_intent: OrderIntent | None
    execution_events: tuple[ExecutionEvent, ...] = ()
    status: CycleStatus
    block_reason: str | None = None
    """Why the cycle stopped short of execution, e.g. OUTSIDE_TRADING_WINDOW (next open in `next_entry_at`)."""
    next_entry_at: UtcDatetime | None = None
    config_sha256: str
    code: CodeVersion


class RunManifest(Contract):
    """Written at run start and sealed at run end. Enough to reproduce or replay the run."""

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    run_id: str
    started_at: UtcDatetime
    ended_at: UtcDatetime | None = None
    environment: Environment
    autonomy: AutonomyMode
    code: CodeVersion
    config_sha256: str
    limits_version: str
    prompt_bundle_sha256: str | None
    model_id: str | None
    model_provider: str | None = None
    """e.g. 'ninerouter' — the only real-model path. Never a credential."""
    model_endpoint: str | None = None
    """Non-secret base URL, e.g. http://localhost:20128/v1."""
    model_settings_sha256: str | None
    dependency_lock_sha256: str
    trading_window_version: str | None = None
    starting_capital_tmn: str | None = None
    universe_version: str
    dataset_refs: tuple[DataRef, ...] = ()
    clock: Literal["VIRTUAL", "WALL"]
    seed: int
    strategy_versions: tuple[str, ...]
