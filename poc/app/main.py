"""
main.py — HTTP API (FastAPI).

Эндпоинты:
  GET  /health              — жив ли сервис + версии
  GET  /metrics             — простые счётчики (вместо Prometheus на демо)
  POST /v1/access/verify    — главный контракт ТЗ
  POST /v1/admin/revoke     — снять шаблон с edge-кеша
  GET  /v1/guard/queue      — очередь manual_review
  POST /v1/guard/review/{id}— решение охраны open/deny
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from poc.app.metrics import metrics
from poc.app.models import (
    AccessVerifyRequest,
    AccessVerifyResponse,
    GuardResolveRequest,
    GuardResolveResponse,
    RevokeRequest,
    RevokeResponse,
)
from poc.app.pipeline import MODEL_VERSION, append_operator_audit, process_event
from poc.app.store import gallery_store, guard_queue
from poc.app.turnstile import turnstile

app = FastAPI(
    title="Face Gate PoC",
    version="0.2.0",
    description="Policy PoC + prod-shaped edge pieces (turnstile ack, revoke, guard queue, metrics).",
)


@app.get("/health")
def health() -> dict[str, object]:
    """Проверка живости + какая «модель»/policy сейчас на узле."""
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "policy_version": gallery_store.policy_version,
    }


@app.get("/metrics")
def get_metrics() -> dict[str, object]:
    """Счётчики для демо-наблюдаемости (в проде — Prometheus)."""
    snap = metrics.snapshot()
    snap["policy_version"] = gallery_store.policy_version
    snap["guard_queue_open"] = len(guard_queue.list_open())
    return snap


@app.post("/v1/access/verify", response_model=AccessVerifyResponse)
def verify_access(body: AccessVerifyRequest) -> AccessVerifyResponse:
    """Кадр/событие → решение → команда турникету."""
    return process_event(body)


@app.post("/v1/admin/revoke", response_model=RevokeResponse)
def revoke_access(body: RevokeRequest) -> RevokeResponse:
    """
    Модель приоритетного отзыва с центра на edge:
    шаблон пропадает из локального кеша, policy_version растёт.
    """
    revoked = gallery_store.revoke(body.employee_id)
    if revoked:
        metrics.revocations += 1
    return RevokeResponse(
        employee_id=body.employee_id,
        revoked=revoked,
        policy_version=gallery_store.policy_version,
    )


@app.get("/v1/guard/queue")
def guard_queue_list() -> dict[str, object]:
    """Что сейчас ждёт ручной разбор (вместо UI охраны)."""
    return {"items": guard_queue.list_open()}


@app.post("/v1/guard/review/{event_id}", response_model=GuardResolveResponse)
def guard_resolve(event_id: str, body: GuardResolveRequest) -> GuardResolveResponse:
    """Охранник подтвердил open или окончательный deny — пишем audit + турникет."""
    item = guard_queue.resolve(
        event_id, action=body.action, operator_id=body.operator_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="no open review for event_id")

    metrics.guard_actions += 1
    append_operator_audit(
        event_id=event_id,
        action=body.action,
        operator_id=body.operator_id,
        audit_id=item.get("audit_id", "a-unknown"),
    )

    if body.action == "open":
        ack = turnstile.apply(event_id=event_id, command="open")
    else:
        ack = turnstile.apply(event_id=event_id, command="hold")

    return GuardResolveResponse(
        event_id=event_id,
        status="resolved",
        operator_action=body.action,
        turnstile_status=ack.status,
    )
