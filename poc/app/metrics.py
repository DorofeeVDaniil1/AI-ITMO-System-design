from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class MetricsRegistry:
    """In-process counters — prod would be Prometheus; shape is the same idea."""

    decisions: dict[str, int] = field(default_factory=dict)
    turnstile_open_ok: int = 0
    turnstile_open_dup: int = 0
    turnstile_hold: int = 0
    guard_actions: int = 0
    revocations: int = 0
    _lock: Lock = field(default_factory=Lock)

    def inc_decision(self, decision: str) -> None:
        with self._lock:
            self.decisions[decision] = self.decisions.get(decision, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "decisions": dict(self.decisions),
                "turnstile_open_ok": self.turnstile_open_ok,
                "turnstile_open_duplicate_rejected": self.turnstile_open_dup,
                "turnstile_hold": self.turnstile_hold,
                "guard_actions": self.guard_actions,
                "revocations": self.revocations,
            }

    def reset(self) -> None:
        with self._lock:
            self.decisions.clear()
            self.turnstile_open_ok = 0
            self.turnstile_open_dup = 0
            self.turnstile_hold = 0
            self.guard_actions = 0
            self.revocations = 0


metrics = MetricsRegistry()
