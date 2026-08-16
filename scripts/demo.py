#!/usr/bin/env python3
"""Run the five TZ reference events through the decision pipeline (no server)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from poc.app.models import AccessVerifyRequest
from poc.app.pipeline import process_event, reset_runtime_state
from poc.tests.test_smoke import REFERENCE_EVENTS


def main() -> None:
    reset_runtime_state()
    print(f"{'event':8} {'decision':14} {'cmd':6} {'degraded':8} reasons")
    print("-" * 72)
    for event_id in ("e-1001", "e-1002", "e-1003", "e-1004", "e-1005"):
        req = AccessVerifyRequest(**REFERENCE_EVENTS[event_id])
        resp = process_event(req)
        print(
            f"{resp.event_id:8} {resp.decision:14} {resp.turnstile_command:6} "
            f"{str(resp.degraded_mode):8} {', '.join(resp.reasons)}"
        )
    print("-" * 72)
    print("audit -> poc/data/audit.jsonl")


if __name__ == "__main__":
    main()
