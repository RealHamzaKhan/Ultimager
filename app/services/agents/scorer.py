"""Scorer Agent — pure deterministic Python. No LLM. Ever.

Takes verified CheckpointResults → calculates final score.
Same input ALWAYS produces same output. No randomness possible.
"""
from __future__ import annotations

import logging

from app.services.agents.base import CheckpointResult, GradingResult

logger = logging.getLogger(__name__)


def run_scorer(
    checkpoints: list[CheckpointResult],
    criteria: list[dict],
    student_id: int,
    session_id: int,
    max_score: float,
    overall_feedback: str = "",
    strengths: list = None,
    weaknesses: list = None,
) -> GradingResult:
    """Calculate final score from checkpoint results.

    Partial credit is supported — each checkpoint can award 0/25/50/75/100%
    of its points_max. Score is always deterministic.
    """
    total = 0.0

    # Build rubric breakdown grouped by criterion
    by_criterion: dict[str, list[CheckpointResult]] = {}
    for cp in checkpoints:
        by_criterion.setdefault(cp.criterion, []).append(cp)

    # Flow-audit gap #5 fix: detect criteria/checkpoint mismatches.
    # Log warnings for orphaned checkpoints (in results but not in rubric) and
    # missing criteria (in rubric but no checkpoints produced results).
    criteria_names = {str(c.get("criterion", "")).strip() for c in criteria if c.get("criterion")}
    checkpoint_criteria = set(by_criterion.keys())

    orphaned = checkpoint_criteria - criteria_names
    if orphaned:
        logger.warning(
            "Scorer: checkpoints exist for criteria NOT in rubric (will be ignored): %s",
            sorted(orphaned),
        )

    missing = criteria_names - checkpoint_criteria
    if missing:
        logger.warning(
            "Scorer: rubric criteria have NO checkpoint results (will score 0): %s",
            sorted(missing),
        )

    rubric_breakdown = []
    for crit in criteria:
        crit_name = crit.get("criterion", "")
        crit_max = float(crit.get("max", 0))
        crit_checkpoints = by_criterion.get(crit_name, [])

        crit_score = sum(cp.points_awarded for cp in crit_checkpoints)
        # Detect and disclose score capping
        score_capped = crit_score > crit_max
        if score_capped:
            logger.info(
                "Scorer: criterion '%s' checkpoints summed to %.2f, exceeds max %.2f — capping",
                crit_name, crit_score, crit_max,
            )
        # Never exceed the criterion maximum
        crit_score = min(crit_score, crit_max)
        total += crit_score

        not_evaluated = len(crit_checkpoints) == 0
        entry: dict = {
            "criterion": crit_name,
            "score": round(crit_score, 2),
            "max": crit_max,
            "checkpoints": [_cp_to_dict(cp) for cp in crit_checkpoints],
            "not_evaluated": not_evaluated,
            "score_capped": score_capped,
        }
        if not_evaluated:
            entry["justification"] = (
                "This criterion was not evaluated by the AI — no grading checkpoints were generated. "
                "Manual grading required."
            )
            entry["flagged"] = True
            entry["flag_reasons"] = ["not_evaluated_by_ai"]
        rubric_breakdown.append(entry)

    # Cap total at max_score
    total = min(round(total, 2), max_score)
    pct = round((total / max_score * 100) if max_score > 0 else 0, 1)

    # Checkpoint statistics
    total_cps = len(checkpoints)
    verified_cps = sum(1 for cp in checkpoints if cp.verified)
    retried_cps = sum(1 for cp in checkpoints if cp.retry_count > 0)
    flagged_cps = sum(1 for cp in checkpoints if cp.needs_review)
    full_credit = sum(1 for cp in checkpoints if cp.score_percent == 100)
    partial_credit = sum(1 for cp in checkpoints if 0 < cp.score_percent < 100)
    no_credit = sum(1 for cp in checkpoints if cp.score_percent == 0)

    # Build review flags for unevaluated criteria
    unevaluated = [item["criterion"] for item in rubric_breakdown if item.get("not_evaluated")]
    review_flags = []
    needs_review_flag = flagged_cps > 0
    if unevaluated:
        needs_review_flag = True
        review_flags.append(
            f"{len(unevaluated)} criterion/criteria not evaluated by AI: "
            f"{', '.join(unevaluated)}. Manual grading required."
        )

    result = GradingResult(
        student_id=student_id,
        session_id=session_id,
        total_score=total,
        max_score=max_score,
        score_percent=pct,
        checkpoints=checkpoints,
        rubric_breakdown=rubric_breakdown,
        overall_feedback=overall_feedback,
        strengths=strengths or [],
        weaknesses=weaknesses or [],
        needs_review=needs_review_flag,
        review_flags=review_flags,
        grading_method="multi_agent",
        checkpoint_stats={
            "total": total_cps,
            "verified": verified_cps,
            "retried": retried_cps,
            "flagged": flagged_cps,
            "full_credit": full_credit,
            "partial_credit": partial_credit,
            "no_credit": no_credit,
            "verification_rate": round(verified_cps / total_cps * 100 if total_cps else 0, 1),
        },
    )

    return result


def _cp_to_dict(cp: CheckpointResult) -> dict:
    return {
        "id": cp.checkpoint_id,
        # H-5 fix: use the specific checkpoint description (what was being checked),
        # not the rubric criterion name (which is the same for all checkpoints in a group).
        # Fall back to criterion name only if description was not populated.
        "description": cp.description if cp.description else cp.criterion,
        "criterion": cp.criterion,          # keep rubric group name available too
        "points": cp.points_max,
        "points_awarded": cp.points_awarded,
        "score_percent": cp.score_percent,
        "pass": cp.passed,
        "verified": cp.verified,
        "verification_method": cp.verification_method,
        "evidence_quote": cp.evidence_quote,
        "source_file": cp.source_file,
        "reasoning": cp.reasoning,
        "confidence": cp.confidence,
        "retry_count": cp.retry_count,
        "flagged": cp.needs_review,
        "flags": cp.flags,
        "model_used": cp.model_used,
        "judge_truncated": getattr(cp, "judge_truncated", False),
    }
