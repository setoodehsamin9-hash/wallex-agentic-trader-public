"""Kill switch latch: blocks new entries until an operator resets it. Exits and liquidation continue.

Persisted through `on_change` (the journal store), so a restart cannot silently clear it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class KillState:
    latched: bool
    reason: str | None
    at: datetime | None
    by: str | None


class KillSwitch:
    def __init__(self, state: KillState | None = None, on_change: Callable[[KillState], None] | None = None,
                 on_read: Callable[[], KillState] | None = None) -> None:
        self._state = state or KillState(False, None, None, None)
        self._on_change = on_change
        self._on_read = on_read

    @property
    def latched(self) -> bool:
        return self.state.latched

    @property
    def state(self) -> KillState:
        if self._on_read is not None:
            self._state = self._on_read()
        return self._state

    def latch(self, reason: str, *, at: datetime, by: str = "system") -> None:
        if self.latched:
            return
        self._state = KillState(True, reason, at, by)
        if self._on_change:
            self._on_change(self._state)

    def reset(self, reason: str, *, at: datetime, operator: str) -> None:
        """Operator only (CLI). Never exposed to the model or the assistant."""
        if not operator or not reason:
            raise ValueError("reset needs an operator and a reason")
        self._state = KillState(False, f"reset: {reason}", at, operator)
        if self._on_change:
            self._on_change(self._state)
