from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Named thresholds — same idea as production policy, values are demo-calibrated.
T_ALLOW = 0.75
M_MIN = 0.12
Q_MIN = 0.55
L_ALLOW = 0.80
L_SPOOF = 0.35
CACHE_STALE_MINUTES = 120


@dataclass
class MatchInfo:
    employee_id: Optional[str]
    match_score: Optional[float]
    margin: Optional[float]


@dataclass
class DecisionResult:
    decision: str
    turnstile_command: str
    requires_human_review: bool
    degraded_mode: bool
    reasons: list[str]
    employee_id: Optional[str]
    match_score: Optional[float]
    margin_to_second_best: Optional[float]


def decide(
    *,
    face_detected: bool,
    quality_score: float,
    liveness_score: float,
    match: MatchInfo,
    policy_allowed: bool,
    network: str,
    cache_age_minutes: Optional[float],
) -> DecisionResult:
    reasons: list[str] = []
    degraded = network == "offline" or (
        cache_age_minutes is not None and cache_age_minutes > CACHE_STALE_MINUTES
    )

    if not face_detected:
        return _review_or_deny(
            decision="manual_review",
            reasons=["no_face_detected"],
            degraded=degraded,
            match=match,
        )

    if quality_score < Q_MIN:
        reasons.append("quality_below_threshold")
        return _review_or_deny(
            decision="manual_review",
            reasons=reasons,
            degraded=degraded,
            match=match,
        )
    reasons.append("quality_ok")

    if liveness_score <= L_SPOOF:
        reasons.append("liveness_spoof_suspected")
        return _review_or_deny(
            decision="deny",
            reasons=reasons,
            degraded=degraded,
            match=match,
        )

    if liveness_score < L_ALLOW:
        reasons.append("liveness_uncertain")
        return _review_or_deny(
            decision="manual_review",
            reasons=reasons,
            degraded=degraded,
            match=match,
        )
    reasons.append("liveness_ok")

    if match.employee_id is None or match.match_score is None:
        reasons.append("no_match")
        return _review_or_deny(
            decision="manual_review",
            reasons=reasons,
            degraded=degraded,
            match=match,
        )

    if match.match_score < T_ALLOW:
        reasons.append("match_below_allow_threshold")
        return _review_or_deny(
            decision="manual_review",
            reasons=reasons,
            degraded=degraded,
            match=match,
        )
    reasons.append("match_above_allow_threshold")

    margin = match.margin if match.margin is not None else 0.0
    if margin < M_MIN:
        reasons.append("margin_too_small")
        return _review_or_deny(
            decision="manual_review",
            reasons=reasons,
            degraded=degraded,
            match=match,
        )
    reasons.append("margin_ok")

    if not policy_allowed:
        reasons.append("policy_denied")
        return _review_or_deny(
            decision="deny",
            reasons=reasons,
            degraded=degraded,
            match=match,
        )
    reasons.append("policy_ok")

    if degraded:
        reasons.append("stale_or_offline_cache")
        return DecisionResult(
            decision="manual_review",
            turnstile_command="hold",
            requires_human_review=True,
            degraded_mode=True,
            reasons=reasons,
            employee_id=match.employee_id,
            match_score=match.match_score,
            margin_to_second_best=match.margin,
        )

    return DecisionResult(
        decision="allow",
        turnstile_command="open",
        requires_human_review=False,
        degraded_mode=False,
        reasons=reasons,
        employee_id=match.employee_id,
        match_score=match.match_score,
        margin_to_second_best=match.margin,
    )


def _review_or_deny(
    *,
    decision: str,
    reasons: list[str],
    degraded: bool,
    match: MatchInfo,
) -> DecisionResult:
    return DecisionResult(
        decision=decision,
        turnstile_command="hold",
        requires_human_review=decision == "manual_review",
        degraded_mode=degraded,
        reasons=reasons,
        employee_id=match.employee_id,
        match_score=match.match_score,
        margin_to_second_best=match.margin,
    )
