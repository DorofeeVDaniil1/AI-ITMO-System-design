from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Optional

from poc.app.metrics import metrics


@dataclass
class TurnstileAck:
    accepted: bool
    status: str  # opened | duplicate | held | rejected
    event_id: str
    detail: str


class TurnstileSimulator:
    """
    Stand-in for a real turnstile SDK.
    Prod would: send command over serial/IP, wait ack, protect against replay.
    Here we only model ack + one-open-per-event_id.
    """

    def __init__(self) -> None:
        self._opened: set[str] = set()
        self._lock = Lock()

    def reset(self) -> None:
        with self._lock:
            self._opened.clear()

    def apply(self, *, event_id: str, command: str) -> TurnstileAck:
        with self._lock:
            if command == "hold":
                metrics.turnstile_hold += 1
                return TurnstileAck(
                    accepted=True,
                    status="held",
                    event_id=event_id,
                    detail="barrier stays closed",
                )
            if command != "open":
                return TurnstileAck(
                    accepted=False,
                    status="rejected",
                    event_id=event_id,
                    detail=f"unknown command {command}",
                )
            if event_id in self._opened:
                metrics.turnstile_open_dup += 1
                return TurnstileAck(
                    accepted=False,
                    status="duplicate",
                    event_id=event_id,
                    detail="open already issued for this event_id",
                )
            self._opened.add(event_id)
            metrics.turnstile_open_ok += 1
            return TurnstileAck(
                accepted=True,
                status="opened",
                event_id=event_id,
                detail="simulated open ack",
            )

    def was_opened(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._opened


turnstile = TurnstileSimulator()
