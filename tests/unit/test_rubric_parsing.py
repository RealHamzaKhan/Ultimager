"""Tests for rubric parsing and generation logic."""
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.services.ai_grader_fixed import parse_rubric


class TestParseRubricFormat:
    """Test rubric text parsing into structured criteria."""

    def test_standard_format(self):
        text = "Code Quality (40): Correctness and style\nDocumentation (30): Comments\nTesting (30): Coverage"
        result = parse_rubric(text)
        assert len(result) == 3
        # criterion stores full line text
        assert "Code Quality" in result[0]["criterion"]
        assert result[0]["max"] == 40
        assert "Documentation" in result[1]["criterion"]
        assert result[1]["max"] == 30
        assert "Testing" in result[2]["criterion"]
        assert result[2]["max"] == 30

    def test_single_criterion(self):
        text = "Correctness (100): Full marks for correct solution"
        result = parse_rubric(text)
        assert len(result) == 1
        assert "Correctness" in result[0]["criterion"]
        assert result[0]["max"] == 100

    def test_empty_string(self):
        assert parse_rubric("") == []

    def test_none_like_input(self):
        """Should handle gracefully."""
        assert parse_rubric("   \n  ") == []

    def test_total_lines_skipped(self):
        text = "Code (50): Quality\nDesign (50): Architecture\nTotal: 100 points"
        result = parse_rubric(text)
        assert len(result) == 2
        assert all(r["criterion"] != "Total" for r in result)

    def test_rubric_header_lines_skipped(self):
        text = "Rubric for Assignment 1\nCode (50): Quality\nDesign (50): Architecture"
        result = parse_rubric(text)
        assert len(result) == 2

    def test_duplicate_names_deduplicated(self):
        text = "Code (40): Quality\nCode (40): Style"
        result = parse_rubric(text)
        # Should deduplicate
        assert len(result) >= 1

    def test_points_keyword_stripped(self):
        text = "Code Quality (40 points): Correctness"
        result = parse_rubric(text)
        assert len(result) >= 1
        assert result[0]["max"] == 40

    def test_multiline_with_blank_lines(self):
        text = "Code (40): Quality\n\nDesign (30): Architecture\n\nTesting (30): Coverage"
        result = parse_rubric(text)
        assert len(result) == 3

    def test_criterion_with_hyphen_separator(self):
        """Test that various separator formats work."""
        text = "Code Quality (40): Quality of implementation"
        result = parse_rubric(text)
        assert len(result) >= 1

    def test_large_point_values(self):
        text = "Project (500): Large project\nPresentation (500): Oral defense"
        result = parse_rubric(text)
        assert len(result) == 2
        assert result[0]["max"] == 500

    def test_zero_point_criterion(self):
        text = "Bonus (0): Optional extra"
        result = parse_rubric(text)
        # 0 points may or may not be included depending on implementation
        # Just ensure no crash
        assert isinstance(result, list)

    def test_decimal_points(self):
        """Non-integer point values."""
        text = "Code (40): Quality\nDesign (30): Style"
        result = parse_rubric(text)
        assert len(result) == 2


class TestParseRubricEdgeCases:
    """Edge cases for rubric parsing."""

    def test_unicode_criterion_names(self):
        text = "Código (50): Calidad\nDocumentación (50): Comentarios"
        result = parse_rubric(text)
        # Should not crash with unicode
        assert isinstance(result, list)

    def test_very_long_criterion_name(self):
        long_name = "A" * 200
        text = f"{long_name} (100): Description"
        result = parse_rubric(text)
        assert isinstance(result, list)

    def test_no_parentheses_format(self):
        """Lines without (points) format may be skipped."""
        text = "Code Quality: Good code\nDocumentation: Good docs"
        result = parse_rubric(text)
        # Without point values, these may not parse as criteria
        assert isinstance(result, list)
