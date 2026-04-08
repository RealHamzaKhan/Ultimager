"""Tests for score validation logic (OverridePayload) and letter grade mapping."""
import pytest
from pydantic import ValidationError

from app.schemas import OverridePayload


# ── Valid scores ────────────────────────────────────────────────────


class TestValidScores:
    """OverridePayload.score must be >= 0 (no upper bound in schema, but tested)."""

    def test_zero_score(self):
        p = OverridePayload(score=0)
        assert p.score == 0.0

    def test_fifty_score(self):
        p = OverridePayload(score=50)
        assert p.score == 50.0

    def test_hundred_score(self):
        p = OverridePayload(score=100)
        assert p.score == 100.0

    def test_fractional_score(self):
        p = OverridePayload(score=99.5)
        assert p.score == 99.5

    def test_very_high_score_accepted(self):
        """Schema only enforces ge=0, so high values are accepted."""
        p = OverridePayload(score=200)
        assert p.score == 200.0


# ── Invalid scores ──────────────────────────────────────────────────


class TestInvalidScores:
    def test_negative_score_rejected(self):
        with pytest.raises(ValidationError):
            OverridePayload(score=-1)

    def test_string_non_numeric_rejected(self):
        with pytest.raises(ValidationError):
            OverridePayload(score="hello")

    def test_none_rejected(self):
        with pytest.raises(ValidationError):
            OverridePayload(score=None)

    def test_missing_score_rejected(self):
        with pytest.raises(ValidationError):
            OverridePayload()


# ── String coercion ─────────────────────────────────────────────────


class TestStringCoercion:
    """Pydantic v2 coerces numeric strings to float for float fields."""

    def test_string_integer(self):
        p = OverridePayload(score="85")
        assert p.score == 85.0

    def test_string_float(self):
        p = OverridePayload(score="85.5")
        assert p.score == 85.5

    def test_string_fraction_rejected(self):
        """Strings like '85/100' are not valid floats."""
        with pytest.raises(ValidationError):
            OverridePayload(score="85/100")


# ── Optional fields ─────────────────────────────────────────────────


class TestOverridePayloadOptionalFields:
    def test_comments_optional(self):
        p = OverridePayload(score=75)
        assert p.comments is None

    def test_comments_provided(self):
        p = OverridePayload(score=75, comments="Good effort")
        assert p.comments == "Good effort"

    def test_is_reviewed_default_false(self):
        p = OverridePayload(score=75)
        assert p.is_reviewed is False

    def test_is_reviewed_set_true(self):
        p = OverridePayload(score=75, is_reviewed=True)
        assert p.is_reviewed is True


# ── Grade mapping (letter grade thresholds from ai_grader_fixed) ────


class TestLetterGradeThresholds:
    """
    Verify the letter grade thresholds embedded in _validate_and_fix_result.
    These thresholds are percentage-based:
        A+: >= 97, A: >= 93, A-: >= 90,
        B+: >= 87, B: >= 83, B-: >= 80,
        C+: >= 77, C: >= 73, C-: >= 70,
        D+: >= 67, D: >= 60,
        F: < 60
    We replicate the logic here to validate the mapping since it is inline.
    """

    @staticmethod
    def _letter_grade(percentage: float) -> str:
        """Mirror of the inline letter-grade logic in ai_grader_fixed."""
        if percentage >= 97:
            return "A+"
        elif percentage >= 93:
            return "A"
        elif percentage >= 90:
            return "A-"
        elif percentage >= 87:
            return "B+"
        elif percentage >= 83:
            return "B"
        elif percentage >= 80:
            return "B-"
        elif percentage >= 77:
            return "C+"
        elif percentage >= 73:
            return "C"
        elif percentage >= 70:
            return "C-"
        elif percentage >= 67:
            return "D+"
        elif percentage >= 60:
            return "D"
        else:
            return "F"

    @pytest.mark.parametrize("pct,expected", [
        (100, "A+"),
        (97, "A+"),
        (96.9, "A"),
        (93, "A"),
        (90, "A-"),
        (89.9, "B+"),
        (87, "B+"),
        (83, "B"),
        (80, "B-"),
        (77, "C+"),
        (73, "C"),
        (70, "C-"),
        (67, "D+"),
        (60, "D"),
        (59.9, "F"),
        (0, "F"),
    ])
    def test_threshold_boundary(self, pct, expected):
        assert self._letter_grade(pct) == expected

    def test_perfect_score(self):
        assert self._letter_grade(100.0) == "A+"

    def test_zero_is_f(self):
        assert self._letter_grade(0) == "F"

    def test_negative_is_f(self):
        assert self._letter_grade(-5) == "F"
