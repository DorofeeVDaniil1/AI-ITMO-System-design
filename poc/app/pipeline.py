"""
pipeline.py — склейка одного прохода: fixture/scores → decide → турникет → audit.

Это «горячий путь» PoC в одном процессе:
1) берём событие (часто scores из events.json);
2) считаем policy через decision.decide;
3) шлём команду в симулятор турникета (ack);
4) если review — кладём в очередь охраны;
5) пишем JSONL audit без сырого кадра.

Идемпотентность: повтор того же event_id → тот же ответ, второе open не шлём.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from poc.app.decision import MatchInfo, decide
from poc.app.metrics import metrics
from poc.app.models import AccessVerifyRequest, AccessVerifyResponse, QualityBlock
from poc.app.store import gallery_store, guard_queue
from poc.app.turnstile import turnstile

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
EVENTS_PATH = DATA_DIR / "events.json"
AUDIT_PATH = DATA_DIR / "audit.jsonl"
MODEL_VERSION = "demo-fixture-v1"  # в проде — хеш/тег весов на edge

_event_fixtures: Optional[dict[str, Any]] = None
_seen_events: dict[str, AccessVerifyResponse] = {}  # кеш ответов по event_id
_next_decision_n = 70001
_next_audit_n = 70001


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def get_event_fixtures() -> dict[str, Any]:
    """Детерминированные scores для e-1001…e-1005 из ТЗ."""
    global _event_fixtures
    if _event_fixtures is None:
        _event_fixtures = _load_json(EVENTS_PATH)
    return _event_fixtures


def reset_runtime_state() -> None:
    """Сброс всего in-memory состояния — нужно тестам между кейсами."""
    global _event_fixtures, _next_decision_n, _next_audit_n
    _seen_events.clear()
    _event_fixtures = None
    _next_decision_n = 70001
    _next_audit_n = 70001
    gallery_store.reset()
    turnstile.reset()
    guard_queue.reset()
    metrics.reset()
    if AUDIT_PATH.exists():
        AUDIT_PATH.unlink()


def _next_ids() -> tuple[str, str]:
    """Порядковые id как в примере ТЗ: d-70001, a-70001."""
    global _next_decision_n, _next_audit_n
    decision_id = f"d-{_next_decision_n}"
    audit_id = f"a-{_next_audit_n}"
    _next_decision_n += 1
    _next_audit_n += 1
    return decision_id, audit_id


def _demo_latency_ms(event_id: str, wall_ms: int, *, early_exit: bool) -> int:
    """
    Правдоподобный latency_ms без time.sleep (CI не тормозит).

    Симулируем бюджет стадий edge-GPU; seed от event_id → стабильно в тестах.
    """
    rng = random.Random(event_id)
    detect = rng.randint(35, 55)
    quality = rng.randint(15, 30)
    liveness = rng.randint(70, 110)
    embed = rng.randint(180, 260)
    match = rng.randint(20, 45)
    policy = rng.randint(3, 8)
    if early_exit:
        # quality/spoof отвалились рано — «короткий» путь
        stages = detect + quality + liveness
    else:
        stages = detect + quality + liveness + embed + match + policy
    jitter = rng.randint(-25, 40)
    return max(wall_ms, stages + jitter, 320)


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Сходство двух эмбеддингов (для пути без match_override)."""
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _employee_enrolled(employee_id: Optional[str]) -> bool:
    """Есть ли шаблон в локальном кеше (после revoke — нет)."""
    if not employee_id:
        return False
    return any(e["employee_id"] == employee_id for e in gallery_store.employees())


def _match_gallery(probe: list[float], gate_id: str) -> tuple[MatchInfo, bool]:
    """Грубый 1:N: top-1, margin до top-2, флаг policy по gate."""
    scores: list[tuple[str, float, bool]] = []
    probe_v = np.asarray(probe, dtype=float)
    for emp in gallery_store.employees():
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
    """Одна строка JSONL = одно решение/действие. Кадр не пишем специально."""
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def process_event(req: AccessVerifyRequest) -> AccessVerifyResponse:
    """Обработка одного события доступа end-to-end."""
    # Повтор того же event_id — возвращаем закешированный ответ (идемпотентность)
    if req.event_id in _seen_events:
        return _seen_events[req.event_id]

    t0 = time.perf_counter()
    fixtures = get_event_fixtures()
    fx = fixtures.get(req.event_id)

    if fx is None:
        # Неизвестное событие без fixture → fail-closed (нет лица)
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
        cache_age = gallery_store.default_cache_age()
    cache_age_f = float(cache_age) if cache_age is not None else None

    # Demo-события ТЗ задают scores явно (match_override), чтобы не ломаться на игрушечных векторах.
    # Если сотрудника уже revoke'нули — шаблона нет → match сбрасываем.
    if match_override is not None:
        emp_id = match_override.get("employee_id")
        enrolled = _employee_enrolled(emp_id)
        match = MatchInfo(
            emp_id if enrolled else None,
            float(match_override["match_score"]) if enrolled else None,
            float(match_override["margin"]) if enrolled else None,
        )
        policy_allowed = bool(match_override.get("policy_allowed", True)) and enrolled
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

    wall_ms = int((time.perf_counter() - t0) * 1000)
    early_exit = (
        "quality_below_threshold" in result.reasons
        or "liveness_spoof_suspected" in result.reasons
        or "no_face_detected" in result.reasons
    )
    latency_ms = _demo_latency_ms(req.event_id, wall_ms, early_exit=early_exit)
    decision_id, audit_id = _next_ids()

    # Железо турникета здесь — симулятор с ack
    ack = turnstile.apply(event_id=req.event_id, command=result.turnstile_command)
    metrics.inc_decision(result.decision)

    if result.requires_human_review:
        guard_queue.enqueue(
            req.event_id,
            {
                "event_id": req.event_id,
                "decision_id": decision_id,
                "audit_id": audit_id,
                "gate_id": req.gate_id,
                "camera_id": req.camera_id,
                "reasons": result.reasons,
                "employee_id": result.employee_id,
                "status": "open",
            },
        )

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
        turnstile_status=ack.status,
        policy_version=gallery_store.policy_version,
    )

    _append_audit(
        {
            "audit_id": audit_id,
            "event_id": req.event_id,
            "decision_id": decision_id,
            "decision": result.decision,
            "turnstile_command": result.turnstile_command,
            "turnstile_ack": {
                "accepted": ack.accepted,
                "status": ack.status,
                "detail": ack.detail,
            },
            "employee_id": result.employee_id,
            "match_score": result.match_score,
            "margin_to_second_best": result.margin_to_second_best,
            "reasons": result.reasons,
            "degraded_mode": result.degraded_mode,
            "gate_id": req.gate_id,
            "camera_id": req.camera_id,
            "network": network,
            "cache_age_minutes": cache_age_f,
            "model_version": MODEL_VERSION,
            "policy_version": gallery_store.policy_version,
            "latency_ms": latency_ms,
        }
    )

    _seen_events[req.event_id] = response
    return response


def append_operator_audit(
    *,
    event_id: str,
    action: str,
    operator_id: str,
    audit_id: str,
) -> None:
    """Дописать в audit действие охраны (после /v1/guard/review)."""
    _append_audit(
        {
            "audit_id": f"{audit_id}-op",
            "event_id": event_id,
            "type": "operator_action",
            "operator_id": operator_id,
            "operator_action": action,
            "policy_version": gallery_store.policy_version,
        }
    )
