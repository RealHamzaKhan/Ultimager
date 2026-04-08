"""Tests for B3 — judge truncation flag.

Verifies that when submission_content exceeds 28,000 chars, the returned
CheckpointResult has judge_truncated=True so the teacher can see it in the UI.
"""
import pytest
from unittest.mock import patch, MagicMock


def _ok_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    return resp


LONG_CONTENT = "x" * 30_000   # exceeds 28_000 char limit


@pytest.mark.asyncio
async def test_judge_truncation_flag_set_when_content_exceeds_limit():
    """judge_truncated must be True when submission_content > 28K chars."""
    from app.services.agents.domain_judge import judge_checkpoint

    judge_json = '{"score_percent": 75, "reasoning": "ok", "confidence": "high", "evidence_quote": "xxxx"}'
    with patch("app.services.agents.domain_judge.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _ok_response(judge_json)
        MockOpenAI.return_value = mock_client

        result = await judge_checkpoint(
            checkpoint_id="cp1",
            criterion="Test",
            points_max=5.0,
            pass_description="Correct",
            fail_description="Wrong",
            submission_content=LONG_CONTENT,
        )

    assert result.judge_truncated is True, "judge_truncated should be True when content > 28K"


@pytest.mark.asyncio
async def test_judge_truncation_flag_false_for_short_content():
    """judge_truncated must be False for content ≤ 28K chars."""
    from app.services.agents.domain_judge import judge_checkpoint

    judge_json = '{"score_percent": 75, "reasoning": "ok", "confidence": "high", "evidence_quote": "pass"}'
    with patch("app.services.agents.domain_judge.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _ok_response(judge_json)
        MockOpenAI.return_value = mock_client

        result = await judge_checkpoint(
            checkpoint_id="cp1",
            criterion="Test",
            points_max=5.0,
            pass_description="Correct",
            fail_description="Wrong",
            submission_content="short content",
        )

    assert result.judge_truncated is False


@pytest.mark.asyncio
async def test_judge_truncation_flag_false_for_exactly_28k():
    """At exactly 28K chars (boundary), judge_truncated must be False."""
    from app.services.agents.domain_judge import judge_checkpoint

    judge_json = '{"score_percent": 100, "reasoning": "full marks", "confidence": "high", "evidence_quote": "y"}'
    with patch("app.services.agents.domain_judge.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _ok_response(judge_json)
        MockOpenAI.return_value = mock_client

        result = await judge_checkpoint(
            checkpoint_id="cp1",
            criterion="Test",
            points_max=5.0,
            pass_description="Correct",
            fail_description="Wrong",
            submission_content="y" * 28_000,  # exactly at limit
        )

    assert result.judge_truncated is False
