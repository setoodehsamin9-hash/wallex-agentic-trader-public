"""Risk Engine output. Deterministic; the AI never sees a way to override it."""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .base import SCHEMA_VERSION, Contract, UtcDatetime


class RiskOutcome(StrEnum):
    APPROVE = "APPROVE"
    REDUCE = "REDUCE"
    """Approved at a smaller size than requested (caps, posture, liquidity)."""
    REJECT = "REJECT"


class RiskCheck(Contract):
    name: str
    """e.g. 'symbol_eligible', 'kill_switch', 'drawdown_pause', 'max_exposure', 'min_notional'."""
    passed: bool
    detail: str
    limit_key: str | None = None
    """Config key of the threshold applied, so the verdict is reproducible from config hash + key."""


class RiskVerdict(Contract):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    verdict_id: str
    intent_hash: str
    outcome: RiskOutcome
    approved_qty: Decimal | None = Field(default=None, ge=0)
    approved_notional_tmn: Decimal | None = Field(default=None, ge=0)
    checks: tuple[RiskCheck, ...]
    limits_version: str
    evaluated_at: UtcDatetime
    expires_at: UtcDatetime
    """The execution adapter refuses a verdict past this time; a fresh evaluation is required."""
    reservation_id: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> "RiskVerdict":
        if self.outcome is RiskOutcome.REJECT and self.approved_qty not in (None, Decimal(0)):
            raise ValueError("a REJECT verdict approves no quantity")
        if self.outcome is not RiskOutcome.REJECT and not self.approved_qty:
            raise ValueError("APPROVE/REDUCE must carry a positive approved_qty")
        return self
