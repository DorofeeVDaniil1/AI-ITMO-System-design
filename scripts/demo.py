#!/usr/bin/env python3
"""
demo.py — прогон событий ТЗ + сценарий №7 без HTTP-сервера.

Печатает decision / команду турникету / reasons.
Для review-событий показывает, что они ушли в очередь охраны.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from poc.app.models import AccessVerifyRequest
from poc.app.pipeline import process_event, reset_runtime_state
from poc.app.store import guard_queue
from poc.tests.test_smoke import REFERENCE_EVENTS

# Референс ТЗ + доп. кейсы сценария 7 (нет лица, серый liveness)
DEMO_EVENTS = (
    "e-1001",
    "e-1002",
    "e-1003",
    "e-1004",
    "e-1005",
    "e-1006",
    "e-1007",
)


def main() -> None:
    reset_runtime_state()
    print(
        f"{'event':8} {'decision':14} {'cmd':6} {'review':6} "
        f"{'lat':>5} {'id':10} reasons"
    )
    print("-" * 100)
    for event_id in DEMO_EVENTS:
        req = AccessVerifyRequest(**REFERENCE_EVENTS[event_id])
        resp = process_event(req)
        print(
            f"{resp.event_id:8} {resp.decision:14} {resp.turnstile_command:6} "
            f"{str(resp.requires_human_review):6} {resp.latency_ms:5d} "
            f"{resp.decision_id:10} {', '.join(resp.reasons)}"
        )
    print("-" * 100)
    queued = guard_queue.list_open()
    print(f"очередь охраны: {len(queued)} событий (турникет сам не open)")
    for item in queued:
        print(f"  - {item['event_id']}: {', '.join(item['reasons'])}")
    print("audit -> poc/data/audit.jsonl")
    print("UI охраны (если поднят API): http://127.0.0.1:8000/ui/guard")


if __name__ == "__main__":
    main()
