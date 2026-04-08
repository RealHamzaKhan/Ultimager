"""Tests for LLM client: _extract_json, _validate_result, and related functions."""
import json
import hashlib
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.ai_grader_fixed import (
    _extract_json,
    _validate_result,
    compute_grading_hash,
    parse_rubric,
    RateLimiter,
    ProviderFailoverError,
    ProviderSpec,
    _ResponseShim,
    _ChoiceShim,
    _MessageShim,
)


# ── _extract_json tests ─────────────────────────────────────────

class TestExtractJson:
    """Test the robust JSON extractor."""

    def test_valid_json_direct(self):
        raw = '{"total_score": 85, "rubric_breakdown": []}'
        result = _extract_json(raw)
        assert result["total_score"] == 85

    def test_json_in_markdown_fences(self):
        raw = '```json\n{"total_score": 85}\n```'
        result = _extract_json(raw)
        assert result["total_score"] == 85

    def test_json_in_plain_fences(self):
        raw = '```\n{"total_score": 85}\n```'
        result = _extract_json(raw)
        assert result["total_score"] == 85

    def test_json_with_surrounding_text(self):
        raw = 'Here is the result:\n{"total_score": 85}\nDone.'
        result = _extract_json(raw)
        assert result["total_score"] == 85

    def test_completely_invalid_json_raises(self):
        raw = "This is not JSON at all, no braces anywhere"
        with pytest.raises(json.JSONDecodeError):
            _extract_json(raw)

    def test_empty_string_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("")

    def test_nested_json_object(self):
        raw = '{"rubric_breakdown": [{"criterion": "A", "score": 10}], "total_score": 10}'
        result = _extract_json(raw)
        assert len(result["rubric_breakdown"]) == 1

    def test_json_with_whitespace(self):
        raw = '   \n  {"total_score": 42}  \n  '
        result = _extract_json(raw)
        assert result["total_score"] == 42

    def test_truncated_json_raises(self):
        """Truncated JSON that can't even match braces should raise."""
        raw = '{"total_score": 85, "rubric_breakdown": ['
        with pytest.raises(json.JSONDecodeError):
            _extract_json(raw)

    def test_json_with_trailing_comma_repaired(self):
        """Trailing commas are auto-repaired by the robust parser."""
        raw = '{"total_score": 85,}'
        result = _extract_json(raw)
        assert result == {"total_score": 85}


# ── _validate_result tests ───────────────────────────────────────

class TestValidateResult:
    """Test result validation and normalization."""

    @pytest.fixture
    def rubric_criteria(self):
        return [
            {"criterion": "Code Quality", "max": 40},
            {"criterion": "Documentation", "max": 30},
            {"criterion": "Testing", "max": 30},
        ]

    def test_valid_result_passes_through(self, rubric_criteria):
        result = {
            "rubric_breakdown": [
                {"criterion": "Code Quality", "score": 35, "max": 40, "justification": "Good"},
                {"criterion": "Documentation", "score": 25, "max": 30, "justification": "OK"},
                {"criterion": "Testing", "score": 20, "max": 30, "justification": "Fair"},
            ],
            "total_score": 80,
            "overall_feedback": "Good work",
            "strengths": ["Clean code"],
            "weaknesses": ["Missing tests"],
            "confidence": "high",
            "confidence_reasoning": "Clear submission",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        assert validated["total_score"] == 80
        assert validated["letter_grade"] == "B-"
        assert len(validated["rubric_breakdown"]) == 3

    def test_score_clamped_to_max(self, rubric_criteria):
        """Scores exceeding criterion max are clamped."""
        result = {
            "rubric_breakdown": [
                {"criterion": "Code Quality", "score": 50, "max": 40, "justification": "Over"},
                {"criterion": "Documentation", "score": 30, "max": 30, "justification": "OK"},
                {"criterion": "Testing", "score": 30, "max": 30, "justification": "OK"},
            ],
            "total_score": 110,
            "overall_feedback": "",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        # Code Quality score should be clamped to 40
        cq = next(b for b in validated["rubric_breakdown"] if b["criterion"] == "Code Quality")
        assert cq["score"] == 40

    def test_negative_score_clamped_to_zero(self, rubric_criteria):
        result = {
            "rubric_breakdown": [
                {"criterion": "Code Quality", "score": -5, "max": 40, "justification": "Bad"},
                {"criterion": "Documentation", "score": 0, "max": 30, "justification": "None"},
                {"criterion": "Testing", "score": 0, "max": 30, "justification": "None"},
            ],
            "total_score": -5,
            "overall_feedback": "",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        cq = next(b for b in validated["rubric_breakdown"] if b["criterion"] == "Code Quality")
        assert cq["score"] == 0

    def test_fuzzy_criterion_matching(self, rubric_criteria):
        """AI returns slightly different criterion names."""
        result = {
            "rubric_breakdown": [
                {"criterion": "code quality", "score": 30, "max": 40, "justification": "Good"},
                {"criterion": "documentation", "score": 20, "max": 30, "justification": "OK"},
                {"criterion": "testing", "score": 15, "max": 30, "justification": "Fair"},
            ],
            "total_score": 65,
            "overall_feedback": "Average",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        assert len(validated["rubric_breakdown"]) == 3
        assert validated["total_score"] == 65

    def test_missing_criteria_get_zero_score(self, rubric_criteria):
        """Criteria not returned by AI are scored 0."""
        result = {
            "rubric_breakdown": [
                {"criterion": "Code Quality", "score": 35, "max": 40, "justification": "Good"},
            ],
            "total_score": 35,
            "overall_feedback": "",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        assert len(validated["rubric_breakdown"]) == 3
        # Missing criteria should have score 0
        doc = next(b for b in validated["rubric_breakdown"] if b["criterion"] == "Documentation")
        assert doc["score"] == 0
        testing = next(b for b in validated["rubric_breakdown"] if b["criterion"] == "Testing")
        assert testing["score"] == 0

    def test_empty_rubric_breakdown(self, rubric_criteria):
        result = {
            "rubric_breakdown": [],
            "total_score": 0,
            "overall_feedback": "",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        assert len(validated["rubric_breakdown"]) == 3
        assert all(b["score"] == 0 for b in validated["rubric_breakdown"])
        assert validated["total_score"] == 0

    def test_invalid_confidence_defaults_to_medium(self, rubric_criteria):
        result = {
            "rubric_breakdown": [],
            "total_score": 0,
            "overall_feedback": "",
            "confidence": "super_high",  # Invalid
        }
        validated = _validate_result(result, rubric_criteria, 100)
        assert validated["confidence"] == "medium"

    def test_valid_confidence_values(self, rubric_criteria):
        for conf in ["high", "medium", "low"]:
            result = {
                "rubric_breakdown": [],
                "total_score": 0,
                "overall_feedback": "",
                "confidence": conf,
            }
            validated = _validate_result(result, rubric_criteria, 100)
            assert validated["confidence"] == conf

    def test_letter_grade_A_plus(self, rubric_criteria):
        result = {
            "rubric_breakdown": [
                {"criterion": "Code Quality", "score": 40, "max": 40, "justification": "Perfect"},
                {"criterion": "Documentation", "score": 30, "max": 30, "justification": "Perfect"},
                {"criterion": "Testing", "score": 30, "max": 30, "justification": "Perfect"},
            ],
            "total_score": 100,
            "overall_feedback": "",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        assert validated["letter_grade"] == "A+"

    def test_letter_grade_F(self, rubric_criteria):
        result = {
            "rubric_breakdown": [
                {"criterion": "Code Quality", "score": 10, "max": 40, "justification": "Poor"},
                {"criterion": "Documentation", "score": 5, "max": 30, "justification": "Poor"},
                {"criterion": "Testing", "score": 5, "max": 30, "justification": "Poor"},
            ],
            "total_score": 20,
            "overall_feedback": "",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        assert validated["letter_grade"] == "F"

    def test_score_string_coercion(self, rubric_criteria):
        """Score returned as string should be coerced."""
        result = {
            "rubric_breakdown": [
                {"criterion": "Code Quality", "score": "35", "max": 40, "justification": "Good"},
                {"criterion": "Documentation", "score": "25", "max": 30, "justification": "OK"},
                {"criterion": "Testing", "score": "20", "max": 30, "justification": "Fair"},
            ],
            "total_score": 80,
            "overall_feedback": "",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        assert validated["total_score"] == 80

    def test_non_list_strengths_becomes_empty_list(self, rubric_criteria):
        result = {
            "rubric_breakdown": [],
            "total_score": 0,
            "overall_feedback": "",
            "strengths": "not a list",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        assert validated["strengths"] == []

    def test_max_score_zero_no_crash(self):
        """max_score=0 should not cause division by zero."""
        rubric_criteria = [{"criterion": "Test", "max": 0}]
        result = {
            "rubric_breakdown": [{"criterion": "Test", "score": 0, "max": 0, "justification": ""}],
            "total_score": 0,
            "overall_feedback": "",
        }
        validated = _validate_result(result, rubric_criteria, 0)
        assert validated["percentage"] == 0

    def test_total_score_recalculated_from_breakdown(self, rubric_criteria):
        """Total score should be sum of breakdown scores, not AI's total."""
        result = {
            "rubric_breakdown": [
                {"criterion": "Code Quality", "score": 40, "max": 40, "justification": ""},
                {"criterion": "Documentation", "score": 30, "max": 30, "justification": ""},
                {"criterion": "Testing", "score": 30, "max": 30, "justification": ""},
            ],
            "total_score": 50,  # AI returned wrong total
            "overall_feedback": "",
        }
        validated = _validate_result(result, rubric_criteria, 100)
        assert validated["total_score"] == 100  # Recalculated from breakdown


# ── ResponseShim tests ───────────────────────────────────────────

class TestResponseShim:
    def test_shim_has_choices(self):
        shim = _ResponseShim("test content")
        assert len(shim.choices) == 1
        assert shim.choices[0].message.content == "test content"

    def test_shim_with_usage(self):
        shim = _ResponseShim("content", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
        assert shim.usage.prompt_tokens == 100
        assert shim.usage.completion_tokens == 50
        assert shim.usage.total_tokens == 150

    def test_shim_without_usage(self):
        shim = _ResponseShim("content")
        assert shim.usage is None


# ── ProviderFailoverError tests ──────────────────────────────────

class TestProviderFailoverError:
    def test_error_message_includes_purpose(self):
        err = ProviderFailoverError("grading", [
            {"provider": "nvidia", "model": "test", "error_type": "timeout", "error": "Timed out"}
        ])
        assert "grading" in str(err)
        assert "timeout" in str(err)

    def test_error_stores_attempts(self):
        attempts = [
            {"provider": "nvidia", "model": "test", "error_type": "rate_limited", "error": "429"}
        ]
        err = ProviderFailoverError("grading", attempts)
        assert err.attempts == attempts
        assert err.purpose == "grading"


# ── RateLimiter tests ────────────────────────────────────────────

class TestRateLimiter:
    def test_init_defaults(self):
        rl = RateLimiter(max_requests=10, per_seconds=60)
        assert rl.max_requests == 10
        assert rl.per_seconds == 60
        assert rl.timestamps == []

    @pytest.mark.asyncio
    async def test_acquire_under_limit(self):
        rl = RateLimiter(max_requests=5, per_seconds=60)
        await rl.acquire()
        assert len(rl.timestamps) == 1

    @pytest.mark.asyncio
    async def test_acquire_multiple(self):
        rl = RateLimiter(max_requests=5, per_seconds=60)
        for _ in range(3):
            await rl.acquire()
        assert len(rl.timestamps) == 3


# ── compute_grading_hash tests ───────────────────────────────────

class TestComputeGradingHash:
    def test_returns_hex_string(self):
        files = [{"filename": "test.py", "type": "code", "content": "print('hello')"}]
        h = compute_grading_hash(files, "Code (100): quality", 100)
        assert isinstance(h, str)
        assert len(h) >= 16  # Hex string (may be truncated)

    def test_consistent_across_calls(self):
        files = [{"filename": "test.py", "type": "code", "content": "print('hello')"}]
        h1 = compute_grading_hash(files, "Code (100): quality", 100)
        h2 = compute_grading_hash(files, "Code (100): quality", 100)
        assert h1 == h2

    def test_different_content_different_hash(self):
        files1 = [{"filename": "test.py", "type": "code", "content": "print('hello')"}]
        files2 = [{"filename": "test.py", "type": "code", "content": "print('world')"}]
        h1 = compute_grading_hash(files1, "Code (100): quality", 100)
        h2 = compute_grading_hash(files2, "Code (100): quality", 100)
        assert h1 != h2

    def test_different_rubric_different_hash(self):
        files = [{"filename": "test.py", "type": "code", "content": "print('hello')"}]
        h1 = compute_grading_hash(files, "Code (100): quality", 100)
        h2 = compute_grading_hash(files, "Design (100): design", 100)
        assert h1 != h2

    def test_different_max_score_different_hash(self):
        files = [{"filename": "test.py", "type": "code", "content": "print('hello')"}]
        h1 = compute_grading_hash(files, "Code (100): quality", 100)
        h2 = compute_grading_hash(files, "Code (100): quality", 50)
        assert h1 != h2

    def test_empty_files_list(self):
        h = compute_grading_hash([], "Code (100): quality", 100)
        assert isinstance(h, str)
        assert len(h) >= 16

    def test_files_with_images(self):
        files = [{"filename": "test.py", "type": "code", "content": "code",
                  "images": [{"data": "base64data", "page": 1}]}]
        h = compute_grading_hash(files, "Code (100): quality", 100)
        assert isinstance(h, str)


# ── parse_rubric tests (additional, complementing test_grading_logic.py) ──

class TestParseRubricExtended:
    def test_rubric_with_various_formats(self):
        text = "Code Quality (40): Correctness\nDocumentation (30): Comments\nTesting (30): Coverage"
        result = parse_rubric(text)
        assert len(result) == 3
        # criterion stores full line text including points
        assert "Code Quality" in result[0]["criterion"]
        assert result[0]["max"] == 40

    def test_rubric_with_spaces_in_points(self):
        text = "Code Quality ( 40 ): Correctness"
        result = parse_rubric(text)
        assert len(result) >= 1

    def test_empty_rubric_returns_empty(self):
        result = parse_rubric("")
        assert result == []

    def test_rubric_with_only_whitespace(self):
        result = parse_rubric("   \n  \n  ")
        assert result == []
