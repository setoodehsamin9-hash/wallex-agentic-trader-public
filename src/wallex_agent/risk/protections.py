"""Stateful protections (reimplemented concepts from Freqtrade's StoplossGuard/Cooldown/MaxDrawdown, corrected to
use live equity incl. open positions). Thresholds come from RiskLimits; nothing here has a default value."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from wallex_agent.runtime.config import RiskLimits


@dataclass
class Protections:
    limits: RiskLimits
    exits: list[tuple[datetime, Decimal]] = field(default_factory=lambda: [])
    drawdown_pause_until: datetime | None = None
    pause_trigger_drawdown: Decimal | None = None
    session_start_equity: dict[str, Decimal] = field(default_factory=lambda: {})

    def record_exit(self, net_pnl_tmn: Decimal, at: datetime) -> None:
        self.exits.append((at, net_pnl_tmn))

    def loss_streak_until(self, now: datetime) -> datetime | None:
        k, window, cool = self.limits.loss_streak_count, self.limits.loss_streak_window_s, self.limits.cooldown_s
        if k is None or window is None or cool is None:
            raise ValueError("loss streak limits not configured")
        recent = [(t, p) for t, p in self.exits if t > now - timedelta(seconds=window) and t <= now]
        if len(recent) < k:
            return None
        last_k = recent[-k:]
        if all(p < 0 for _, p in last_k):
            until = last_k[-1][0] + timedelta(seconds=cool)
            return until if until > now else None
        return None

    def drawdown_paused(self, now: datetime, drawdown: Decimal) -> bool:
        """Pause new entries for `cooldown_s` when drawdown reaches the pause level, then resume automatically
        (ADR-002 §5). It re-triggers only on a NEW deeper low, so a flat drawdown cannot lock trading forever;
        the hard stops remain total_loss_budget and drawdown_emergency (kill latch)."""
        pause, cool = self.limits.drawdown_pause_fraction, self.limits.cooldown_s
        if pause is None or cool is None:
            raise ValueError("drawdown limits not configured")
        if drawdown >= pause and (self.pause_trigger_drawdown is None or drawdown > self.pause_trigger_drawdown):
            self.drawdown_pause_until = now + timedelta(seconds=cool)
            self.pause_trigger_drawdown = drawdown
        if drawdown < pause:
            self.pause_trigger_drawdown = None
        return self.drawdown_pause_until is not None and now < self.drawdown_pause_until

    def session_loss(self, session_key: str, equity: Decimal) -> Decimal:
        start = self.session_start_equity.setdefault(session_key, equity)
        return start - equity
