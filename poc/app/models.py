from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


Decision = Literal["allow", "deny", "manual_review"]
TurnstileCommand = Literal["open", "hold"]


class QualityBlock(BaseModel):
    face_detected: bool
    quality_score: float
    liveness_score: float


class AccessVerifyRequest(BaseModel):
    event_id: str
    gate_id: str
    camera_id: str
    captured_at: str
    frame_uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AccessVerifyResponse(BaseModel):
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
