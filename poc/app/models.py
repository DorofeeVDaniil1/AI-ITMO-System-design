"""
models.py — контракты API (Pydantic).

AccessVerify* — референс из ТЗ (POST /v1/access/verify).
Остальные модели — prod-shaped обвязка: revoke, guard, ответы с turnstile_status.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


Decision = Literal["allow", "deny", "manual_review"]
TurnstileCommand = Literal["open", "hold"]


class QualityBlock(BaseModel):
    """Кусок ответа про качество кадра и liveness (в PoC часто из fixture)."""

    face_detected: bool
    quality_score: float
    liveness_score: float


class AccessVerifyRequest(BaseModel):
    """Вход события с камеры / edge-узла (как в ТЗ)."""

    event_id: str
    gate_id: str
    camera_id: str
    captured_at: str
    frame_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AccessVerifyResponse(BaseModel):
    """Ответ verify: решение + команда турникету + audit_id."""

    event_id: str
    decision_id: str
    decision: Decision
    employee_id: Optional[str] = None
    match_score: Optional[float] = None
    margin_to_second_best: Optional[float] = None
    quality: QualityBlock
    reasons: list[str]
    turnstile_command: TurnstileCommand
    requires_human_review: bool
    degraded_mode: bool
    audit_id: str
    latency_ms: int
    # Доп. поля «как на edge» — ТЗ их не запрещает
    turnstile_status: Optional[str] = None  # opened / held / duplicate / ...
    policy_version: Optional[int] = None


class RevokeRequest(BaseModel):
    """Админский отзыв доступа: убрать шаблон с локального edge-кеша."""

    employee_id: str
    reason: str = "access_revoked"


class RevokeResponse(BaseModel):
    employee_id: str
    revoked: bool
    policy_version: int


class GuardResolveRequest(BaseModel):
    """Решение охраны по событию из очереди review."""

    action: Literal["open", "deny"]
    operator_id: str = "guard-1"


class GuardResolveResponse(BaseModel):
    event_id: str
    status: str
    operator_action: str
    turnstile_status: Optional[str] = None
