from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.app.main import app
from poc.app.pipeline import AUDIT_PATH, reset_runtime_state

REFERENCE_EVENTS = {
    "e-1001": {
        "event_id": "e-1001",
        "gate_id": "gate-2",
        "camera_id": "cam-2a",
        "captured_at": "2026-07-31T08:52:14Z",
        "frame_uri": "file://demo/frames/e-1001.jpg",
        "metadata": {
            "direction": "in",
            "illumination": "normal",
            "edge_node": "edge-gate-2",
            "network": "online",
        },
    },
    "e-1002": {
        "event_id": "e-1002",
        "gate_id": "gate-1",
        "camera_id": "cam-1b",
        "captured_at": "2026-07-31T08:57:41Z",
        "frame_uri": "file://demo/frames/e-1002.jpg",
        "metadata": {
            "direction": "in",
            "illumination": "backlight",
            "occlusion_hint": "mask",
            "edge_node": "edge-gate-1",
            "network": "online",
        },
    },
    "e-1003": {
        "event_id": "e-1003",
        "gate_id": "gate-3",
        "camera_id": "cam-3a",
        "captured_at": "2026-07-31T09:03:07Z",
        "frame_uri": "file://demo/frames/e-1003.jpg",
        "metadata": {
            "direction": "in",
            "illumination": "normal",
            "edge_node": "edge-gate-3",
            "network": "online",
        },
    },
    "e-1004": {
        "event_id": "e-1004",
        "gate_id": "gate-2",
        "camera_id": "cam-2b",
        "captured_at": "2026-07-31T09:05:22Z",
        "frame_uri": "file://demo/frames/e-1004.jpg",
        "metadata": {
            "direction": "in",
            "illumination": "dim",
            "head_pose_hint": "yaw_30",
            "edge_node": "edge-gate-2",
            "network": "online",
        },
    },
    "e-1005": {
        "event_id": "e-1005",
        "gate_id": "gate-1",
        "camera_id": "cam-1a",
        "captured_at": "2026-07-31T09:11:58Z",
        "frame_uri": "file://demo/frames/e-1005.jpg",
        "metadata": {
            "direction": "in",
            "illumination": "normal",
            "edge_node": "edge-gate-1",
            "network": "offline",
            "cache_age_minutes": 240,
            "note": "сотрудник уволен вчера, отзыв доступа мог не доехать до edge-кеша",
        },
    },
}

EXPECTED = {
    "e-1001": {"decision": "allow", "open": True, "degraded": False},
    "e-1002": {"decision": "manual_review", "open": False, "degraded": False},
    "e-1003": {"decision": "deny", "open": False, "degraded": False},
    "e-1004": {"decision": "manual_review", "open": False, "degraded": False},
    "e-1005": {"decision": "manual_review", "open": False, "degraded": True},
}


@pytest.fixture(autouse=True)
def _clean_state():
    reset_runtime_state()
    yield
    reset_runtime_state()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize("event_id", list(EXPECTED.keys()))
def test_reference_events(client: TestClient, event_id: str):
    exp = EXPECTED[event_id]
    resp = client.post("/v1/access/verify", json=REFERENCE_EVENTS[event_id])
    assert resp.status_code == 200
    body = resp.json()

    assert body["decision"] in {"allow", "deny", "manual_review"}
    assert body["decision"] == exp["decision"]
    if exp["open"]:
        assert body["turnstile_command"] == "open"
        assert body["requires_human_review"] is False
        assert body["employee_id"]
        assert body["reasons"]
    else:
        assert body["turnstile_command"] != "open"
    assert body["degraded_mode"] is exp["degraded"]

    if body["decision"] != "allow":
        assert body["turnstile_command"] != "open"

    assert AUDIT_PATH.exists()
    lines = AUDIT_PATH.read_text(encoding="utf-8").strip().splitlines()
    assert any(event_id in line for line in lines)
    last = json.loads(lines[-1])
    assert last["event_id"] == event_id
    assert last["audit_id"] == body["audit_id"]
    assert "reasons" in last
    assert "frame" not in last
    assert "frame_uri" not in last


def test_idempotent_open(client: TestClient):
    first = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1001"]).json()
    second = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1001"]).json()
    assert first["decision"] == second["decision"] == "allow"
    assert first["decision_id"] == second["decision_id"]
    assert first["audit_id"] == second["audit_id"]

    opens = 0
    for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["event_id"] == "e-1001" and rec["turnstile_command"] == "open":
            opens += 1
    assert opens == 1


def test_e1001_happy_details(client: TestClient):
    body = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1001"]).json()
    assert body["employee_id"] == "emp-4821"
    assert "quality_ok" in body["reasons"]
    assert "liveness_ok" in body["reasons"]
    assert body["decision_id"].startswith("d-")
    assert body["audit_id"].startswith("a-")
    assert 320 <= body["latency_ms"] <= 900
    assert body["match_score"] == pytest.approx(0.812, abs=1e-6)


def test_e1003_spoof_reason(client: TestClient):
    body = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1003"]).json()
    assert any("spoof" in r or "liveness" in r for r in body["reasons"])
    # early exit on spoof → latency still plausible but can be on the lower side
    assert 320 <= body["latency_ms"] <= 900


def test_e1002_quality_reason(client: TestClient):
    body = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1002"]).json()
    assert "quality_below_threshold" in body["reasons"]


def test_e1004_margin_reason(client: TestClient):
    body = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1004"]).json()
    assert "margin_too_small" in body["reasons"]


def test_ids_are_sequential(client: TestClient):
    a = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1001"]).json()
    b = client.post("/v1/access/verify", json=REFERENCE_EVENTS["e-1002"]).json()
    assert a["decision_id"] == "d-70001"
    assert a["audit_id"] == "a-70001"
    assert b["decision_id"] == "d-70002"
    assert b["audit_id"] == "a-70002"


def test_unknown_event_fail_closed(client: TestClient):
    payload = {
        "event_id": "e-unknown",
        "gate_id": "gate-1",
        "camera_id": "cam-1a",
        "captured_at": "2026-07-31T09:00:00Z",
        "frame_uri": "file://demo/frames/missing.jpg",
        "metadata": {"network": "online", "edge_node": "edge-gate-1"},
    }
    body = client.post("/v1/access/verify", json=payload).json()
    assert body["decision"] == "manual_review"
    assert body["turnstile_command"] != "open"
    assert "no_face_detected" in body["reasons"]


def test_health_exposes_model_version(client: TestClient):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["model_version"] == "demo-fixture-v1"
    assert "policy_version" in body
