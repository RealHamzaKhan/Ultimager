"""Tests for grading logic in app.services.ai_grader_fixed."""
import hashlib

import pytest

from app.services.ai_grader_fixed import (
    parse_rubric,
    compute_grading_hash,
    evaluate_relevance_gate,
    build_relevance_block_result,
)


# ── parse_rubric ────────────────────────────────────────────────────


class TestParseRubric:
    def test_valid_rubric_text(self, sample_rubric_text):
        """Standard multi-line rubric with 'Name (points): description' format."""
        criteria = parse_rubric(sample_rubric_text)
        assert len(criteria) == 3
        names = [c["criterion"] for c in criteria]
        # Criterion names contain the full parsed label
        assert any("Code Quality" in n for n in names)
        assert any("Documentation" in n for n in names)
        assert any("Testing" in n for n in names)
        # Check max values
        maxes = {c["max"] for c in criteria}
        assert 40.0 in maxes
        assert 30.0 in maxes

    def test_empty_rubric(self):
        assert parse_rubric("") == []
        assert parse_rubric("   ") == []
        assert parse_rubric(None) == []

    def test_single_criterion(self):
        result = parse_rubric("Correctness (100): All tests pass")
        assert len(result) == 1
        assert "Correctness" in result[0]["criterion"]
        assert result[0]["max"] == 100.0

    def test_lines_starting_with_total_are_skipped(self):
        text = "Design: 40\nTotal: 100"
        result = parse_rubric(text)
        names = [c["criterion"] for c in result]
        assert not any("total" in n.lower() for n in names)

    def test_lines_starting_with_rubric_are_skipped(self):
        text = "Rubric for Assignment 1\nAccuracy: 50\nStyle: 50"
        result = parse_rubric(text)
        names = [c["criterion"] for c in result]
        assert not any("rubric" in n.lower() for n in names)
        assert len(result) == 2

    def test_duplicate_criterion_names_deduplicated(self):
        text = "Design: 30\nDesign: 30\nImplementation: 40"
        result = parse_rubric(text)
        names = [c["criterion"] for c in result]
        assert names.count("Design") == 1

    def test_points_keyword_stripped(self):
        text = "Testing - 25 points"
        result = parse_rubric(text)
        assert len(result) == 1
        assert result[0]["max"] == 25.0


# ── compute_grading_hash ────────────────────────────────────────────


class TestComputeGradingHash:
    def test_returns_hex_string(self):
        files = [{"filename": "a.py", "content": "x = 1"}]
        h = compute_grading_hash(files, "Testing: 100", 100)
        assert isinstance(h, str)
        # SHA-256 hex digest truncated to 16 chars
        assert len(h) == 16
        # All hex characters
        assert all(c in "0123456789abcdef" for c in h)

    def test_consistent_across_calls(self):
        files = [{"filename": "main.py", "content": "print('hi')"}]
        h1 = compute_grading_hash(files, "Code: 50\nStyle: 50", 100)
        h2 = compute_grading_hash(files, "Code: 50\nStyle: 50", 100)
        assert h1 == h2

    def test_different_rubric_produces_different_hash(self):
        files = [{"filename": "a.py", "content": "pass"}]
        h1 = compute_grading_hash(files, "Testing: 100", 100)
        h2 = compute_grading_hash(files, "Design: 100", 100)
        assert h1 != h2

    def test_different_files_produce_different_hash(self):
        f1 = [{"filename": "a.py", "content": "x = 1"}]
        f2 = [{"filename": "b.py", "content": "y = 2"}]
        h1 = compute_grading_hash(f1, "Code: 100", 100)
        h2 = compute_grading_hash(f2, "Code: 100", 100)
        assert h1 != h2

    def test_different_max_score_produces_different_hash(self):
        files = [{"filename": "a.py", "content": "x = 1"}]
        h1 = compute_grading_hash(files, "Code: 50", 50)
        h2 = compute_grading_hash(files, "Code: 50", 100)
        assert h1 != h2


# ── evaluate_relevance_gate ─────────────────────────────────────────


class TestEvaluateRelevanceGate:
    def test_relevant_submission_passes(self):
        """A clearly relevant submission should not be blocked."""
        relevance = {
            "is_relevant": True,
            "confidence": "high",
            "flags": [],
            "assignment_signal": {
                "token_overlap": 15,
                "signal_ratio": 0.5,
                "has_relevant_sections": True,
                "files_with_overlap": 3,
                "files_scanned": 3,
                "matched_terms": ["algorithm", "sort", "implementation"],
            },
        }
        gate = evaluate_relevance_gate(relevance)
        assert gate["block_grading"] is False
        assert gate["is_relevant"] is True

    def test_empty_submission_blocked(self):
        """An empty submission should be blocked."""
        relevance = {
            "is_relevant": False,
            "confidence": "high",
            "flags": ["empty_submission"],
        }
        gate = evaluate_relevance_gate(relevance)
        assert gate["block_grading"] is True
        assert "empty" in gate["reason"].lower()

    def test_none_relevance_not_blocked(self):
        """If relevance is None, gate should not block (defaults to relevant)."""
        gate = evaluate_relevance_gate(None)
        assert gate["block_grading"] is False
        assert gate["is_relevant"] is True

    def test_wrong_assignment_high_confidence_blocked(self):
        """Wrong assignment with high confidence and no relevant sections should block."""
        relevance = {
            "is_relevant": False,
            "confidence": "high",
            "flags": ["wrong_assignment", "off_topic"],
            "assignment_signal": {
                "token_overlap": 0,
                "signal_ratio": 0.0,
                "has_relevant_sections": False,
                "files_with_overlap": 0,
                "files_scanned": 2,
            },
        }
        gate = evaluate_relevance_gate(relevance)
        assert gate["block_grading"] is True

    def test_mixed_content_not_blocked(self):
        """Mixed content (some relevant, some not) should NOT be blocked."""
        relevance = {
            "is_relevant": False,
            "confidence": "high",
            "flags": ["off_topic"],
            "mixed_content": True,
            "assignment_signal": {
                "token_overlap": 5,
                "signal_ratio": 0.2,
                "has_relevant_sections": True,
            },
        }
        gate = evaluate_relevance_gate(relevance)
        assert gate["block_grading"] is False
        assert gate["mixed_content"] is True

    def test_gate_output_structure(self):
        """Verify all expected keys are present in gate output."""
        gate = evaluate_relevance_gate({})
        expected_keys = {
            "block_grading", "review_required", "reason",
            "is_relevant", "confidence", "flags", "critical_flags",
            "has_relevant_sections", "mixed_content",
            "assignment_signal", "policy_version",
        }
        assert expected_keys.issubset(set(gate.keys()))

    def test_flags_normalized_and_sorted(self):
        relevance = {
            "flags": ["  OFF_TOPIC ", "empty_submission", "OFF_TOPIC"],
        }
        gate = evaluate_relevance_gate(relevance)
        # Deduplicated, lowered, sorted
        assert gate["flags"] == sorted(set(["off_topic", "empty_submission"]))


# ── build_relevance_block_result ────────────────────────────────────


class TestBuildRelevanceBlockResult:
    def test_returns_zero_scores(self, sample_rubric_text):
        relevance = {
            "is_relevant": False,
            "confidence": "high",
            "flags": ["wrong_assignment"],
            "reasoning": "Submission is about biology, not CS.",
        }
        result = build_relevance_block_result(sample_rubric_text, 100, relevance)

        assert result["total_score"] == 0.0
        assert result["letter_grade"] == "F"
        assert result["percentage"] == 0.0
        assert result["max_score"] == 100

    def test_rubric_breakdown_matches_criteria(self, sample_rubric_text):
        relevance = {"is_relevant": False, "flags": ["off_topic"], "confidence": "high"}
        result = build_relevance_block_result(sample_rubric_text, 100, relevance)

        breakdown = result["rubric_breakdown"]
        assert len(breakdown) == 3
        for item in breakdown:
            assert item["score"] == 0.0
            assert "criterion" in item
            assert "max" in item
            assert "justification" in item

    def test_overall_feedback_present(self, sample_rubric_text):
        relevance = {"is_relevant": False, "flags": ["empty_submission"]}
        result = build_relevance_block_result(sample_rubric_text, 100, relevance)

        assert "overall_feedback" in result
        assert len(result["overall_feedback"]) > 0
        assert "irrelevant" in result["overall_feedback"].lower() or "unrelated" in result["overall_feedback"].lower()

    def test_strengths_empty_weaknesses_populated(self, sample_rubric_text):
        relevance = {"is_relevant": False, "flags": ["wrong_assignment"]}
        result = build_relevance_block_result(sample_rubric_text, 100, relevance)

        assert result["strengths"] == []
        assert len(result["weaknesses"]) >= 1

    def test_confidence_defaults_to_medium(self, sample_rubric_text):
        relevance = {"is_relevant": False, "flags": ["off_topic"], "confidence": "invalid_value"}
        result = build_relevance_block_result(sample_rubric_text, 100, relevance)

        assert result["confidence"] in {"high", "medium"}

    def test_custom_gate_passed_through(self, sample_rubric_text):
        """When a pre-computed gate is passed, it should be used."""
        gate = evaluate_relevance_gate({
            "is_relevant": False,
            "confidence": "high",
            "flags": ["empty_submission"],
        })
        relevance = {"is_relevant": False}
        result = build_relevance_block_result(sample_rubric_text, 100, relevance, gate=gate)

        assert result["total_score"] == 0.0
        assert result["relevance_gate"] == gate
