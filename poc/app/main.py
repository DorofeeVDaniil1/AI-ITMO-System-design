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
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "policy_version": gallery_store.policy_version,
    }


@app.get("/metrics")
def get_metrics() -> dict[str, object]:
    snap = metrics.snapshot()
    snap["policy_version"] = gallery_store.policy_version
    snap["guard_queue_open"] = len(guard_queue.list_open())
    return snap


@app.post("/v1/access/verify", response_model=AccessVerifyResponse)
def verify_access(body: AccessVerifyRequest) -> AccessVerifyResponse:
    return process_event(body)


@app.post("/v1/admin/revoke", response_model=RevokeResponse)
def revoke_access(body: RevokeRequest) -> RevokeResponse:
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
    return {"items": guard_queue.list_open()}


@app.post("/v1/guard/review/{event_id}", response_model=GuardResolveResponse)
def guard_resolve(event_id: str, body: GuardResolveRequest) -> GuardResolveResponse:
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

    turnstile_status = None
    if body.action == "open":
        ack = turnstile.apply(event_id=event_id, command="open")
        turnstile_status = ack.status
    else:
        ack = turnstile.apply(event_id=event_id, command="hold")
        turnstile_status = ack.status

    return GuardResolveResponse(
        event_id=event_id,
        status="resolved",
        operator_action=body.action,
        turnstile_status=turnstile_status,
    )
