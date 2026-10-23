"""Approval Service: single-use tickets for APPROVAL_REQUIRED. The operator can only approve or reject the exact
intent the algorithm built; there is no way to edit symbol, side, quantity, entry, stop or take-profit.

Lifecycle: PENDING -(operator approve)-> APPROVED -(engine consume, atomic)-> CONSUMED -> fresh risk recheck ->
execute. PENDING/APPROVED past expiry -> EXPIRED (reservation released). REJECTED is terminal.
"""

from datetime import datetime, timedelta
from typing import Any

from wallex_agent.contracts import ApprovalStatus, ApprovalTicket, Environment, OrderIntent
from wallex_agent.contracts.hashing import content_id
from wallex_agent.journal.store import JournalStore
from wallex_agent.policy import POLICY_VERSION


class ApprovalError(RuntimeError):
    pass


class ApprovalService:
    def __init__(self, store: JournalStore, *, ttl_s: int) -> None:
        self.store = store
        self.ttl = timedelta(seconds=ttl_s)

    def open(self, run_id: str, intent: OrderIntent, bundle: dict[str, Any], now: datetime) -> ApprovalTicket:
        ticket = ApprovalTicket(ticket_id="tk-" + content_id({"h": intent.intent_hash})[:10], intent_hash=intent.intent_hash,
                                environment=intent.environment, policy_version=POLICY_VERSION, created_at=now,
                                expires_at=now + self.ttl, status=ApprovalStatus.PENDING)
        self.store.save_ticket(run_id, ticket, bundle)
        return ticket

    def _get(self, ticket_id: str, *, run_id: str | None = None) -> tuple[ApprovalTicket, dict[str, Any]]:
        found = self.store.ticket(ticket_id, run_id=run_id)
        if found is None:
            raise ApprovalError(f"unknown ticket {ticket_id}")
        return found

    def decide(self, ticket_id: str, *, approve: bool, operator: str, now: datetime, note: str | None = None) -> ApprovalTicket:
        """Operator action (CLI). Approve or reject only."""
        ticket, _ = self._get(ticket_id)
        if ticket.status is not ApprovalStatus.PENDING:
            raise ApprovalError(f"ticket is {ticket.status}, not PENDING")
        if now >= ticket.expires_at:
            self.store.transition_ticket(ticket_id, ApprovalStatus.PENDING, ticket.model_copy(update={"status": ApprovalStatus.EXPIRED}))
            raise ApprovalError("ticket expired")
        new = ticket.model_copy(update={"status": ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED,
                                        "decided_by": operator, "decided_at": now, "note": note})
        if not self.store.transition_ticket(ticket_id, ApprovalStatus.PENDING, new):
            raise ApprovalError("ticket changed concurrently")
        return new

    def consume(self, ticket_id: str, *, intent_hash: str, environment: Environment, now: datetime,
                run_id: str | None = None) -> dict[str, Any]:
        """Engine side: APPROVED -> CONSUMED exactly once, for exactly this intent and environment."""
        ticket, bundle = self._get(ticket_id, run_id=run_id)
        if ticket.intent_hash != intent_hash or ticket.environment is not environment or ticket.policy_version != POLICY_VERSION:
            raise ApprovalError("ticket does not match this intent/environment/policy")
        if ticket.status is not ApprovalStatus.APPROVED:
            raise ApprovalError(f"ticket is {ticket.status}")
        if now >= ticket.expires_at:
            self.store.transition_ticket(ticket_id, ApprovalStatus.APPROVED, ticket.model_copy(update={"status": ApprovalStatus.EXPIRED}))
            raise ApprovalError("ticket expired before execution")
        if not self.store.transition_ticket(ticket_id, ApprovalStatus.APPROVED, ticket.model_copy(update={"status": ApprovalStatus.CONSUMED})):
            raise ApprovalError("ticket already consumed")
        return bundle

    def expire_stale(self, now: datetime, *, run_id: str | None = None) -> list[ApprovalTicket]:
        expired: list[ApprovalTicket] = []
        for st in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED):
            for t in self.store.tickets(st, run_id=run_id):
                if now >= t.expires_at and self.store.transition_ticket(t.ticket_id, st, t.model_copy(update={"status": ApprovalStatus.EXPIRED})):
                    expired.append(t)
        return expired
