from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import numpy as np

from poc.app.decision import MatchInfo, decide
from poc.app.models import AccessVerifyRequest, AccessVerifyResponse, QualityBlock

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
GALLERY_PATH = DATA_DIR / "gallery.json"
EVENTS_PATH = DATA_DIR / "events.json"
AUDIT_PATH = DATA_DIR / "audit.jsonl"

_gallery: Optional[dict[str, Any]] = None
_event_fixtures: Optional[dict[str, Any]] = None
_seen_events: dict[str, AccessVerifyResponse] = {}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def get_gallery() -> dict[str, Any]:
    global _gallery
    if _gallery is None:
        _gallery = _load_json(GALLERY_PATH)
    return _gallery


def get_event_fixtures() -> dict[str, Any]:
    global _event_fixtures
    if _event_fixtures is None:
        _event_fixtures = _load_json(EVENTS_PATH)
    return _event_fixtures


def reset_runtime_state() -> None:
    """For tests: clear idempotency cache, fixtures cache, and audit file."""
    global _gallery, _event_fixtures
    _seen_events.clear()
    _gallery = None
    _event_fixtures = None
    if AUDIT_PATH.exists():
        AUDIT_PATH.unlink()


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _match_gallery(probe: list[float], gate_id: str) -> tuple[MatchInfo, bool]:
    gallery = get_gallery()
    scores: list[tuple[str, float, bool]] = []
    probe_v = np.asarray(probe, dtype=float)
    for emp in gallery["employees"]:
        emb = np.asarray(emp["embedding"], dtype=float)
        score = _cosine(probe_v, emb)
        allowed = gate_id in emp.get("gates_allowed", [])
        scores.append((emp["employee_id"], score, allowed))
    scores.sort(key=lambda x: x[1], reverse=True)
    if not scores:
        return MatchInfo(None, None, None), False
    best_id, best_score, best_allowed = scores[0]
    second = scores[1][1] if len(scores) > 1 else 0.0
    margin = best_score - second
    return MatchInfo(best_id, best_score, margin), best_allowed


def _append_audit(record: dict[str, Any]) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def process_event(req: AccessVerifyRequest) -> AccessVerifyResponse:
    if req.event_id in _seen_events:
        return _seen_events[req.event_id]

    t0 = time.perf_counter()
    fixtures = get_event_fixtures()
    fx = fixtures.get(req.event_id)

    if fx is None:
        # Unknown event: fail closed without inventing a face.
        face_detected = False
        quality_score = 0.0
        liveness_score = 0.0
        match_override = None
        probe = None
    else:
        face_detected = bool(fx["face_detected"])
        quality_score = float(fx["quality_score"])
        liveness_score = float(fx["liveness_score"])
        match_override = fx.get("match_override")
        probe = fx.get("probe_embedding")

    meta = req.metadata or {}
    network = str(meta.get("network", "online"))
    cache_age = meta.get("cache_age_minutes")
    if cache_age is None:
        cache_age = get_gallery().get("cache_age_minutes_default", 5)
    cache_age_f = float(cache_age) if cache_age is not None else None

    # Demo events pin scores so TZ scenarios stay deterministic.
    # Without override we still do cosine against gallery.json (real match path).
    if match_override is not None:
        match = MatchInfo(
            match_override.get("employee_id"),
            float(match_override["match_score"]),
            float(match_override["margin"]),
        )
        policy_allowed = bool(match_override.get("policy_allowed", True))
    elif probe is not None and face_detected:
        match, policy_allowed = _match_gallery(probe, req.gate_id)
    else:
        match, policy_allowed = MatchInfo(None, None, None), False

    result = decide(
        face_detected=face_detected,
        quality_score=quality_score,
        liveness_score=liveness_score,
        match=match,
        policy_allowed=policy_allowed,
        network=network,
        cache_age_minutes=cache_age_f,
    )

    decision_id = f"d-{uuid.uuid4().hex[:8]}"
    audit_id = f"a-{uuid.uuid4().hex[:8]}"
    latency_ms = int((time.perf_counter() - t0) * 1000)

    response = AccessVerifyResponse(
        event_id=req.event_id,
        decision_id=decision_id,
        decision=result.decision,  # type: ignore[arg-type]
        employee_id=result.employee_id,
        match_score=result.match_score,
        margin_to_second_best=result.margin_to_second_best,
        quality=QualityBlock(
            face_detected=face_detected,
            quality_score=quality_score,
            liveness_score=liveness_score,
        ),
        reasons=result.reasons,
        turnstile_command=result.turnstile_command,  # type: ignore[arg-type]
        requires_human_review=result.requires_human_review,
        degraded_mode=result.degraded_mode,
        audit_id=audit_id,
        latency_ms=latency_ms,
    )

    _append_audit(
        {
            "audit_id": audit_id,
            "event_id": req.event_id,
            "decision_id": decision_id,
            "decision": result.decision,
            "turnstile_command": result.turnstile_command,
            "employee_id": result.employee_id,
            "match_score": result.match_score,
            "margin_to_second_best": result.margin_to_second_best,
            "reasons": result.reasons,
            "degraded_mode": result.degraded_mode,
            "gate_id": req.gate_id,
            "camera_id": req.camera_id,
            # no raw frame on purpose
        }
    )

    _seen_events[req.event_id] = response
    return response
