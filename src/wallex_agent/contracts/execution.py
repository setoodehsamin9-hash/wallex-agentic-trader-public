"""Order intents, approval tickets and the internal order state machine.

`OrderState` is OUR lifecycle, not Wallex's status enum. The raw exchange status is kept
alongside it on every event so the two are never confused.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field

from .base import SCHEMA_VERSION, Contract, UtcDatetime
from .modes import AutonomyMode, Environment


class OrderPurpose(StrEnum):
    ENTRY = "ENTRY"
    EXIT_STOP = "EXIT_STOP"
    EXIT_TAKE_PROFIT = "EXIT_TAKE_PROFIT"
    EXIT_TIME = "EXIT_TIME"
    EXIT_AI = "EXIT_AI"
    """Early exit requested by AgentAction.EXIT_POSITION (risk-reducing)."""
    EXIT_SIGNAL = "EXIT_SIGNAL"
    """Deterministic strategy exit (e.g. F2 trend loss at a decision close); protective, never AI-originated."""
    LIQUIDATION = "LIQUIDATION"
    """End-of-challenge conversion to TMN."""
    KILL = "KILL"


class OrderIntent(Contract):
    """Built deterministically from a candidate + risk verdict. `intent_hash` binds approvals and verdicts to it."""

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    intent_id: str
    intent_hash: str
    decision_id: str
    client_order_id: str
    """Generated once and persisted BEFORE submission; reused for every status query and retry."""
    symbol: str
    side: Literal["BUY", "SELL"]
    order_type: Literal["LIMIT", "MARKET"]
    qty: Decimal = Field(gt=0)
    limit_price: Decimal | None = Field(default=None, gt=0)
    purpose: OrderPurpose
    environment: Environment
    autonomy: AutonomyMode
    created_at: UtcDatetime


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"


class ApprovalTicket(Contract):
    """Single-use approval for exactly one intent hash, environment and policy version. Server-side storage only."""

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    ticket_id: str
    intent_hash: str
    environment: Environment
    policy_version: str
    created_at: UtcDatetime
    expires_at: UtcDatetime
    status: ApprovalStatus
    decided_by: str | None = None
    decided_at: UtcDatetime | None = None
    note: str | None = None


class OrderState(StrEnum):
    INTENT = "INTENT"
    RESERVED = "RESERVED"
    SUBMITTING = "SUBMITTING"
    ACKED = "ACKED"
    UNKNOWN = "UNKNOWN"
    """Submission outcome not known (timeout, lost response). Reconcile by client_order_id; never resubmit blindly."""
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


TERMINAL_ORDER_STATES = frozenset({OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED})


class FillSource(StrEnum):
    BACKTEST_SIM = "BACKTEST_SIM"
    SHADOW_SIM = "SHADOW_SIM"
    EXCHANGE = "EXCHANGE"


class ExecutionEvent(Contract):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    event_id: str
    intent_id: str
    client_order_id: str
    state: OrderState
    exchange_status_raw: str | None
    cum_filled_qty: Decimal = Field(ge=0)
    avg_fill_price: Decimal | None = None
    fee: Decimal | None = None
    fee_asset: str | None = None
    source: FillSource
    at: UtcDatetime
