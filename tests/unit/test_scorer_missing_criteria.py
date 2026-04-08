"""Tests for B5 (missing criteria) and B6 (score capping).

B5: Criteria with no checkpoints should get not_evaluated=True in rubric_breakdown.
B6: When checkpoints exceed criterion max, score_capped=True should be set.
"""
import pytest
from app.services.agents.base import CheckpointResult
from app.services.agents.scorer import run_scorer


def test_missing_criterion_has_not_evaluated_flag():
    """A rubric criterion with no checkpoints must appear in rubric_breakdown with not_evaluated=True."""
    criteria = [
        {"criterion": "Q1 - Implementation", "max": 10},
        {"criterion": "Q2 - Testing", "max": 5},  # No checkpoints
    ]
    checkpoints = [
        CheckpointResult(
            checkpoint_id="cp1",
            criterion="Q1 - Implementation",
            description="Implements correctly",
            points_max=10,
            points_awarded=7,
            score_percent=75,
            verified=True,
            verification_method="exact",
        )
        # Q2 has NO checkpoints
    ]

    result = run_scorer(
        checkpoints=checkpoints,
        criteria=criteria,
        student_id=1,
        session_id=1,
        max_score=15,
    )

    breakdown = {item["criterion"]: item for item in result.rubric_breakdown}
    assert "Q2 - Testing" in breakdown
    q2 = breakdown["Q2 - Testing"]
    assert q2.get("not_evaluated") is True, "Missing criterion must have not_evaluated=True"
    assert q2.get("score") == 0


def test_evaluated_criterion_does_not_have_not_evaluated_flag():
    """A criterion with checkpoints must NOT have not_evaluated=True."""
    criteria = [{"criterion": "Q1 - Implementation", "max": 10}]
    checkpoints = [
        CheckpointResult(
            checkpoint_id="cp1",
            criterion="Q1 - Implementation",
            description="Implements correctly",
            points_max=10,
            points_awarded=8,
            score_percent=75,
            verified=True,
            verification_method="exact",
        )
    ]
    result = run_scorer(
        checkpoints=checkpoints,
        criteria=criteria,
        student_id=1,
        session_id=1,
        max_score=10,
    )
    breakdown = {item["criterion"]: item for item in result.rubric_breakdown}
    assert breakdown["Q1 - Implementation"].get("not_evaluated") is not True


def test_missing_criterion_sets_needs_review_and_review_flags():
    """When a criterion is not evaluated, GradingResult.needs_review must be True and flags set."""
    criteria = [
        {"criterion": "Q1 - Impl", "max": 10},
        {"criterion": "Q2 - No Checkpoints", "max": 5},
    ]
    checkpoints = [
        CheckpointResult(
            checkpoint_id="cp1",
            criterion="Q1 - Impl",
            description="Q1 check",
            points_max=10,
            points_awarded=5,
            score_percent=50,
            verified=True,
            verification_method="exact",
        )
    ]
    result = run_scorer(
        checkpoints=checkpoints,
        criteria=criteria,
        student_id=1,
        session_id=1,
        max_score=15,
    )
    assert result.needs_review is True
    assert any("not evaluated" in flag.lower() for flag in result.review_flags), (
        f"Expected a 'not evaluated' review flag but got: {result.review_flags}"
    )


def test_score_capped_flag_set_when_checkpoints_exceed_max():
    """When checkpoints sum exceeds criterion max, score_capped must be True in rubric_breakdown."""
    criteria = [{"criterion": "Q1 - Impl", "max": 5}]
    checkpoints = [
        CheckpointResult(
            checkpoint_id="cp1",
            criterion="Q1 - Impl",
            description="Part A",
            points_max=5,
            points_awarded=5.0,
            score_percent=100,
            verified=True,
            verification_method="exact",
        ),
        CheckpointResult(
            checkpoint_id="cp2",
            criterion="Q1 - Impl",
            description="Part B",
            points_max=3,
            points_awarded=3.0,
            score_percent=100,
            verified=True,
            verification_method="exact",
        ),
    ]
    result = run_scorer(
        checkpoints=checkpoints,
        criteria=criteria,
        student_id=1,
        session_id=1,
        max_score=5,
    )
    breakdown = {item["criterion"]: item for item in result.rubric_breakdown}
    q1 = breakdown["Q1 - Impl"]
    assert q1["score"] == 5.0, f"Score must be capped at 5.0, got {q1['score']}"
    assert q1.get("score_capped") is True, "score_capped must be True when capping occurred"


def test_no_score_capped_flag_when_not_capped():
    """When checkpoints don't exceed criterion max, score_capped must be False."""
    criteria = [{"criterion": "Q1 - Impl", "max": 10}]
    checkpoints = [
        CheckpointResult(
            checkpoint_id="cp1",
            criterion="Q1 - Impl",
            description="test",
            points_max=5,
            points_awarded=5.0,
            score_percent=100,
            verified=True,
            verification_method="exact",
        ),
    ]
    result = run_scorer(
        checkpoints=checkpoints,
        criteria=criteria,
        student_id=1,
        session_id=1,
        max_score=10,
    )
    breakdown = {item["criterion"]: item for item in result.rubric_breakdown}
    q1 = breakdown["Q1 - Impl"]
    assert q1.get("score_capped") is False, "score_capped must be False when no capping"
