"""Unit tests for the multi-agent grading pipeline.

Tests cover:
1. Evidence Verifier (deterministic)
2. Scorer (deterministic)
3. Critic (deterministic flags)
4. Orchestrator result conversion
"""
from __future__ import annotations

import pytest
from dataclasses import field

from app.services.agents.base import CheckpointResult, GradingResult
from app.services.agents.verifier import verify_evidence, run_verifier
from app.services.agents.scorer import run_scorer
from app.services.agents.critic import run_critic
from app.services.agents.orchestrator import (
    _result_to_ai_result_dict,
    _percentage_to_letter,
    _generate_feedback,
)


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def make_checkpoint(
    checkpoint_id: str = "cp1",
    criterion: str = "BFS Implementation",
    points_max: float = 5.0,
    score_percent: int = 100,
    evidence_quote: str = "queue = deque([start])",
    verified: bool = True,
    confidence: str = "high",
    flags: list | None = None,
) -> CheckpointResult:
    cp = CheckpointResult(
        checkpoint_id=checkpoint_id,
        criterion=criterion,
        points_max=points_max,
        score_percent=score_percent,
        points_awarded=points_max * score_percent / 100,
        evidence_quote=evidence_quote,
        verified=verified,
        confidence=confidence,
    )
    if flags:
        cp.flags = flags
    return cp


# ═══════════════════════════════════════════════════════════════
# 1. VERIFIER TESTS
# ═══════════════════════════════════════════════════════════════

class TestVerifyEvidence:
    """Test the 4-tier evidence verification."""

    def test_exact_match(self):
        verified, method, sim = verify_evidence(
            "queue = deque([start])",
            "def bfs(graph, start):\n    queue = deque([start])\n    visited = set()"
        )
        assert verified is True
        assert method == "exact"
        assert sim == 1.0

    def test_case_insensitive_match(self):
        verified, method, sim = verify_evidence(
            "THE BFS ALGORITHM USES A QUEUE",
            "The BFS Algorithm uses a queue to traverse nodes level by level."
        )
        assert verified is True
        assert method == "case_insensitive"
        assert sim >= 0.90

    def test_char_stripped_match_for_code(self):
        """LLM may reformat indentation — stripped or case_insensitive match handles it."""
        content = "    for node in graph[current]:\n        if node not in visited:\n            queue.append(node)"
        quote = "for node in graph[current]:\n  if node not in visited:\n    queue.append(node)"
        verified, method, sim = verify_evidence(quote, content)
        assert verified is True
        # Should match via case_insensitive or char_stripped — either is fine
        assert method in ("case_insensitive", "char_stripped")

    def test_fuzzy_match_long_quote(self):
        content = "The Breadth First Search algorithm explores all neighbors at the current depth before moving deeper."
        quote = "Breadth First Search algorithm explores all neighbors at the current depth"
        verified, method, sim = verify_evidence(quote, content)
        assert verified is True
        assert sim >= 0.80

    def test_empty_quote_returns_false(self):
        verified, method, sim = verify_evidence("", "some content here")
        assert verified is False
        assert method == "empty"
        assert sim == 0.0

    def test_whitespace_only_quote_returns_false(self):
        verified, method, sim = verify_evidence("   \n\t  ", "some content")
        assert verified is False
        assert method == "empty"

    def test_visual_evidence_always_verified(self):
        verified, method, sim = verify_evidence("[VISUAL] Diagram shows BFS tree", "no matching text here")
        assert verified is True
        assert method == "visual"
        assert sim == 1.0

    def test_clearly_fabricated_returns_false(self):
        verified, method, sim = verify_evidence(
            "THIS TEXT ABSOLUTELY DOES NOT EXIST IN THE SUBMISSION XYZ123",
            "def hello(): print('world')"
        )
        assert verified is False

    def test_empty_submission(self):
        verified, method, sim = verify_evidence("some evidence", "")
        assert verified is False


class TestRunVerifier:
    """Test run_verifier with CheckpointResult."""

    def test_verified_evidence_no_flags(self):
        cp = make_checkpoint(
            evidence_quote="queue = deque([start])",
            score_percent=100,
            verified=False,  # will be updated by verifier
        )
        result = run_verifier(cp, "queue = deque([start])\nsome more code")
        assert result.verified is True
        assert len(result.flags) == 0

    def test_hallucinated_evidence_flagged(self):
        cp = make_checkpoint(
            evidence_quote="COMPLETELY FABRICATED TEXT XXXX",
            score_percent=75,
            verified=False,
        )
        result = run_verifier(cp, "def add(a, b): return a + b")
        assert result.verified is False
        assert "evidence_likely_hallucinated" in result.flags
        assert result.needs_review is True

    def test_empty_evidence_with_marks_flagged(self):
        cp = make_checkpoint(
            evidence_quote="",
            score_percent=50,
            verified=False,
        )
        result = run_verifier(cp, "def bfs(): pass")
        assert result.verified is False
        assert "no_evidence_for_awarded_marks" in result.flags
        assert result.needs_review is True

    def test_score_not_changed_by_verifier(self):
        """Verifier NEVER changes score — only flags."""
        cp = make_checkpoint(
            evidence_quote="FABRICATED TEXT DOES NOT EXIST XYZ",
            score_percent=75,
            verified=False,
        )
        cp.points_awarded = 3.75
        result = run_verifier(cp, "completely different content here")
        # Score unchanged
        assert result.score_percent == 75
        assert result.points_awarded == 3.75


# ═══════════════════════════════════════════════════════════════
# 2. SCORER TESTS
# ═══════════════════════════════════════════════════════════════

class TestRunScorer:
    """Test the deterministic scorer."""

    def test_full_credit_all_checkpoints(self):
        checkpoints = [
            make_checkpoint("cp1", "BFS", 5.0, 100),
            make_checkpoint("cp2", "Queue", 3.0, 100),
            make_checkpoint("cp3", "Visited", 2.0, 100),
        ]
        criteria = [
            {"criterion": "BFS", "max": 5},
            {"criterion": "Queue", "max": 3},
            {"criterion": "Visited", "max": 2},
        ]
        result = run_scorer(checkpoints, criteria, student_id=1, session_id=1, max_score=10.0)
        assert result.total_score == 10.0
        assert result.score_percent == 100.0
        assert result.checkpoint_stats["full_credit"] == 3
        assert result.checkpoint_stats["partial_credit"] == 0
        assert result.checkpoint_stats["no_credit"] == 0

    def test_partial_credit_scoring(self):
        checkpoints = [
            make_checkpoint("cp1", "BFS", 4.0, 75),   # 3.0 pts
            make_checkpoint("cp2", "Queue", 4.0, 50),  # 2.0 pts
            make_checkpoint("cp3", "Visited", 2.0, 0), # 0.0 pts
        ]
        criteria = [
            {"criterion": "BFS", "max": 4},
            {"criterion": "Queue", "max": 4},
            {"criterion": "Visited", "max": 2},
        ]
        result = run_scorer(checkpoints, criteria, student_id=1, session_id=1, max_score=10.0)
        assert result.total_score == 5.0
        assert result.score_percent == 50.0
        assert result.checkpoint_stats["full_credit"] == 0
        assert result.checkpoint_stats["partial_credit"] == 2
        assert result.checkpoint_stats["no_credit"] == 1

    def test_zero_score_empty_checkpoints(self):
        result = run_scorer([], [], student_id=1, session_id=1, max_score=10.0)
        assert result.total_score == 0.0
        assert result.score_percent == 0.0

    def test_score_clamped_to_max(self):
        """Score cannot exceed max_score."""
        checkpoints = [
            make_checkpoint("cp1", "Test", 12.0, 100),
        ]
        criteria = [{"criterion": "Test", "max": 12}]
        result = run_scorer(checkpoints, criteria, student_id=1, session_id=1, max_score=10.0)
        assert result.total_score <= 10.0

    def test_deterministic_same_input_same_output(self):
        """Same inputs must always produce identical outputs."""
        checkpoints = [
            make_checkpoint("cp1", "BFS", 5.0, 75),
            make_checkpoint("cp2", "Queue", 5.0, 50),
        ]
        criteria = [
            {"criterion": "BFS", "max": 5},
            {"criterion": "Queue", "max": 5},
        ]
        r1 = run_scorer(checkpoints, criteria, student_id=1, session_id=1, max_score=10.0)
        r2 = run_scorer(checkpoints, criteria, student_id=1, session_id=1, max_score=10.0)
        assert r1.total_score == r2.total_score
        assert r1.score_percent == r2.score_percent

    def test_verification_rate_computed(self):
        checkpoints = [
            make_checkpoint("cp1", "BFS", 5.0, 100, verified=True),
            make_checkpoint("cp2", "Queue", 5.0, 100, verified=False),
        ]
        criteria = [
            {"criterion": "BFS", "max": 5},
            {"criterion": "Queue", "max": 5},
        ]
        result = run_scorer(checkpoints, criteria, student_id=1, session_id=1, max_score=10.0)
        # 1 of 2 checkpoints verified (only the ones with score > 0 counted)
        assert "verification_rate" in result.checkpoint_stats


# ═══════════════════════════════════════════════════════════════
# 3. CRITIC TESTS
# ═══════════════════════════════════════════════════════════════

class TestRunCritic:
    """Test the deterministic critic agent."""

    def _make_clean_result(self) -> GradingResult:
        """Create a clean grading result with no issues."""
        checkpoints = [
            make_checkpoint("cp1", "BFS", 5.0, 100, verified=True, confidence="high"),
            make_checkpoint("cp2", "Queue", 5.0, 100, verified=True, confidence="high"),
        ]
        result = GradingResult(
            student_id=1,
            session_id=1,
            total_score=10.0,
            max_score=10.0,
            score_percent=100.0,
            checkpoints=checkpoints,
        )
        return result

    def test_clean_result_no_flags(self):
        result = self._make_clean_result()
        result = run_critic(result, "def bfs(graph, start): queue = deque([start])")
        assert len(result.review_flags) == 0
        assert result.needs_review is False
        assert result.confidence == "high"

    def test_low_confidence_checkpoint_flagged(self):
        checkpoints = [
            make_checkpoint("cp1", "BFS", 5.0, 100, verified=True, confidence="low"),
        ]
        result = GradingResult(
            student_id=1, session_id=1,
            total_score=5.0, max_score=5.0, score_percent=100.0,
            checkpoints=checkpoints,
        )
        result = run_critic(result, "some submission text that is long enough to matter here")
        assert any("Low confidence" in f for f in result.review_flags)
        assert result.needs_review is True

    def test_hallucinated_evidence_flagged(self):
        cp = make_checkpoint("cp1", "BFS", 5.0, 75, verified=False, confidence="medium")
        cp.flags = ["evidence_likely_hallucinated"]
        cp.points_awarded = 3.75
        result = GradingResult(
            student_id=1, session_id=1,
            total_score=3.75, max_score=5.0, score_percent=75.0,
            checkpoints=[cp],
        )
        result = run_critic(result, "some submission text")
        assert any("hallucination" in f.lower() or "evidence" in f.lower() for f in result.review_flags)
        assert result.needs_review is True

    def test_zero_score_with_content_flagged(self):
        checkpoints = [
            make_checkpoint("cp1", "BFS", 5.0, 0, verified=True, confidence="high"),
        ]
        result = GradingResult(
            student_id=1, session_id=1,
            total_score=0.0, max_score=5.0, score_percent=0.0,
            checkpoints=checkpoints,
        )
        long_submission = "def bfs(graph, start):\n    queue = deque([start])\n    " * 10
        result = run_critic(result, long_submission)
        assert any("zero" in f.lower() or "zero marks" in f.lower() for f in result.review_flags)

    def test_many_review_flags_reduce_confidence(self):
        """3+ flags should give low overall confidence."""
        cp1 = make_checkpoint("cp1", "A", 2.0, 75, verified=False, confidence="low")
        cp1.flags = ["evidence_likely_hallucinated"]
        cp1.points_awarded = 1.5
        cp1.needs_review = True

        cp2 = make_checkpoint("cp2", "B", 2.0, 50, verified=False, confidence="low")
        cp2.flags = ["evidence_likely_hallucinated"]
        cp2.points_awarded = 1.0
        cp2.needs_review = True

        cp3 = make_checkpoint("cp3", "C", 2.0, 25, verified=False, confidence="low")
        cp3.flags = ["evidence_likely_hallucinated"]
        cp3.points_awarded = 0.5
        cp3.needs_review = True

        result = GradingResult(
            student_id=1, session_id=1,
            total_score=3.0, max_score=6.0, score_percent=50.0,
            checkpoints=[cp1, cp2, cp3],
        )
        result = run_critic(result, "some content here that is long")
        assert result.confidence == "low"

    def test_critic_never_changes_scores(self):
        """Critic only flags — NEVER changes scores."""
        cp = make_checkpoint("cp1", "BFS", 5.0, 75, verified=False, confidence="low")
        cp.flags = ["evidence_likely_hallucinated"]
        cp.points_awarded = 3.75
        result = GradingResult(
            student_id=1, session_id=1,
            total_score=3.75, max_score=5.0, score_percent=75.0,
            checkpoints=[cp],
        )
        original_score = result.total_score
        result = run_critic(result, "some submission text here")
        assert result.total_score == original_score


# ═══════════════════════════════════════════════════════════════
# 4. ORCHESTRATOR HELPERS
# ═══════════════════════════════════════════════════════════════

class TestPercentageToLetter:
    def test_a_plus(self):
        assert _percentage_to_letter(100) == "A+"
        assert _percentage_to_letter(97) == "A+"

    def test_a(self):
        assert _percentage_to_letter(95) == "A"
        assert _percentage_to_letter(93) == "A"

    def test_b(self):
        assert _percentage_to_letter(85) == "B"

    def test_c(self):
        assert _percentage_to_letter(75) == "C"   # 73-76 = C

    def test_f(self):
        assert _percentage_to_letter(55) == "F"
        assert _percentage_to_letter(0) == "F"


class TestResultToAiResultDict:
    """Test orchestrator dict conversion."""

    def test_basic_conversion(self):
        checkpoints = [make_checkpoint("cp1", "BFS", 5.0, 100, verified=True)]
        result = GradingResult(
            student_id=1, session_id=1,
            total_score=5.0, max_score=5.0, score_percent=100.0,
            checkpoints=checkpoints,
            overall_feedback="Great work!",
            strengths=["Good BFS implementation"],
            weaknesses=[],
        )
        d = _result_to_ai_result_dict(result)
        assert d["total_score"] == 5.0
        assert d["max_score"] == 5.0
        assert d["percentage"] == 100.0
        assert d["letter_grade"] == "A+"
        assert d["overall_feedback"] == "Great work!"
        assert d["grading_method"] == "multi_agent"

    def test_flags_aggregated_from_checkpoints(self):
        cp = make_checkpoint("cp1", "BFS", 5.0, 75, verified=False)
        cp.flags = ["evidence_likely_hallucinated"]
        result = GradingResult(
            student_id=1, session_id=1,
            total_score=3.75, max_score=5.0, score_percent=75.0,
            checkpoints=[cp],
            review_flags=["Possible evidence hallucination in: BFS"],
        )
        d = _result_to_ai_result_dict(result)
        assert len(d["flags"]) > 0
        assert any("BFS" in f for f in d["flags"])

    def test_auto_feedback_generated_when_empty(self):
        result = GradingResult(
            student_id=1, session_id=1,
            total_score=9.0, max_score=10.0, score_percent=90.0,
            checkpoints=[],
            overall_feedback="",
        )
        d = _result_to_ai_result_dict(result)
        assert len(d["overall_feedback"]) > 0
        assert "Excellent" in d["overall_feedback"] or "90" in d["overall_feedback"]
