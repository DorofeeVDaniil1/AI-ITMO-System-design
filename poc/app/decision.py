"""
decision.py — чистое правило allow / deny / manual_review.

Сюда НЕ ходят нейросети. На вход уже готовые scores (quality, liveness, match).
Задача модуля: применить пороги и fail-closed логику как на edge-policy.

Порядок гейтов важен: сначала качество и liveness, потом матч/margin,
потом policy, в конце свежесть кеша (даже хороший match при stale → review).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Пороги как именованные константы — в проде калибруются на holdout по identity.
T_ALLOW = 0.75  # минимальный score top-1 для авто-открытия
M_MIN = 0.12  # минимальный отрыв top-1 от top-2 (иначе «два кандидата»)
Q_MIN = 0.55  # ниже — кадр слишком плохой, allow нельзя
L_ALLOW = 0.80  # liveness достаточно «живой» для allow
L_SPOOF = 0.35  # ниже/равно — считаем spoof → deny (не review)
CACHE_STALE_MINUTES = 120  # старше — кеш шаблонов/policy не доверяем для auto-open


@dataclass
class MatchInfo:
    """Результат 1:N поиска: кто ближе и насколько оторвался от второго."""

    employee_id: Optional[str]
    match_score: Optional[float]
    margin: Optional[float]  # score_1 - score_2


@dataclass
class DecisionResult:
    """Итог policy: решение, команда турникету, причины для audit."""

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
    """
    Главная функция политики.

    allow только если ВСЕ гейты зелёные.
    Иначе hold: manual_review (серая зона) или deny (явный spoof / policy).
    """
    reasons: list[str] = []
    # degraded = работаем не в «полном» режиме доверия к кешу/сети
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

    # Явный spoof жёстче, чем «сомнительный» liveness
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

    # Важно: даже идеальный match при stale/offline → review (отзыв мог не доехать)
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
    """Любой не-allow путь: турникет hold, review только если decision=manual_review."""
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
