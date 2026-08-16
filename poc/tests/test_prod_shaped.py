"""
test_prod_shaped.py — слой «ближе к edge», не замена smoke по ТЗ.

Турникет ack, revoke шаблона, очередь охраны, /metrics.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from poc.app.main import app
from poc.app.pipeline import reset_runtime_state
from poc.tests.test_smoke import REFERENCE_EVENTS


def setup_function() -> None:
    reset_runtime_state()


def test_turnstile_ack_on_allow():
    client = TestClient(app)
    body = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1001"]).json()
    assert body["decision"] == "allow"
    assert body["turnstile_status"] == "opened"
    assert body["policy_version"] == 1


def test_turnstile_hold_on_review():
    client = TestClient(app)
    body = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1002"]).json()
    assert body["decision"] == "manual_review"
    assert body["turnstile_status"] == "held"
    queue = client.get("/v1/guard/queue").json()["items"]
    assert any(i["event_id"] == "e-1002" for i in queue)


def test_revoke_blocks_later_allow():
    client = TestClient(app)
    rev = client.post(
        "/v1/admin/revoke", json={"employee_id": "emp-4821", "reason": "fired"}
    ).json()
    assert rev["revoked"] is True
    assert rev["policy_version"] >= 2

    body = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1001"]).json()
    # fixture would match emp-4821, but template is gone from edge cache
    assert body["turnstile_command"] != "open"
    assert body["decision"] in {"manual_review", "deny"}


def test_guard_can_open_after_review():
    client = TestClient(app)
    client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1004"]).json()
    resolved = client.post(
        "/v1/guard/review/e-1004",
        json={"action": "open", "operator_id": "guard-7"},
    ).json()
    assert resolved["status"] == "resolved"
    assert resolved["turnstile_status"] == "opened"
    assert client.get("/v1/guard/queue").json()["items"] == []


def test_metrics_endpoint():
    client = TestClient(app)
    client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1001"])
    client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1003"])
    m = client.get("/metrics").json()
    assert m["decisions"].get("allow", 0) >= 1
    assert m["decisions"].get("deny", 0) >= 1
    assert m["turnstile_open_ok"] >= 1
    assert "policy_version" in m
