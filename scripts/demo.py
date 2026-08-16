#!/usr/bin/env python3
"""
demo.py — прогон пяти событий ТЗ без поднятия HTTP-сервера.

Удобно для проверяющего: python scripts/demo.py
Пишет таблицу decision/cmd/latency/scores и наполняет poc/data/audit.jsonl.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Чтобы импорты poc.* работали при запуске из корня репо
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from poc.app.models import AccessVerifyRequest
from poc.app.pipeline import process_event, reset_runtime_state
from poc.tests.test_smoke import REFERENCE_EVENTS


def main() -> None:
    reset_runtime_state()
    print(
        f"{'event':8} {'decision':14} {'cmd':6} {'lat':>5} "
        f"{'score':>6} {'margin':>6} {'id':10} reasons"
    )
    print("-" * 96)
    for event_id in ("e-1001", "e-1002", "e-1003", "e-1004", "e-1005"):
        req = AccessVerifyRequest(**REFERENCE_EVENTS[event_id])
        resp = process_event(req)
        score = f"{resp.match_score:.3f}" if resp.match_score is not None else "-"
        margin = (
            f"{resp.margin_to_second_best:.3f}"
            if resp.margin_to_second_best is not None
            else "-"
        )
        print(
            f"{resp.event_id:8} {resp.decision:14} {resp.turnstile_command:6} "
            f"{resp.latency_ms:5d} {score:>6} {margin:>6} {resp.decision_id:10} "
            f"{', '.join(resp.reasons)}"
        )
    print("-" * 96)
    print("audit -> poc/data/audit.jsonl")


if __name__ == "__main__":
    main()
