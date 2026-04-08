"""Tests for helper functions in app.main."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.main import _json_filter, _safe_json_load, _compute_grade_distribution, _serialize_submission


# ── _json_filter ────────────────────────────────────────────────────


class TestJsonFilter:
    def test_valid_json_string(self):
        result = _json_filter('{"key": "value"}')
        assert result == {"key": "value"}

    def test_list_json_string(self):
        result = _json_filter('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_none_returns_none(self):
        assert _json_filter(None) is None

    def test_empty_string_returns_none(self):
        assert _json_filter("") is None

    def test_invalid_json_returns_none(self):
        assert _json_filter("{broken json") is None

    def test_dict_passed_through(self):
        """Non-string values are returned as-is."""
        d = {"already": "parsed"}
        assert _json_filter(d) == d

    def test_integer_passed_through(self):
        assert _json_filter(42) == 42


# ── _safe_json_load ─────────────────────────────────────────────────


class TestSafeJsonLoad:
    def test_dict_returned_directly(self):
        d = {"a": 1}
        assert _safe_json_load(d) == {"a": 1}

    def test_json_string_parsed(self):
        result = _safe_json_load('{"x": 10}')
        assert result == {"x": 10}

    def test_none_returns_empty_dict(self):
        assert _safe_json_load(None) == {}

    def test_empty_string_returns_empty_dict(self):
        assert _safe_json_load("") == {}

    def test_invalid_json_returns_empty_dict(self):
        assert _safe_json_load("{not valid") == {}

    def test_json_array_returns_empty_dict(self):
        """If JSON parses to a list (not dict), return empty dict."""
        assert _safe_json_load("[1, 2, 3]") == {}

    def test_false_value_returns_empty_dict(self):
        assert _safe_json_load(0) == {}
        assert _safe_json_load(False) == {}


# ── _compute_grade_distribution ─────────────────────────────────────


class TestComputeGradeDistribution:
    def test_basic_distribution(self):
        subs = [
            MagicMock(ai_letter_grade="A"),
            MagicMock(ai_letter_grade="A"),
            MagicMock(ai_letter_grade="B"),
            MagicMock(ai_letter_grade="F"),
        ]
        dist = _compute_grade_distribution(subs)
        assert dist == {"A": 2, "B": 1, "F": 1}

    def test_empty_list(self):
        assert _compute_grade_distribution([]) == {}

    def test_none_grades_skipped(self):
        subs = [
            MagicMock(ai_letter_grade="A"),
            MagicMock(ai_letter_grade=None),
            MagicMock(ai_letter_grade=""),
        ]
        dist = _compute_grade_distribution(subs)
        assert dist == {"A": 1}

    def test_with_dict_objects(self):
        """The function also handles dict-like submissions via .get()."""
        subs = [
            {"ai_letter_grade": "C+"},
            {"ai_letter_grade": "C+"},
            {"ai_letter_grade": "B-"},
        ]
        dist = _compute_grade_distribution(subs)
        assert dist == {"C+": 2, "B-": 1}

    def test_all_same_grade(self):
        subs = [MagicMock(ai_letter_grade="A+") for _ in range(5)]
        dist = _compute_grade_distribution(subs)
        assert dist == {"A+": 5}


# ── _serialize_submission ───────────────────────────────────────────


class TestSerializeSubmission:
    def _make_submission(self, **overrides):
        """Build a mock StudentSubmission with all required attributes."""
        now = datetime.now(timezone.utc)
        defaults = {
            "id": 1,
            "session_id": 10,
            "student_identifier": "alice",
            "status": "graded",
            "file_count": 2,
            "ai_score": 85.0,
            "ai_letter_grade": "B",
            "ai_confidence": "high",
            "is_overridden": False,
            "override_score": None,
            "override_comments": None,
            "is_reviewed": False,
            "tests_passed": 5,
            "tests_total": 10,
            "graded_at": now,
            "error_message": None,
            "created_at": now,
            "files": json.dumps([{"filename": "main.py", "type": "code"}]),
            "ai_result": json.dumps({
                "rubric_breakdown": [
                    {"criterion": "Code", "score": 45, "max": 50, "justification": "Good"},
                    {"criterion": "Docs", "score": 40, "max": 50, "justification": "OK"},
                ],
                "total_score": 85,
                "overall_feedback": "Well done.",
                "strengths": ["Clean code"],
                "weaknesses": ["Missing tests"],
                "suggestions_for_improvement": "Add tests.",
                "confidence": "high",
                "confidence_reasoning": "Clear submission.",
                "percentage": 85.0,
                "grading_hash": "deadbeef",
            }),
            "ingestion_report": None,
            "relevance_flags": None,
            "is_relevant": True,
            "is_flagged": False,
            "flag_reason": None,
            "flagged_by": None,
            "flagged_at": None,
        }
        defaults.update(overrides)
        sub = MagicMock()
        for k, v in defaults.items():
            setattr(sub, k, v)
        return sub

    def test_full_serialization(self):
        sub = self._make_submission()
        result = _serialize_submission(sub)

        assert result["id"] == 1
        assert result["student_identifier"] == "alice"
        assert result["status"] == "graded"
        assert result["ai_score"] == 85.0
        assert result["ai_letter_grade"] == "B"
        assert result["final_score"] == 85.0
        assert result["is_overridden"] is False
        assert isinstance(result["files"], list)
        assert len(result["files"]) == 1
        assert result["ai_result"] is not None
        assert result["ai_feedback"] == "Well done."
        assert len(result["rubric_breakdown"]) == 2
        assert result["strengths"] == ["Clean code"]
        assert result["weaknesses"] == ["Missing tests"]

    def test_overridden_uses_override_score(self):
        sub = self._make_submission(
            is_overridden=True,
            override_score=92.0,
            override_comments="Adjusted up.",
        )
        result = _serialize_submission(sub)
        assert result["final_score"] == 92.0
        assert result["override_score"] == 92.0
        assert result["override_comments"] == "Adjusted up."

    def test_none_ai_result(self):
        sub = self._make_submission(ai_result=None, ai_score=None, status="pending")
        result = _serialize_submission(sub)

        assert result["ai_result"] is None
        assert result["ai_feedback"] == ""
        assert result["rubric_breakdown"] == []
        assert result["strengths"] == []
        assert result["final_score"] is None

    def test_malformed_ai_result_json(self):
        sub = self._make_submission(ai_result="{broken json")
        result = _serialize_submission(sub)

        assert result["ai_result"] is None
        assert result["ai_feedback"] == ""

    def test_malformed_files_json(self):
        sub = self._make_submission(files="{not a list}")
        result = _serialize_submission(sub)

        # Falls back to empty list
        assert result["files"] == []

    def test_graded_at_iso_format(self):
        dt = datetime(2026, 3, 14, 12, 0, 0, tzinfo=timezone.utc)
        sub = self._make_submission(graded_at=dt)
        result = _serialize_submission(sub)
        assert result["graded_at"] == "2026-03-14T12:00:00+00:00"

    def test_graded_at_none(self):
        sub = self._make_submission(graded_at=None)
        result = _serialize_submission(sub)
        assert result["graded_at"] is None

    def test_ingestion_report_parsed(self):
        report = {"student_id": "alice", "files_received": []}
        sub = self._make_submission(ingestion_report=json.dumps(report))
        result = _serialize_submission(sub)
        assert result["ingestion_report"] == report

    def test_relevance_flags_parsed(self):
        flags = {"flags": ["off_topic"], "confidence": "high"}
        sub = self._make_submission(relevance_flags=json.dumps(flags))
        result = _serialize_submission(sub)
        assert result["relevance_flags"] == flags

    def test_flag_fields(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        sub = self._make_submission(
            is_flagged=True,
            flag_reason="Possible plagiarism",
            flagged_by="instructor",
            flagged_at=dt,
        )
        result = _serialize_submission(sub)
        assert result["is_flagged"] is True
        assert result["flag_reason"] == "Possible plagiarism"
        assert result["flagged_by"] == "instructor"
        assert result["flagged_at"] == "2026-01-01T00:00:00+00:00"
