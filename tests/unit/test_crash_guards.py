"""Crash guard tests — B1 and B2 bugs.

B1: choices[0] crashes on empty API response (domain_judge + checkpoint_grader)
B2: Routing failure silently maps ALL files to ALL criteria (checkpoint_grader)
"""
import pytest
from unittest.mock import MagicMock, patch


def _empty_choices_response():
    resp = MagicMock()
    resp.choices = []          # ← the crash trigger
    return resp


# ─────────────────────────────────────────────────────────────────
# B1a: judge_checkpoint — empty choices must not raise IndexError
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_judge_checkpoint_empty_choices_returns_zero_score():
    from app.services.agents.domain_judge import judge_checkpoint

    with patch("app.services.agents.domain_judge.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _empty_choices_response()
        MockOpenAI.return_value = mock_client

        result = await judge_checkpoint(
            checkpoint_id="cp1",
            criterion="Test criterion",
            points_max=5.0,
            pass_description="Correct implementation",
            fail_description="Wrong or missing",
            submission_content="some content",
        )
    # Must NOT raise IndexError; must return 0 score
    assert result.points_awarded == 0
    assert result.score_percent == 0


# ─────────────────────────────────────────────────────────────────
# B1b: route_files_to_criteria — empty choices must not crash
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_route_files_to_criteria_empty_choices_skips_batch():
    from app.services.checkpoint_grader import route_files_to_criteria

    criteria = [{"criterion": "Q1 - Implementation", "max": 10}]
    mock_file = MagicMock()
    mock_file.filename = "solution.py"
    mock_file.text_content = "def foo(): pass"
    mock_file.images = []
    student_files = [mock_file]

    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _empty_choices_response()
        MockOpenAI.return_value = mock_client

        result = await route_files_to_criteria(
            criteria=criteria,
            student_files=student_files,
        )
    # Should not raise; returns something (dict or tuple after Task 2)
    assert result is not None


# ─────────────────────────────────────────────────────────────────
# B2: Routing fallback must NOT map all files to all criteria
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_route_files_batch_failure_skips_not_maps_all():
    from app.services.checkpoint_grader import route_files_to_criteria

    criteria = [
        {"criterion": "Q1 - Implementation", "max": 10},
        {"criterion": "Q2 - Testing", "max": 5},
    ]
    mock_file1 = MagicMock()
    mock_file1.filename = "q1.py"
    mock_file1.text_content = "def solve(): pass"
    mock_file1.images = []

    mock_file2 = MagicMock()
    mock_file2.filename = "q2.py"
    mock_file2.text_content = "def test(): pass"
    mock_file2.images = []

    student_files = [mock_file1, mock_file2]

    call_count = 0

    def fail_then_empty(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("API unavailable")
        return _empty_choices_response()

    with patch("openai.OpenAI") as MockOpenAI:
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fail_then_empty
        MockOpenAI.return_value = mock_client

        result = await route_files_to_criteria(criteria=criteria, student_files=student_files)

    # Unpack if tuple (after Task 2 changes)
    if isinstance(result, tuple):
        routing_map, fallback_flag = result
        assert fallback_flag is True, "fallback_flag must be True when a batch failed"
    else:
        routing_map = result

    # CRITICAL: result must NOT be all-to-all (each file mapped to every criterion)
    # With 2 criteria and 2 files, all-to-all = 4 total. Skip means ≤ sum from successful batches.
    total_mappings = sum(len(v) for v in routing_map.values())
    assert total_mappings <= 2, (
        f"All-to-all fallback occurred: {total_mappings} total file mappings across criteria. "
        f"Expected ≤2 (only from successful batches). Result: {routing_map}"
    )
