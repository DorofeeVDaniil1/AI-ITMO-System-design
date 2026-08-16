"""
Сценарий №7 (обязательный): рискованное событие → ручная проверка, без open.

Проверяем все ветки из ТЗ:
- лицо не найдено
- низкое качество
- сомнительный liveness
- малый margin
- offline / stale cache

Для каждой: turnstile != open, событие в очереди охраны, причина в ответе и audit.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from poc.app.main import app
from poc.app.pipeline import AUDIT_PATH, reset_runtime_state
from poc.tests.test_smoke import REFERENCE_EVENTS

# event_id → ожидаемая ключевая причина в reasons
RISKY_CASES = {
    "e-1006": "no_face_detected",
    "e-1002": "quality_below_threshold",
    "e-1007": "liveness_uncertain",
    "e-1004": "margin_too_small",
    "e-1005": "stale_or_offline_cache",
}


def setup_function() -> None:
    reset_runtime_state()


def test_scenario7_all_risky_go_to_guard_not_open():
    client = TestClient(app)
    for event_id, reason in RISKY_CASES.items():
        reset_runtime_state()
        body = client.post("/v1/access/verify", json=REFERENCE_EVENTS[event_id]).json()

        assert body["decision"] == "manual_review", event_id
        assert body["turnstile_command"] == "hold", event_id
        assert body["requires_human_review"] is True, event_id
        assert reason in body["reasons"], (event_id, body["reasons"])

        queue = client.get("/v1/guard/queue").json()["items"]
        assert any(item["event_id"] == event_id for item in queue), event_id
        queued = next(item for item in queue if item["event_id"] == event_id)
        assert reason in queued["reasons"]

        lines = AUDIT_PATH.read_text(encoding="utf-8").strip().splitlines()
        last = json.loads(lines[-1])
        assert last["event_id"] == event_id
        assert last["turnstile_command"] == "hold"
        assert reason in last["reasons"]


def test_scenario7_guard_ui_shows_reasons():
    """Страница охраны отдаёт HTML; после verify видны event и reason."""
    client = TestClient(app)
    client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1002"])
    page = client.get("/ui/guard")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "e-1002" in page.text
    assert "quality_below_threshold" in page.text
    assert "hold" in page.text
    assert "Загрузить рисковые события" in page.text


def test_scenario7_guard_ui_empty_explains_demo_py():
    client = TestClient(app)
    page = client.get("/ui/guard")
    assert page.status_code == 200
    assert "Очередь пуста" in page.text
    assert "demo.py" in page.text


def test_scenario7_spoof_deny_does_not_auto_open():
    """Явный spoof — deny (не review), турникет всё равно не open."""
    client = TestClient(app)
    body = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1003"]).json()
    assert body["decision"] == "deny"
    assert body["turnstile_command"] == "hold"
    assert body["requires_human_review"] is False
