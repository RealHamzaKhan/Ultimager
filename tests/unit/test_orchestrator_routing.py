"""Unit tests for orchestrator file-routing cross-contamination fix.

Critical regression tests that verify:
  - When file_routing_map returns [] for Q1 criteria, all Q1 checkpoints score 0
    without calling the LLM judge (no cross-file contamination).
  - When file_routing_map maps a criterion to specific files, only those files
    are passed to the judge.
  - When file_routing_map maps a criterion to files that are all empty/missing,
    the criterion also scores 0 (no fallback to full submission text).
  - When no routing map is provided, the Q-number heuristic still works correctly.

Bug reference: Cross-contamination where a student submitting only Q2.ipynb + Q3.ipynb
was incorrectly awarded marks for Q1 criteria using Q3.ipynb content as "evidence".
Root cause: orchestrator fell back to full submission text when routing returned [].
Fix: router [] → score 0 immediately, no LLM call.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.agents.base import CheckpointResult, GradingResult
from app.services.agents.orchestrator import (
    GradingOrchestrator,
    _extract_question_number,
    _filter_for_question,
    filter_routing_map_by_question,
)


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def make_file(filename: str, text_content: str) -> dict:
    return {"filename": filename, "text_content": text_content}


def make_checkpoint_dict(
    checkpoint_id: str,
    criterion: str,
    description: str = "test criterion",
    points: float = 2.0,
) -> dict:
    return {
        "id": checkpoint_id,
        "criterion": criterion,
        "description": description,
        "points": points,
    }


def make_criterion(criterion: str, max: float = 2.0) -> dict:
    return {"criterion": criterion, "max": max, "description": f"Test criterion: {criterion}"}


async def _run(coro):
    """Run a coroutine in the test."""
    return await coro


# ─────────────────────────────────────────────────────────────────
# Tests: filter_routing_map_by_question
# ─────────────────────────────────────────────────────────────────

class TestFilterRoutingMapByQuestion:
    """
    Tests for the Q-number cross-contamination filter.

    The LLM router can return false-positive mappings: e.g. it maps Q3.ipynb
    to 'Q1b(iii) - Return count of matches' because Q3 also contains a count.
    This filter removes such cross-question mappings.
    """

    def test_removes_cross_question_files(self):
        """Q1 criterion must not get Q2/Q3 files."""
        routing = {
            "Q1b(iii) - Return count of matches": ["Q3.ipynb", "Q1.ipynb"],
            "Q2b - Implement sorting": ["Q2.ipynb"],
            "Q3c - Extract approved": ["Q3.ipynb"],
        }
        result = filter_routing_map_by_question(routing)
        assert "Q3.ipynb" not in result["Q1b(iii) - Return count of matches"]
        assert "Q1.ipynb" in result["Q1b(iii) - Return count of matches"]

    def test_q1_criterion_no_q1_file_becomes_empty(self):
        """
        Critical regression: Q1 criterion with only Q2/Q3 files → empty list
        (so orchestrator short-circuits to score 0).
        """
        routing = {
            "Q1b(iii) - Return count of matches": ["Q3.ipynb"],  # LLM false positive
            "Q2b - Implement sorting": ["Q2.ipynb"],
        }
        result = filter_routing_map_by_question(routing)
        assert result["Q1b(iii) - Return count of matches"] == [], (
            "Q3.ipynb should be removed from Q1 criterion, leaving []"
        )

    def test_generic_criterion_keeps_all_files(self):
        """Criteria without a Q-number keep all their files."""
        routing = {
            "Code Quality": ["Q1.ipynb", "Q2.ipynb", "Q3.ipynb"],
        }
        result = filter_routing_map_by_question(routing)
        assert result["Code Quality"] == ["Q1.ipynb", "Q2.ipynb", "Q3.ipynb"]

    def test_generic_filename_kept_for_any_criterion(self):
        """Files without a Q-number prefix (solution.py, main.py) are always kept."""
        routing = {
            "Q1a - Count messages": ["solution.py", "Q2.ipynb"],
        }
        result = filter_routing_map_by_question(routing)
        assert "solution.py" in result["Q1a - Count messages"]
        assert "Q2.ipynb" not in result["Q1a - Count messages"]

    def test_correct_q_files_preserved(self):
        """Q1 files stay with Q1 criteria, Q2 files stay with Q2 criteria."""
        routing = {
            "Q1a - Count messages": ["Q1.ipynb", "Q1 THEORY.txt"],
            "Q2b - Sorting": ["Q2.ipynb", "Q2 THEORY.txt"],
            "Q3c - Orders": ["Q3.ipynb"],
        }
        result = filter_routing_map_by_question(routing)
        assert set(result["Q1a - Count messages"]) == {"Q1.ipynb", "Q1 THEORY.txt"}
        assert set(result["Q2b - Sorting"]) == {"Q2.ipynb", "Q2 THEORY.txt"}
        assert result["Q3c - Orders"] == ["Q3.ipynb"]

    def test_empty_routing_map_unchanged(self):
        assert filter_routing_map_by_question({}) == {}

    def test_empty_file_lists_unchanged(self):
        routing = {"Q1a - Count messages": [], "Q2b - Sorting": []}
        result = filter_routing_map_by_question(routing)
        assert result["Q1a - Count messages"] == []
        assert result["Q2b - Sorting"] == []

    def test_does_not_mutate_input(self):
        """filter_routing_map_by_question must return a new dict."""
        routing = {
            "Q1b(iii) - Return count": ["Q3.ipynb"],
        }
        original_routing = {"Q1b(iii) - Return count": ["Q3.ipynb"]}
        _ = filter_routing_map_by_question(routing)
        assert routing == original_routing


# ─────────────────────────────────────────────────────────────────
# Tests: _extract_question_number
# ─────────────────────────────────────────────────────────────────

class TestExtractQuestionNumber:
    def test_q1_prefix(self):
        assert _extract_question_number("Q1a(i) - Count messages per sender") == "1"

    def test_q2_prefix(self):
        assert _extract_question_number("Q2d - Implement BFS") == "2"

    def test_q3_prefix(self):
        assert _extract_question_number("Q3c(iii) - Extract approved orders") == "3"

    def test_no_prefix(self):
        assert _extract_question_number("Code Quality") is None

    def test_lowercase_q(self):
        assert _extract_question_number("q1a - something") == "1"

    def test_empty_string(self):
        assert _extract_question_number("") is None


# ─────────────────────────────────────────────────────────────────
# Tests: _filter_for_question
# ─────────────────────────────────────────────────────────────────

class TestFilterForQuestion:
    """Tests for the Q-number heuristic (fallback when no routing map)."""

    def test_q1_file_matched(self):
        files = [
            make_file("Q1.ipynb", "# Q1 content here"),
            make_file("Q2.ipynb", "# Q2 content here"),
            make_file("Q3.ipynb", "# Q3 content here"),
        ]
        result = _filter_for_question("1", files, "full text")
        assert "Q1 content here" in result
        assert "Q2 content here" not in result
        assert "Q3 content here" not in result

    def test_no_matching_file_falls_back_to_full_text(self):
        """When no file matches the Q-number, use full text (single-file case)."""
        files = [make_file("solution.py", "# all answers here")]
        result = _filter_for_question("1", files, "the full submission text")
        assert result == "the full submission text"

    def test_multiple_q1_files_all_included(self):
        files = [
            make_file("Q1.ipynb", "Q1 notebook"),
            make_file("Q1 THEORY.txt", "Q1 theory text"),
            make_file("Q3.ipynb", "Q3 stuff"),
        ]
        result = _filter_for_question("1", files, "full text")
        assert "Q1 notebook" in result
        assert "Q1 theory text" in result
        assert "Q3 stuff" not in result


# ─────────────────────────────────────────────────────────────────
# Tests: Cross-contamination fix (the critical regression tests)
# ─────────────────────────────────────────────────────────────────

class TestCrossContaminationFix:
    """
    Core regression tests for the cross-contamination bug fix.

    Scenario: Muhammad Maaz submitted Q2 THEORY.txt, Q2.ipynb, Q3.ipynb.
    The file router correctly returns [] for all Q1 criteria.
    Before the fix: orchestrator fell back to full submission text,
      and the judge found Q3 content and awarded Q1 marks.
    After the fix: router [] → all Q1 checkpoints score 0 immediately.
    """

    def _make_orchestrator(self) -> GradingOrchestrator:
        return GradingOrchestrator(rate_limiter=None)

    def _make_q1_q2_q3_setup(self):
        """Build a session with Q1, Q2, Q3 criteria and checkpoints."""
        criteria = [
            make_criterion("Q1a - Count messages per sender", max=2.0),
            make_criterion("Q2b - Implement sorting", max=3.0),
            make_criterion("Q3c - Extract approved orders", max=2.0),
        ]
        checkpoints_by_criterion = {
            "Q1a - Count messages per sender": [
                make_checkpoint_dict("q1a_cp1", "Q1a - Count messages per sender",
                                     "Counts per sender correctly", 1.0),
                make_checkpoint_dict("q1a_cp2", "Q1a - Count messages per sender",
                                     "Handles edge cases", 1.0),
            ],
            "Q2b - Implement sorting": [
                make_checkpoint_dict("q2b_cp1", "Q2b - Implement sorting",
                                     "Sorting logic correct", 3.0),
            ],
            "Q3c - Extract approved orders": [
                make_checkpoint_dict("q3c_cp1", "Q3c - Extract approved orders",
                                     "Filters correctly", 2.0),
            ],
        }
        student_files = [
            make_file("Q2 THEORY.txt", "Theory about sorting algorithms"),
            make_file("Q2.ipynb", "def sort(arr): return sorted(arr)"),
            make_file("Q3.ipynb", "approved = [o for o in orders if o['approved']]"),
        ]
        submission_text = "\n\n".join(f["text_content"] for f in student_files)
        return criteria, checkpoints_by_criterion, student_files, submission_text

    @pytest.mark.asyncio
    async def test_router_empty_list_scores_zero_no_llm_call(self):
        """
        CRITICAL: When routing returns [] for Q1 criteria, all Q1 checkpoints
        must score 0 without calling the LLM judge.
        """
        orchestrator = self._make_orchestrator()
        criteria, checkpoints_by_criterion, student_files, submission_text = (
            self._make_q1_q2_q3_setup()
        )

        # Routing: Q1 → [] (student has no Q1 file)
        #          Q2 → files found, Q3 → files found
        file_routing_map = {
            "Q1a - Count messages per sender": [],          # no Q1 file submitted
            "Q2b - Implement sorting": ["Q2.ipynb", "Q2 THEORY.txt"],
            "Q3c - Extract approved orders": ["Q3.ipynb"],
        }

        # Patch judge_checkpoint — it must NOT be called for Q1 criteria
        with patch(
            "app.services.agents.orchestrator.judge_checkpoint",
            new_callable=AsyncMock,
        ) as mock_judge:
            # Make judge return a dummy result for Q2/Q3
            mock_judge.return_value = CheckpointResult(
                checkpoint_id="mock",
                criterion="mock",
                points_max=3.0,
                score_percent=75,
                points_awarded=2.25,
                reasoning="Good work",
                evidence_quote="sorted(arr)",
                source_file="Q2.ipynb",
                confidence="high",
                model_used="test",
            )

            result = await orchestrator.grade_student(
                student_id=999,
                session_id=1,
                checkpoints_by_criterion=checkpoints_by_criterion,
                criteria=criteria,
                submission_text=submission_text,
                student_files=student_files,
                title="Lab Exam",
                max_score=7.0,
                file_routing_map=file_routing_map,
            )

        # The judge should have been called for Q2 and Q3, NOT for Q1
        # Q1 has 2 checkpoints, Q2 has 1, Q3 has 1 → judge called 2 times (not 4)
        assert mock_judge.call_count == 2, (
            f"Judge was called {mock_judge.call_count} times. "
            "It should NOT be called for Q1 criteria (routing returned [])."
        )

        # All Q1 checkpoints must score 0
        q1_results = [cp for cp in result.checkpoints
                      if "Q1a" in cp.criterion]
        assert len(q1_results) == 2, f"Expected 2 Q1 checkpoints, got {len(q1_results)}"
        for cp in q1_results:
            assert cp.score_percent == 0, (
                f"Q1 checkpoint '{cp.checkpoint_id}' scored {cp.score_percent}% "
                "but should be 0 (no file submitted)."
            )
            assert cp.points_awarded == 0.0
            assert cp.verification_method == "router_no_file"
            assert cp.model_used == "none"

    @pytest.mark.asyncio
    async def test_router_empty_list_q1_does_not_use_q3_content(self):
        """
        Regression test for the exact bug: Q3.ipynb content must NOT appear
        as evidence in Q1 checkpoint results.
        """
        orchestrator = self._make_orchestrator()
        criteria, checkpoints_by_criterion, student_files, submission_text = (
            self._make_q1_q2_q3_setup()
        )

        file_routing_map = {
            "Q1a - Count messages per sender": [],
            "Q2b - Implement sorting": ["Q2.ipynb"],
            "Q3c - Extract approved orders": ["Q3.ipynb"],
        }

        with patch(
            "app.services.agents.orchestrator.judge_checkpoint",
            new_callable=AsyncMock,
        ) as mock_judge:
            mock_judge.return_value = CheckpointResult(
                checkpoint_id="mock",
                criterion="mock",
                points_max=3.0,
                score_percent=75,
                points_awarded=2.25,
                reasoning="Good work",
                evidence_quote="sorted(arr)",
                source_file="Q2.ipynb",
                confidence="high",
                model_used="test",
            )

            result = await orchestrator.grade_student(
                student_id=999,
                session_id=1,
                checkpoints_by_criterion=checkpoints_by_criterion,
                criteria=criteria,
                submission_text=submission_text,
                student_files=student_files,
                title="Lab Exam",
                max_score=7.0,
                file_routing_map=file_routing_map,
            )

        q1_results = [cp for cp in result.checkpoints if "Q1a" in cp.criterion]
        for cp in q1_results:
            # The Q3 evidence string must never appear in a Q1 result
            assert "approved" not in cp.evidence_quote.lower(), (
                f"Q1 checkpoint contains Q3 evidence: '{cp.evidence_quote}'. "
                "This is the cross-contamination bug."
            )
            assert cp.source_file not in ("Q3.ipynb", "Q2.ipynb"), (
                f"Q1 checkpoint source_file is '{cp.source_file}' — "
                "cross-contamination: evidence from a different question's file."
            )

    @pytest.mark.asyncio
    async def test_router_empty_files_all_empty_scores_zero(self):
        """
        When routing points to specific files but all those files are empty
        (content is blank), it should also score 0, not fall back to full text.
        """
        orchestrator = self._make_orchestrator()
        criteria = [make_criterion("Q1a - Count messages per sender", max=2.0)]
        checkpoints_by_criterion = {
            "Q1a - Count messages per sender": [
                make_checkpoint_dict("q1a_cp1", "Q1a - Count messages per sender", "test", 2.0),
            ],
        }
        # Q1 file exists but is empty
        student_files = [
            make_file("Q1.ipynb", ""),           # empty Q1 file
            make_file("Q3.ipynb", "rich Q3 content that should not pollute Q1"),
        ]
        submission_text = "rich Q3 content that should not pollute Q1"

        file_routing_map = {
            "Q1a - Count messages per sender": ["Q1.ipynb"],  # routed but empty
        }

        with patch(
            "app.services.agents.orchestrator.judge_checkpoint",
            new_callable=AsyncMock,
        ) as mock_judge:
            result = await orchestrator.grade_student(
                student_id=999,
                session_id=1,
                checkpoints_by_criterion=checkpoints_by_criterion,
                criteria=criteria,
                submission_text=submission_text,
                student_files=student_files,
                title="Lab Exam",
                max_score=2.0,
                file_routing_map=file_routing_map,
            )

        # Judge must NOT be called — routed file was empty
        assert mock_judge.call_count == 0, (
            "Judge was called despite routing pointing to an empty file. "
            "Should have scored 0 immediately."
        )
        q1_results = [cp for cp in result.checkpoints if "Q1a" in cp.criterion]
        assert len(q1_results) == 1
        assert q1_results[0].score_percent == 0
        assert q1_results[0].points_awarded == 0.0

    @pytest.mark.asyncio
    async def test_routing_with_valid_files_passes_correct_content_to_judge(self):
        """
        When routing returns specific files, the judge must be called with
        ONLY the content of those files — not the full submission text.
        """
        orchestrator = self._make_orchestrator()
        criteria = [make_criterion("Q2b - Implement sorting", max=3.0)]
        checkpoints_by_criterion = {
            "Q2b - Implement sorting": [
                make_checkpoint_dict("q2b_cp1", "Q2b - Implement sorting", "test", 3.0),
            ],
        }
        student_files = [
            make_file("Q2.ipynb", "def sort(arr): return sorted(arr)"),
            make_file("Q3.ipynb", "def unrelated_q3_function(): pass"),
        ]
        submission_text = (
            "def sort(arr): return sorted(arr)\n"
            "def unrelated_q3_function(): pass"
        )

        file_routing_map = {
            "Q2b - Implement sorting": ["Q2.ipynb"],
        }

        captured_content: list[str] = []

        async def mock_judge_capture(**kwargs):
            captured_content.append(kwargs.get("submission_content", ""))
            return CheckpointResult(
                checkpoint_id="q2b_cp1",
                criterion="Q2b - Implement sorting",
                points_max=3.0,
                score_percent=100,
                points_awarded=3.0,
                reasoning="Correct",
                evidence_quote="sorted(arr)",
                source_file="Q2.ipynb",
                confidence="high",
                model_used="test",
            )

        with patch(
            "app.services.agents.orchestrator.judge_checkpoint",
            side_effect=mock_judge_capture,
        ):
            await orchestrator.grade_student(
                student_id=999,
                session_id=1,
                checkpoints_by_criterion=checkpoints_by_criterion,
                criteria=criteria,
                submission_text=submission_text,
                student_files=student_files,
                title="Lab Exam",
                max_score=3.0,
                file_routing_map=file_routing_map,
            )

        assert len(captured_content) == 1
        content_sent = captured_content[0]

        # Only Q2.ipynb content should be in the prompt
        assert "sort(arr)" in content_sent, "Q2 file content missing from judge prompt"
        assert "unrelated_q3_function" not in content_sent, (
            "Q3 content leaked into Q2 judge prompt — routing not filtering correctly."
        )

    @pytest.mark.asyncio
    async def test_no_routing_map_uses_q_heuristic(self):
        """
        When no file_routing_map is provided, the Q-number heuristic is used.
        Q1 criteria should only see Q1 files.
        """
        orchestrator = self._make_orchestrator()
        criteria = [make_criterion("Q1a - Count messages", max=2.0)]
        checkpoints_by_criterion = {
            "Q1a - Count messages": [
                make_checkpoint_dict("q1a_cp1", "Q1a - Count messages", "test", 2.0),
            ],
        }
        student_files = [
            make_file("Q1.ipynb", "count = len(messages)"),
            make_file("Q3.ipynb", "approved = filter_orders(orders)"),
        ]
        submission_text = "count = len(messages)\napproved = filter_orders(orders)"

        captured_content: list[str] = []

        async def mock_judge_capture(**kwargs):
            captured_content.append(kwargs.get("submission_content", ""))
            return CheckpointResult(
                checkpoint_id="q1a_cp1",
                criterion="Q1a - Count messages",
                points_max=2.0,
                score_percent=100,
                points_awarded=2.0,
                reasoning="Correct",
                evidence_quote="count = len(messages)",
                source_file="Q1.ipynb",
                confidence="high",
                model_used="test",
            )

        with patch(
            "app.services.agents.orchestrator.judge_checkpoint",
            side_effect=mock_judge_capture,
        ):
            await orchestrator.grade_student(
                student_id=999,
                session_id=1,
                checkpoints_by_criterion=checkpoints_by_criterion,
                criteria=criteria,
                submission_text=submission_text,
                student_files=student_files,
                title="Lab Exam",
                max_score=2.0,
                file_routing_map=None,  # No routing map — use heuristic
            )

        assert len(captured_content) == 1
        content_sent = captured_content[0]

        # Heuristic should have filtered to Q1 file only
        assert "count = len(messages)" in content_sent
        assert "filter_orders" not in content_sent, (
            "Q3 content leaked into Q1 judge prompt (Q-number heuristic not working)."
        )

    @pytest.mark.asyncio
    async def test_all_criteria_empty_routing_total_score_zero(self):
        """
        If routing returns [] for ALL criteria (student submitted nothing relevant),
        total score must be 0.
        """
        orchestrator = self._make_orchestrator()
        criteria = [
            make_criterion("Q1a - Count messages", max=2.0),
            make_criterion("Q2b - Sort data", max=3.0),
        ]
        checkpoints_by_criterion = {
            "Q1a - Count messages": [
                make_checkpoint_dict("q1_cp1", "Q1a - Count messages", "test", 2.0),
            ],
            "Q2b - Sort data": [
                make_checkpoint_dict("q2_cp1", "Q2b - Sort data", "test", 3.0),
            ],
        }
        student_files = [make_file("random.txt", "something unrelated")]
        submission_text = "something unrelated"

        file_routing_map = {
            "Q1a - Count messages": [],
            "Q2b - Sort data": [],
        }

        with patch(
            "app.services.agents.orchestrator.judge_checkpoint",
            new_callable=AsyncMock,
        ) as mock_judge:
            result = await orchestrator.grade_student(
                student_id=999,
                session_id=1,
                checkpoints_by_criterion=checkpoints_by_criterion,
                criteria=criteria,
                submission_text=submission_text,
                student_files=student_files,
                title="Lab Exam",
                max_score=5.0,
                file_routing_map=file_routing_map,
            )

        assert mock_judge.call_count == 0
        assert result.total_score == 0.0
        assert result.score_percent == 0.0
        for cp in result.checkpoints:
            assert cp.score_percent == 0
            assert cp.model_used == "none"


# ─────────────────────────────────────────────────────────────────
# Tests: Routing with image files
# ─────────────────────────────────────────────────────────────────

class TestRoutingWithImages:
    """Tests that routing sends actual images for image-containing files."""

    def _make_image_file(self, filename: str) -> dict:
        """File with images but no text (e.g. scanned PDF, photo)."""
        return {
            "filename": filename,
            "text_content": "",
            "images": [
                {
                    "base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                    "media_type": "image/png",
                    "source": "page_1",
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_image_file_sends_multimodal_routing_message(self):
        """When a file has images, routing API call uses multimodal content list."""
        from app.services.checkpoint_grader import route_files_to_criteria

        files = [
            self._make_image_file("diagram.png"),
            make_file("code.py", "def sort(arr): return sorted(arr)"),
        ]
        criteria = [make_criterion("Q1 - Draw diagram", max=5.0)]

        captured_messages = []

        def fake_create(**kwargs):
            captured_messages.append(kwargs["messages"])
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = '{"routing": {"Q1 - Draw diagram": ["diagram.png"]}}'
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("openai.OpenAI", return_value=mock_client):
            result = await route_files_to_criteria(
                criteria=criteria,
                student_files=files,
            )

        assert len(captured_messages) == 1
        user_msg = captured_messages[0][1]
        # When batch has images, content must be a list (multimodal)
        assert isinstance(user_msg["content"], list), (
            "Routing message must be a list when batch contains image files"
        )
        types = [p.get("type") for p in user_msg["content"]]
        assert "image_url" in types, "No image was included in the routing message"

    @pytest.mark.asyncio
    async def test_text_only_files_send_string_routing_message(self):
        """When all files are text-only, routing API receives plain string (no regression)."""
        from app.services.checkpoint_grader import route_files_to_criteria

        files = [make_file("code.py", "def sort(arr): return sorted(arr)")]
        criteria = [make_criterion("Q1 - Sort", max=3.0)]

        captured_messages = []

        def fake_create(**kwargs):
            captured_messages.append(kwargs["messages"])
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = '{"routing": {"Q1 - Sort": ["code.py"]}}'
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("openai.OpenAI", return_value=mock_client):
            await route_files_to_criteria(criteria=criteria, student_files=files)

        user_msg = captured_messages[0][1]
        assert isinstance(user_msg["content"], str), (
            "Text-only routing must still use a plain string (no regression)"
        )

    @pytest.mark.asyncio
    async def test_routing_image_uses_low_detail(self):
        """Routing images use detail:low (efficient) not detail:high."""
        from app.services.checkpoint_grader import route_files_to_criteria

        files = [self._make_image_file("scan.png")]
        criteria = [make_criterion("Q1 - Diagram", max=3.0)]

        captured_messages = []

        def fake_create(**kwargs):
            captured_messages.append(kwargs["messages"])
            mock_resp = MagicMock()
            mock_resp.choices[0].message.content = '{"routing": {"Q1 - Diagram": ["scan.png"]}}'
            return mock_resp

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = fake_create

        with patch("openai.OpenAI", return_value=mock_client):
            await route_files_to_criteria(criteria=criteria, student_files=files)

        user_content = captured_messages[0][1]["content"]
        image_parts = [p for p in user_content if p.get("type") == "image_url"]
        assert len(image_parts) == 1
        assert image_parts[0]["image_url"]["detail"] == "low", (
            "Routing images must use detail:low for efficiency"
        )


# ─────────────────────────────────────────────────────────────────
# Tests: Orchestrator image support
# ─────────────────────────────────────────────────────────────────

class TestOrchestratorImageSupport:
    """Tests that the orchestrator passes images to the judge."""

    def _make_image_extracted(self, filename: str, text: str = "") -> dict:
        return {
            "filename": filename,
            "text_content": text,
            "images": [
                {
                    "base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
                    "media_type": "image/png",
                    "source": "page_1",
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_images_passed_to_judge_for_image_only_file(self):
        """When a routed file has images, judge receives submission_images."""
        orchestrator = GradingOrchestrator()
        criteria = [make_criterion("Q1 - Draw diagram", max=5.0)]
        checkpoints_by_criterion = {
            "Q1 - Draw diagram": [
                make_checkpoint_dict("cp1", "Q1 - Draw diagram", "Correct diagram", 5.0)
            ]
        }
        student_files = [self._make_image_extracted("diagram.png")]
        file_routing_map = {"Q1 - Draw diagram": ["diagram.png"]}

        captured_images = []

        async def mock_judge(**kwargs):
            captured_images.append(kwargs.get("submission_images"))
            return CheckpointResult(
                checkpoint_id="cp1",
                criterion="Q1 - Draw diagram",
                points_max=5.0,
                score_percent=100,
                points_awarded=5.0,
                reasoning="Good diagram",
                evidence_quote="[VISUAL] Correct BST drawn",
                source_file="diagram.png",
                confidence="high",
                model_used="test",
            )

        with patch("app.services.agents.orchestrator.judge_checkpoint", side_effect=mock_judge):
            result = await orchestrator.grade_student(
                student_id=1, session_id=1,
                checkpoints_by_criterion=checkpoints_by_criterion,
                criteria=criteria,
                submission_text="",
                student_files=student_files,
                title="Test",
                max_score=5.0,
                file_routing_map=file_routing_map,
            )

        assert len(captured_images) == 1
        assert captured_images[0] is not None and len(captured_images[0]) > 0, (
            "Judge must receive images for image-only files"
        )

    @pytest.mark.asyncio
    async def test_image_only_submission_not_treated_as_empty(self):
        """A submission with only images (no text) must NOT score 0 via empty-submission path."""
        orchestrator = GradingOrchestrator()
        criteria = [make_criterion("Q1 - Draw diagram", max=5.0)]
        checkpoints_by_criterion = {
            "Q1 - Draw diagram": [
                make_checkpoint_dict("cp1", "Q1 - Draw diagram", "test", 5.0)
            ]
        }
        student_files = [self._make_image_extracted("scan.pdf", text="")]
        judge_call_count = []

        async def mock_judge(**kwargs):
            judge_call_count.append(1)
            return CheckpointResult(
                checkpoint_id="cp1", criterion="Q1 - Draw diagram",
                points_max=5.0, score_percent=75, points_awarded=3.75,
                reasoning="ok", evidence_quote="[VISUAL] attempt", source_file="scan.pdf",
                confidence="medium", model_used="test",
            )

        with patch("app.services.agents.orchestrator.judge_checkpoint", side_effect=mock_judge):
            result = await orchestrator.grade_student(
                student_id=1, session_id=1,
                checkpoints_by_criterion=checkpoints_by_criterion,
                criteria=criteria,
                submission_text="",  # no text — image-only
                student_files=student_files,
                title="Test", max_score=5.0,
                file_routing_map=None,
            )

        assert len(judge_call_count) == 1, (
            "Judge must be called for image-only submission (not treated as empty)"
        )


# ─────────────────────────────────────────────────────────────────
# Tests: B4 — unverified nonzero flag helper
# ─────────────────────────────────────────────────────────────────

class TestUnverifiedNonzeroFlag:
    """Tests for _flag_unverified_nonzero module-level helper."""

    def test_unverified_nonzero_score_adds_flag(self):
        """_flag_unverified_nonzero must set needs_review and add flag string."""
        from app.services.agents.base import CheckpointResult
        from app.services.agents.orchestrator import _flag_unverified_nonzero

        cp = CheckpointResult(
            checkpoint_id="cp1",
            criterion="Q1",
            points_max=5,
            points_awarded=3.75,
            score_percent=75,
            verified=False,
            verification_method="unverified",
        )
        assert not cp.needs_review
        _flag_unverified_nonzero(cp)
        assert cp.needs_review is True
        assert any("unverified" in f.lower() for f in cp.flags), (
            f"Expected a flag with 'unverified' but got: {cp.flags}"
        )

    def test_verified_checkpoint_not_flagged_by_unverified_helper(self):
        """_flag_unverified_nonzero must NOT touch verified checkpoints."""
        from app.services.agents.base import CheckpointResult
        from app.services.agents.orchestrator import _flag_unverified_nonzero

        cp = CheckpointResult(
            checkpoint_id="cp2",
            criterion="Q1",
            points_max=5,
            points_awarded=5.0,
            score_percent=100,
            verified=True,
            verification_method="exact",
        )
        _flag_unverified_nonzero(cp)
        assert cp.needs_review is False   # verified — must not be flagged
        assert cp.flags == []

    def test_zero_score_unverified_not_flagged(self):
        """An unverified checkpoint with zero points is not suspicious — should not be flagged."""
        from app.services.agents.base import CheckpointResult
        from app.services.agents.orchestrator import _flag_unverified_nonzero

        cp = CheckpointResult(
            checkpoint_id="cp3",
            criterion="Q1",
            points_max=5,
            points_awarded=0.0,
            score_percent=0,
            verified=False,
            verification_method="unverified",
        )
        _flag_unverified_nonzero(cp)
        assert cp.needs_review is False
        assert cp.flags == []

    def test_idempotent_flag_not_duplicated(self):
        """Calling _flag_unverified_nonzero twice must not add the flag twice."""
        from app.services.agents.base import CheckpointResult
        from app.services.agents.orchestrator import _flag_unverified_nonzero

        cp = CheckpointResult(
            checkpoint_id="cp4",
            criterion="Q1",
            points_max=5,
            points_awarded=2.5,
            score_percent=50,
            verified=False,
            verification_method="unverified",
        )
        _flag_unverified_nonzero(cp)
        _flag_unverified_nonzero(cp)
        assert len(cp.flags) == 1, "Flag should not be duplicated on second call"
