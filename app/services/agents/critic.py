"""Critic Agent — reviews the complete grading result for fairness.

The Critic does NOT change scores. It ONLY flags cases for teacher review.
A flagged student is still auto-graded — the teacher just sees the flag.

This agent is pure Python — no LLM. It applies deterministic rules.
"""
from __future__ import annotations

from app.services.agents.base import CheckpointResult, GradingResult


def run_critic(result: GradingResult, submission_text: str) -> GradingResult:
    """Review a GradingResult and add flags where needed.

    Rules (all deterministic):
      1. Any low-confidence checkpoint → flag
      2. Any unverified evidence on an awarded checkpoint → flag
         (catches BOTH evidence_likely_hallucinated AND no_evidence_for_awarded_marks)
      3. Student submitted content but got 0% overall → flag
      4. Any criterion where every checkpoint scored 0 but submission has content → flag
      5. More than 30% of checkpoints need review → flag whole submission
      6. Evidence hallucination flag from verifier → flag
    """
    flags: list[str] = []
    has_content = bool(submission_text and submission_text.strip())

    low_conf_checkpoints: list[str] = []
    unverified_awarded: list[str] = []
    hallucination_flags: list[str] = []

    # Track per-criterion zero scoring (C-1 fix: was collected but never used)
    # Group checkpoints by criterion to detect criteria where ALL checkpoints scored 0
    criterion_scores: dict[str, list[int]] = {}
    for cp in result.checkpoints:
        criterion_scores.setdefault(cp.criterion, []).append(cp.score_percent)

    zero_criteria: list[str] = []
    if has_content and len(submission_text) > 100:
        for crit_name, percents in criterion_scores.items():
            if percents and all(p == 0 for p in percents):
                zero_criteria.append(crit_name)

    for cp in result.checkpoints:
        # Rule 1: low confidence
        if cp.confidence == "low":
            low_conf_checkpoints.append(cp.criterion)

        # Rule 2: unverified but marks awarded
        # C-5 fix: catch BOTH hallucinated quotes AND empty evidence (no_evidence_for_awarded_marks)
        if not cp.verified and cp.points_awarded > 0 and bool(cp.flags):
            unverified_awarded.append(cp.criterion)

        # Rule 6: hallucination flag
        if "evidence_likely_hallucinated" in cp.flags:
            hallucination_flags.append(cp.criterion)

    # Flag low confidence (deduplicated criterion names)
    if low_conf_checkpoints:
        unique_low = list(dict.fromkeys(low_conf_checkpoints))
        flags.append(
            f"Low confidence grading on: {', '.join(unique_low[:3])}"
            + (f" and {len(unique_low) - 3} more" if len(unique_low) > 3 else "")
        )

    # Flag unverified awarded (deduplicated)
    if unverified_awarded:
        unique_unverified = list(dict.fromkeys(unverified_awarded))
        flags.append(
            f"Evidence could not be verified for: {', '.join(unique_unverified[:3])}"
            + (f" and {len(unique_unverified) - 3} more" if len(unique_unverified) > 3 else "")
        )

    # Flag hallucinations (deduplicated)
    if hallucination_flags:
        unique_hall = list(dict.fromkeys(hallucination_flags))
        flags.append(
            f"Possible evidence hallucination in: {', '.join(unique_hall[:3])}"
            + (f" and {len(unique_hall) - 3} more" if len(unique_hall) > 3 else "")
        )

    # C-1 fix: flag criteria where ALL checkpoints scored 0 but submission has content
    if zero_criteria:
        # Only flag if it's not the entire submission (that's caught by the next rule)
        total_criteria = len(criterion_scores)
        if len(zero_criteria) < total_criteria:
            flags.append(
                f"Zero marks on criteria with content: {', '.join(zero_criteria[:3])}"
                + (f" and {len(zero_criteria) - 3} more" if len(zero_criteria) > 3 else "")
            )

    # Flag zero overall with content
    if result.total_score == 0 and has_content:
        flags.append("Student submitted content but received zero marks — please verify")

    # Rule: high flagged checkpoint ratio → flag the whole submission
    total_cps = len(result.checkpoints)
    flagged_cps = sum(1 for cp in result.checkpoints if cp.needs_review)
    if total_cps > 0 and flagged_cps / total_cps > 0.30:
        flags.append(
            f"{flagged_cps}/{total_cps} checkpoints need review — recommend manual check"
        )

    # M-4 fix: factor verification rate into overall confidence, not just flag count
    verification_rate = result.checkpoint_stats.get("verification_rate", 100.0)
    verified_count = result.checkpoint_stats.get("verified", total_cps)
    low_verification = total_cps > 0 and verification_rate < 50.0

    if len(flags) >= 3 or low_verification:
        overall_confidence = "low"
    elif len(flags) >= 1 or (total_cps > 0 and verified_count < total_cps):
        overall_confidence = "medium"
    else:
        overall_confidence = "high"

    result.review_flags = flags
    result.needs_review = len(flags) > 0
    result.confidence = overall_confidence

    return result
