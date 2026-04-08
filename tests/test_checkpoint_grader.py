"""Comprehensive tests for the checkpoint-based grading system.

Tests cover:
1. Evidence verification (exact match, fuzzy match, visual, edge cases)
2. Keyword verification
3. Cross-checkpoint consistency
4. Deterministic scoring
5. Flagging logic
6. Student text collection
7. Prompt building
8. Error result building
9. Letter grade conversion
10. Full grading flow (with mock LLM)
"""

import json
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from app.services.checkpoint_grader import (
    verify_evidence_exists,
    verify_checkpoint_keywords,
    check_cross_checkpoint_consistency,
    score_criterion_from_checkpoints,
    compute_criterion_flags,
    collect_student_text,
    build_checkpoint_grading_prompt,
    build_retry_prompt_for_checkpoints,
    grade_with_checkpoints,
    _build_error_result,
    _percentage_to_letter,
    _normalize_text,
    _is_clear_fabrication,
    get_evidence_flag_tier,
    _strip_to_chars,
)


# ═══════════════════════════════════════════════════════════════
# 1. EVIDENCE VERIFICATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestVerifyEvidenceExists:
    """Test space-stripped evidence verification system."""

    def test_exact_substring_match(self):
        content = "def bfs(graph, start):\n    queue = deque([start])\n    visited = set()"
        quote = "queue = deque([start])"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is True
        assert sim == 1.0

    def test_case_insensitive_match(self):
        content = "The BFS Algorithm traverses level by level"
        quote = "the bfs algorithm traverses level by level"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is True
        assert sim >= 0.95

    def test_whitespace_stripped_match(self):
        """Core test: different whitespace must match after stripping."""
        content = "def bfs(graph, start):\n    queue = deque([start])\n    visited = set()\n    while queue:"
        quote = "def bfs(graph, start):\n  queue = deque([start])\n  visited = set()"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is True
        assert sim >= 0.95

    def test_code_with_different_indentation(self):
        """LLM reformats indentation — stripped matching handles this."""
        content = "    for node in graph[current]:\n        if node not in visited:\n            queue.append(node)"
        quote = "for node in graph[current]:\n    if node not in visited:\n        queue.append(node)"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is True
        assert sim == 1.0  # stripped exact match

    def test_sliding_window_match(self):
        content = "hello world this is a test of the BFS algorithm implementation in python for graph traversal"
        quote = "test of the BFS algorithm implementation in python"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is True
        assert sim >= 0.75

    def test_short_word_match(self):
        content = "BFS uses queue data structure for traversal"
        quote = "queue data"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is True

    def test_empty_quote_returns_false(self):
        found, sim, method = verify_evidence_exists("", "some content")
        assert found is False
        assert method == "empty_quote"

    def test_whitespace_only_quote(self):
        found, sim, method = verify_evidence_exists("   ", "some content")
        assert found is False
        assert method == "empty_quote"

    def test_empty_content(self):
        found, sim, method = verify_evidence_exists("test", "")
        assert found is False

    def test_visual_evidence_always_passes(self):
        found, sim, method = verify_evidence_exists("[VISUAL] The diagram shows a tree", "")
        assert found is True
        assert sim == 1.0
        assert method == "visual_evidence"

    def test_fabricated_evidence_rejected(self):
        content = "def hello():\n    print('world')"
        quote = "def bfs(graph, start): queue = deque([start])"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is False

    def test_similar_but_not_matching(self):
        content = "The quick brown fox jumps over the lazy dog"
        quote = "A slow red cat walks under the active cat"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is False

    def test_multiline_exact_match(self):
        content = "line1\nline2\nline3\nline4\nline5"
        quote = "line2\nline3\nline4"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is True

    def test_deterministic_same_input_same_output(self):
        """Critical: same inputs must always produce same outputs."""
        content = "def bfs(graph):\n    queue = []\n    visited = set()"
        quote = "queue = []\n    visited = set()"
        results = [verify_evidence_exists(quote, content) for _ in range(10)]
        assert all(r == results[0] for r in results), "Evidence verification must be deterministic"

    def test_similarity_tiers_green(self):
        """Exact match in stripped form → similarity 1.0 (green tier)."""
        content = "count = {}\nfor c in comments:\n    name = c['user']"
        quote = "count = {} for c in comments: name = c['user']"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is True
        assert sim >= 0.80  # Green tier

    def test_similarity_tiers_red(self):
        """Completely fabricated evidence → low similarity (red tier)."""
        content = "x = 5\ny = 10\nprint(x + y)"
        quote = "The quantum mechanical wave function collapses upon observation by the researcher"
        found, sim, method = verify_evidence_exists(quote, content)
        assert found is False
        assert sim < 0.20  # Red tier


# ═══════════════════════════════════════════════════════════════
# 2. KEYWORD VERIFICATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestVerifyCheckpointKeywords:

    def test_keywords_present_passes(self):
        cp = {"verification_keywords": ["deque|queue|list", "append|push"]}
        consistent, reason = verify_checkpoint_keywords(cp, "queue = deque([start])\nqueue.append(node)", True)
        assert consistent is True
        assert reason is None

    def test_all_keywords_missing_fails(self):
        cp = {"verification_keywords": ["deque|queue", "append|push"]}
        consistent, reason = verify_checkpoint_keywords(cp, "x = 5\ny = 10", True)
        assert consistent is False
        assert "ALL expected patterns" in reason

    def test_some_keywords_present_passes(self):
        """If some keywords match, it's not a hallucination."""
        cp = {"verification_keywords": ["deque|queue", "visited|seen", "BFS|breadth"]}
        consistent, reason = verify_checkpoint_keywords(cp, "queue = deque([start])", True)
        assert consistent is True  # Only 1 of 3 missing — not ALL

    def test_failed_checkpoint_skips_keywords(self):
        cp = {"verification_keywords": ["deque|queue"]}
        consistent, reason = verify_checkpoint_keywords(cp, "nothing here", False)
        assert consistent is True  # Don't check keywords on failed checkpoints

    def test_no_keywords_passes(self):
        cp = {"verification_keywords": []}
        consistent, reason = verify_checkpoint_keywords(cp, "anything", True)
        assert consistent is True

    def test_no_keywords_key_passes(self):
        cp = {}
        consistent, reason = verify_checkpoint_keywords(cp, "anything", True)
        assert consistent is True

    def test_visual_evidence_skips_keywords(self):
        cp = {"verification_keywords": ["deque"]}
        consistent, reason = verify_checkpoint_keywords(cp, "[VISUAL] Shows a queue diagram", True)
        assert consistent is True

    def test_bad_regex_skipped(self):
        cp = {"verification_keywords": ["[invalid regex", "deque"]}
        consistent, reason = verify_checkpoint_keywords(cp, "queue = deque()", True)
        assert consistent is True  # bad regex skipped, deque matches


# ═══════════════════════════════════════════════════════════════
# 3. CROSS-CHECKPOINT CONSISTENCY TESTS
# ═══════════════════════════════════════════════════════════════

class TestCrossCheckpointConsistency:

    def test_no_dependencies_no_issues(self):
        results = [
            {"checkpoint_id": "cp1", "pass": True},
            {"checkpoint_id": "cp2", "pass": False},
        ]
        checkpoints = [
            {"id": "cp1", "description": "First"},
            {"id": "cp2", "description": "Second"},
        ]
        issues = check_cross_checkpoint_consistency(results, checkpoints)
        assert issues == []

    def test_dependency_violation_detected(self):
        results = [
            {"checkpoint_id": "cp1", "pass": False},
            {"checkpoint_id": "cp2", "pass": True},  # passes but depends on cp1
        ]
        checkpoints = [
            {"id": "cp1", "description": "Function defined"},
            {"id": "cp2", "description": "Function uses correct algorithm", "depends_on": ["cp1"]},
        ]
        issues = check_cross_checkpoint_consistency(results, checkpoints)
        assert len(issues) == 1
        assert "passes but depends on" in issues[0]

    def test_dependencies_satisfied_no_issues(self):
        results = [
            {"checkpoint_id": "cp1", "pass": True},
            {"checkpoint_id": "cp2", "pass": True},
        ]
        checkpoints = [
            {"id": "cp1", "description": "Function defined"},
            {"id": "cp2", "description": "Function uses correct algorithm", "depends_on": ["cp1"]},
        ]
        issues = check_cross_checkpoint_consistency(results, checkpoints)
        assert issues == []

    def test_failed_dependent_no_issue(self):
        """If dependent checkpoint fails, no contradiction."""
        results = [
            {"checkpoint_id": "cp1", "pass": False},
            {"checkpoint_id": "cp2", "pass": False},
        ]
        checkpoints = [
            {"id": "cp1", "description": "Function defined"},
            {"id": "cp2", "description": "Uses correct algorithm", "depends_on": ["cp1"]},
        ]
        issues = check_cross_checkpoint_consistency(results, checkpoints)
        assert issues == []


# ═══════════════════════════════════════════════════════════════
# 4. DETERMINISTIC SCORING TESTS
# ═══════════════════════════════════════════════════════════════

class TestScoreCriterionFromCheckpoints:

    def test_all_verified_passes(self):
        cps = [
            {"id": "cp1", "points": 3},
            {"id": "cp2", "points": 4},
            {"id": "cp3", "points": 3},
        ]
        results = [
            {"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "code a"},
            {"checkpoint_id": "cp2", "pass": True, "verified": True, "evidence_quote": "code b"},
            {"checkpoint_id": "cp3", "pass": True, "verified": True, "evidence_quote": "code c"},
        ]
        scoring = score_criterion_from_checkpoints(cps, results)
        assert scoring["score"] == 10.0
        assert scoring["max"] == 10.0

    def test_unverified_pass_with_evidence_gets_full_points(self):
        """Unverified passes WITH evidence get full points (flagged, not penalized).
        This prevents LLM quoting style from causing score differences."""
        cps = [
            {"id": "cp1", "points": 5},
            {"id": "cp2", "points": 5},
        ]
        results = [
            {"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "x = 1"},
            {"checkpoint_id": "cp2", "pass": True, "verified": False, "evidence_quote": "y = 2"},  # Unverified but has evidence
        ]
        scoring = score_criterion_from_checkpoints(cps, results)
        assert scoring["score"] == 10.0  # Both counted — evidence exists
        assert scoring["max"] == 10.0

    def test_clear_fabrication_gets_zero(self):
        """Pass with empty evidence (clear fabrication) gets 0 points."""
        cps = [
            {"id": "cp1", "points": 5},
            {"id": "cp2", "points": 5},
        ]
        results = [
            {"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "real evidence"},
            {"checkpoint_id": "cp2", "pass": True, "verified": False, "evidence_quote": ""},  # Empty = fabrication
        ]
        scoring = score_criterion_from_checkpoints(cps, results)
        assert scoring["score"] == 5.0  # Only cp1 counted
        assert scoring["max"] == 10.0

    def test_retries_exhausted_no_evidence_gets_zero(self):
        """Pass that failed all retries AND has no evidence gets 0 points."""
        cps = [{"id": "cp1", "points": 10}]
        results = [
            {"checkpoint_id": "cp1", "pass": True, "verified": False,
             "evidence_quote": "", "reasoning": "no retry response received"},
        ]
        scoring = score_criterion_from_checkpoints(cps, results)
        assert scoring["score"] == 0.0

    def test_retries_exhausted_with_evidence_gets_full_points(self):
        """Pass that failed retries BUT has evidence gets full points (flagged for review)."""
        cps = [{"id": "cp1", "points": 10}]
        results = [
            {"checkpoint_id": "cp1", "pass": True, "verified": False,
             "evidence_quote": "def bfs(graph, start):", "reasoning": "Evidence could not be verified after retries — flagged for review"},
        ]
        scoring = score_criterion_from_checkpoints(cps, results)
        assert scoring["score"] == 10.0

    def test_all_fails(self):
        cps = [
            {"id": "cp1", "points": 5},
            {"id": "cp2", "points": 5},
        ]
        results = [
            {"checkpoint_id": "cp1", "pass": False, "verified": True},
            {"checkpoint_id": "cp2", "pass": False, "verified": True},
        ]
        scoring = score_criterion_from_checkpoints(cps, results)
        assert scoring["score"] == 0.0

    def test_missing_results_get_zero(self):
        cps = [
            {"id": "cp1", "points": 5},
            {"id": "cp2", "points": 5},
        ]
        results = [
            {"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "some evidence"},
            # cp2 is missing
        ]
        scoring = score_criterion_from_checkpoints(cps, results)
        assert scoring["score"] == 5.0

    def test_deterministic_across_runs(self):
        """Same inputs must produce same score every time."""
        cps = [{"id": "cp1", "points": 3}, {"id": "cp2", "points": 7}]
        results = [
            {"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "evidence here"},
            {"checkpoint_id": "cp2", "pass": False, "verified": True},
        ]
        scores = [score_criterion_from_checkpoints(cps, results) for _ in range(100)]
        assert all(s["score"] == 3.0 for s in scores)

    def test_identical_submissions_get_identical_scores(self):
        """THE core guarantee: same work = same score."""
        cps = [
            {"id": "cp1", "points": 2},
            {"id": "cp2", "points": 4},
            {"id": "cp3", "points": 4},
        ]
        # Student A and Student B have identical checkpoint results
        results_a = [
            {"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "code A"},
            {"checkpoint_id": "cp2", "pass": True, "verified": True, "evidence_quote": "code B"},
            {"checkpoint_id": "cp3", "pass": False, "verified": True},
        ]
        results_b = [
            {"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "code A"},
            {"checkpoint_id": "cp2", "pass": True, "verified": True, "evidence_quote": "code B"},
            {"checkpoint_id": "cp3", "pass": False, "verified": True},
        ]
        score_a = score_criterion_from_checkpoints(cps, results_a)
        score_b = score_criterion_from_checkpoints(cps, results_b)
        assert score_a["score"] == score_b["score"]
        assert score_a["score"] == 6.0


# ═══════════════════════════════════════════════════════════════
# 5. FLAGGING TESTS
# ═══════════════════════════════════════════════════════════════

class TestComputeCriterionFlags:

    def test_clean_result_no_flags(self):
        """GREEN tier verified passes produce no flags."""
        cps = [{"id": "cp1"}, {"id": "cp2"}]
        results = [
            {"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "code here", "evidence_similarity": 1.0},
            {"checkpoint_id": "cp2", "pass": False, "verified": True},
        ]
        flags = compute_criterion_flags("Test", cps, results)
        assert flags == []

    def test_red_tier_flags_unverified(self):
        """RED tier (empty evidence on pass) flags as 'could not be verified'."""
        cps = [{"id": "cp1"}]
        results = [{"checkpoint_id": "cp1", "pass": True, "verified": False, "evidence_quote": "", "evidence_similarity": 0.0}]
        flags = compute_criterion_flags("Test", cps, results)
        assert any("could not be verified" in f for f in flags)

    def test_orange_tier_flags_low_confidence(self):
        """ORANGE tier produces a low confidence summary."""
        cps = [{"id": "cp1"}]
        results = [{"checkpoint_id": "cp1", "pass": True, "verified": False, "evidence_quote": "text", "evidence_similarity": 0.25}]
        flags = compute_criterion_flags("Test", cps, results)
        assert any("low verification confidence" in f for f in flags)

    def test_yellow_tier_no_flags(self):
        """YELLOW tier (partial match 0.40-0.79) should NOT produce flags."""
        cps = [{"id": "cp1"}]
        results = [{"checkpoint_id": "cp1", "pass": True, "verified": False, "evidence_quote": "some text", "evidence_similarity": 0.6}]
        flags = compute_criterion_flags("Test", cps, results)
        assert flags == []

    def test_missing_evaluation_flagged(self):
        cps = [{"id": "cp1"}, {"id": "cp2"}]
        results = [{"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "code", "evidence_similarity": 1.0}]
        # cp2 missing from results
        flags = compute_criterion_flags("Test", cps, results)
        assert any("not evaluated" in f.lower() for f in flags)

    def test_visual_content_flagged(self):
        cps = [{"id": "cp1", "requires_visual": True}]
        results = [{"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "[VISUAL] diagram", "evidence_similarity": 1.0}]
        flags = compute_criterion_flags("Test", cps, results, has_visual_content=True)
        assert any("visual content" in f.lower() for f in flags)

    def test_visual_flag_only_with_visual_checkpoints(self):
        """Visual flag should NOT trigger when has_visual_content=True but no visual checkpoints."""
        cps = [{"id": "cp1"}]
        results = [{"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "code", "evidence_similarity": 1.0}]
        flags = compute_criterion_flags("Test", cps, results, has_visual_content=True)
        # No requires_visual checkpoint, so no visual flag
        assert not any("visual content" in f.lower() for f in flags)


# ═══════════════════════════════════════════════════════════════
# 6. COLLECT STUDENT TEXT TESTS
# ═══════════════════════════════════════════════════════════════

class TestCollectStudentText:

    def test_dict_files(self):
        files = [
            {"filename": "main.py", "text_content": "def bfs(): pass"},
            {"filename": "utils.py", "text_content": "import queue"},
        ]
        text = collect_student_text(files)
        assert "def bfs(): pass" in text
        assert "import queue" in text
        assert "main.py" in text
        assert "utils.py" in text

    def test_with_vision_notes(self):
        files = [{"filename": "code.py", "text_content": "x = 1"}]
        text = collect_student_text(files, vision_notes="The image shows a diagram")
        assert "x = 1" in text
        assert "The image shows a diagram" in text

    def test_empty_files_excluded(self):
        files = [
            {"filename": "main.py", "text_content": "code here"},
            {"filename": "empty.txt", "text_content": ""},
            {"filename": "blank.py", "text_content": "   "},
        ]
        text = collect_student_text(files)
        assert "main.py" in text
        assert "empty.txt" not in text

    def test_object_files(self):
        """Test with mock file objects that have attributes."""
        f1 = MagicMock()
        f1.filename = "test.py"
        f1.text_content = "print('hello')"
        text = collect_student_text([f1])
        assert "print('hello')" in text


# ═══════════════════════════════════════════════════════════════
# 7. PROMPT BUILDING TESTS
# ═══════════════════════════════════════════════════════════════

class TestBuildCheckpointGradingPrompt:

    def test_prompt_includes_checkpoints(self):
        cps = {
            "BFS Implementation": [
                {"id": "bfs_cp1", "points": 5, "description": "Uses queue", "requires_visual": False},
                {"id": "bfs_cp2", "points": 5, "description": "Visits all nodes", "requires_visual": False},
            ]
        }
        criteria = [{"criterion": "BFS Implementation", "max": 10}]
        files = [{"filename": "code.py", "type": "python", "text_content": "def bfs(): pass"}]

        prompt = build_checkpoint_grading_prompt(
            cps, criteria, "def bfs(): pass", files, "Lab 1", "BFS", 10
        )
        assert "bfs_cp1" in prompt
        assert "bfs_cp2" in prompt
        assert "Uses queue" in prompt
        assert "5 pts" in prompt

    def test_prompt_includes_file_manifest(self):
        files = [{"filename": "main.py", "type": "python", "text_content": "x = 1"}]
        prompt = build_checkpoint_grading_prompt(
            {"C": [{"id": "c1", "points": 1, "description": "test", "requires_visual": False}]},
            [{"criterion": "C", "max": 1}],
            "x = 1", files, "T", "D", 1
        )
        assert "main.py" in prompt

    def test_prompt_includes_vision_notes(self):
        prompt = build_checkpoint_grading_prompt(
            {"C": [{"id": "c1", "points": 1, "description": "test", "requires_visual": False}]},
            [{"criterion": "C", "max": 1}],
            "", [], "T", "D", 1,
            vision_notes="Image shows a BFS tree"
        )
        assert "Image shows a BFS tree" in prompt

    def test_prompt_includes_reference_solution(self):
        prompt = build_checkpoint_grading_prompt(
            {"C": [{"id": "c1", "points": 1, "description": "test", "requires_visual": False}]},
            [{"criterion": "C", "max": 1}],
            "", [], "T", "D", 1,
            reference_solution="def bfs(g): return list(g.keys())"
        )
        assert "REFERENCE SOLUTION" in prompt


# ═══════════════════════════════════════════════════════════════
# 8. RETRY PROMPT TESTS
# ═══════════════════════════════════════════════════════════════

class TestBuildRetryPrompt:

    def test_includes_failed_checkpoint_ids(self):
        cps = [
            {"id": "cp1", "points": 3, "description": "Uses BFS"},
            {"id": "cp3", "points": 4, "description": "Handles edge cases"},
        ]
        prompt = build_retry_prompt_for_checkpoints(cps, "def bfs(): pass")
        assert "cp1" in prompt
        assert "cp3" in prompt
        assert "Uses BFS" in prompt
        assert "NOT FOUND" in prompt

    def test_truncates_long_content(self):
        cps = [{"id": "cp1", "points": 1, "description": "test"}]
        long_content = "x" * 20000
        prompt = build_retry_prompt_for_checkpoints(cps, long_content)
        assert len(prompt) < 15000  # Should be truncated


# ═══════════════════════════════════════════════════════════════
# 9. LETTER GRADE TESTS
# ═══════════════════════════════════════════════════════════════

class TestPercentageToLetter:

    def test_full_range(self):
        assert _percentage_to_letter(100) == "A+"
        assert _percentage_to_letter(97) == "A+"
        assert _percentage_to_letter(95) == "A"
        assert _percentage_to_letter(91) == "A-"
        assert _percentage_to_letter(88) == "B+"
        assert _percentage_to_letter(85) == "B"
        assert _percentage_to_letter(80) == "B-"
        assert _percentage_to_letter(77) == "C+"
        assert _percentage_to_letter(75) == "C"
        assert _percentage_to_letter(70) == "C-"
        assert _percentage_to_letter(67) == "D+"
        assert _percentage_to_letter(63) == "D"
        assert _percentage_to_letter(55) == "F"
        assert _percentage_to_letter(0) == "F"

    def test_boundary_values(self):
        # Exact boundaries
        assert _percentage_to_letter(96.9) == "A"
        assert _percentage_to_letter(93.0) == "A"
        assert _percentage_to_letter(92.9) == "A-"


# ═══════════════════════════════════════════════════════════════
# 10. ERROR RESULT TESTS
# ═══════════════════════════════════════════════════════════════

class TestBuildErrorResult:

    def test_error_result_structure(self):
        criteria = [
            {"criterion": "BFS", "max": 10},
            {"criterion": "DFS", "max": 5},
        ]
        result = _build_error_result(criteria, 15, "LLM call failed")
        assert result["total_score"] == 0
        assert result["max_score"] == 15
        assert result["letter_grade"] == "F"
        assert result["confidence"] == "low"
        assert len(result["rubric_breakdown"]) == 2
        assert result["rubric_breakdown"][0]["criterion"] == "BFS"
        assert result["rubric_breakdown"][0]["flagged"] is True
        assert result["grading_method"] == "checkpoint"


# ═══════════════════════════════════════════════════════════════
# 11. NORMALIZE TEXT TESTS
# ═══════════════════════════════════════════════════════════════

class TestIsClearFabrication:
    """Tests for the fabrication detection logic."""

    def test_empty_evidence_is_fabrication(self):
        assert _is_clear_fabrication({"pass": True, "evidence_quote": ""}) is True
        assert _is_clear_fabrication({"pass": True, "evidence_quote": None}) is True
        assert _is_clear_fabrication({"pass": True}) is True

    def test_real_evidence_is_not_fabrication(self):
        assert _is_clear_fabrication({"pass": True, "evidence_quote": "def bfs(): pass"}) is False

    def test_visual_evidence_is_not_fabrication(self):
        assert _is_clear_fabrication({"pass": True, "evidence_quote": "[VISUAL] Shows BFS diagram"}) is False

    def test_retries_exhausted_no_evidence_is_fabrication(self):
        """No retry response + empty evidence = fabrication."""
        assert _is_clear_fabrication({
            "pass": True,
            "evidence_quote": "",
            "reasoning": "no retry response received"
        }) is True

    def test_retries_exhausted_with_evidence_not_fabrication(self):
        """Retry failed to verify but evidence exists = NOT fabrication."""
        assert _is_clear_fabrication({
            "pass": True,
            "evidence_quote": "def bfs(graph):",
            "reasoning": "Evidence could not be verified after retries — flagged for review"
        }) is False

    def test_unverified_with_evidence_is_not_fabrication(self):
        """Key test: imperfect quoting should NOT be treated as fabrication."""
        assert _is_clear_fabrication({
            "pass": True,
            "verified": False,
            "evidence_quote": "queue = deque([start])",
            "hallucination_detected": True,
        }) is False  # Has evidence text → not fabrication, just imperfect match

    def test_low_similarity_is_fabrication(self):
        """When evidence_similarity < 0.20, it's fabricated (RED tier)."""
        assert _is_clear_fabrication({
            "pass": True,
            "evidence_quote": "completely made up text",
            "evidence_similarity": 0.05,
        }) is True

    def test_medium_similarity_not_fabrication(self):
        """When evidence_similarity >= 0.20, not fabrication even if unverified."""
        assert _is_clear_fabrication({
            "pass": True,
            "evidence_quote": "some partially matching text",
            "evidence_similarity": 0.45,
            "verified": False,
        }) is False


class TestGetEvidenceFlagTier:
    """Test the tiered evidence flag system for frontend display."""

    def test_green_verified(self):
        assert get_evidence_flag_tier({"pass": True, "verified": True, "evidence_quote": "code"}) == "green"

    def test_green_high_similarity(self):
        assert get_evidence_flag_tier({"pass": True, "verified": False, "evidence_quote": "code", "evidence_similarity": 0.85}) == "green"

    def test_yellow_partial_match(self):
        assert get_evidence_flag_tier({"pass": True, "verified": False, "evidence_quote": "code", "evidence_similarity": 0.55}) == "yellow"

    def test_orange_uncertain(self):
        assert get_evidence_flag_tier({"pass": True, "verified": False, "evidence_quote": "code", "evidence_similarity": 0.25}) == "orange"

    def test_red_low_similarity(self):
        assert get_evidence_flag_tier({"pass": True, "verified": False, "evidence_quote": "code", "evidence_similarity": 0.10}) == "red"

    def test_red_empty_evidence(self):
        assert get_evidence_flag_tier({"pass": True, "evidence_quote": ""}) == "red"

    def test_visual(self):
        assert get_evidence_flag_tier({"pass": True, "evidence_quote": "[VISUAL] diagram"}) == "visual"

    def test_none_for_fail(self):
        assert get_evidence_flag_tier({"pass": False}) == "none"

    def test_none_for_missing(self):
        assert get_evidence_flag_tier(None) == "none"


class TestStripToChars:
    """Test the space-stripping function."""

    def test_removes_all_whitespace(self):
        assert _strip_to_chars("int  a = 3\n   def fun():\n         return n") == "inta=3deffun():returnn"

    def test_empty_string(self):
        assert _strip_to_chars("") == ""

    def test_only_whitespace(self):
        assert _strip_to_chars("   \n\t  ") == ""

    def test_preserves_punctuation(self):
        assert _strip_to_chars("x = [1, 2, 3];") == "x=[1,2,3];"


class TestNormalizeText:

    def test_collapses_whitespace(self):
        assert _normalize_text("hello   world") == "hello world"

    def test_strips(self):
        assert _normalize_text("  hello  ") == "hello"

    def test_tabs_and_newlines(self):
        assert _normalize_text("hello\t\tworld\n\nfoo") == "hello world foo"


# ═══════════════════════════════════════════════════════════════
# 12. FULL GRADE_WITH_CHECKPOINTS INTEGRATION TEST
# ═══════════════════════════════════════════════════════════════

class TestGradeWithCheckpoints:
    """Integration test with mocked LLM to verify the full pipeline."""

    @pytest.fixture
    def sample_checkpoints(self):
        return {
            "BFS Implementation": [
                {"id": "bfs_cp1", "points": 3, "description": "Defines BFS function", "verification_keywords": ["def.*bfs|function.*bfs"], "requires_visual": False},
                {"id": "bfs_cp2", "points": 4, "description": "Uses queue data structure", "verification_keywords": ["deque|queue|\\[\\]"], "requires_visual": False},
                {"id": "bfs_cp3", "points": 3, "description": "Tracks visited nodes", "verification_keywords": ["visited|seen"], "requires_visual": False},
            ]
        }

    @pytest.fixture
    def sample_criteria(self):
        return [{"criterion": "BFS Implementation", "max": 10, "description": "Implement BFS"}]

    @pytest.fixture
    def student_files_good(self):
        return [{
            "filename": "bfs.py",
            "type": "python",
            "text_content": "from collections import deque\n\ndef bfs(graph, start):\n    visited = set()\n    queue = deque([start])\n    while queue:\n        node = queue.popleft()\n        if node not in visited:\n            visited.add(node)\n            queue.extend(graph[node] - visited)\n    return visited",
        }]

    @pytest.fixture
    def mock_llm_good_response(self):
        """Mock LLM that returns proper checkpoint evaluations with real evidence."""
        response_data = {
            "checkpoint_evaluations": [
                {
                    "checkpoint_id": "bfs_cp1",
                    "pass": True,
                    "evidence_quote": "def bfs(graph, start):",
                    "source_file": "bfs.py",
                    "reasoning": "Function is clearly defined"
                },
                {
                    "checkpoint_id": "bfs_cp2",
                    "pass": True,
                    "evidence_quote": "queue = deque([start])",
                    "source_file": "bfs.py",
                    "reasoning": "Uses deque as queue"
                },
                {
                    "checkpoint_id": "bfs_cp3",
                    "pass": True,
                    "evidence_quote": "visited = set()",
                    "source_file": "bfs.py",
                    "reasoning": "Tracks visited nodes with a set"
                },
            ],
            "overall_feedback": "Good BFS implementation",
            "strengths": ["Clean code", "Correct algorithm"],
            "weaknesses": [],
            "suggestions_for_improvement": "Add error handling",
            "confidence": "high",
            "confidence_reasoning": "All evidence is clear",
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(response_data)

        mock_meta = {"model": "test-model", "provider": "test"}

        def llm_fn(**kwargs):
            return (mock_response, mock_meta)

        return llm_fn

    @pytest.fixture
    def mock_extract_json(self):
        def extract(raw):
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return extract

    @pytest.mark.asyncio
    async def test_full_pipeline_good_student(
        self, sample_checkpoints, sample_criteria, student_files_good,
        mock_llm_good_response, mock_extract_json
    ):
        """Test full pipeline with a good student — all checkpoints should pass and verify."""
        result = await grade_with_checkpoints(
            checkpoints_by_criterion=sample_checkpoints,
            criteria=sample_criteria,
            student_files=student_files_good,
            title="Lab 1",
            description="Implement BFS",
            max_score=10,
            messages_with_images=[],
            _llm_call_fn=mock_llm_good_response,
            _extract_json_fn=mock_extract_json,
        )

        # Score should be 10/10 — all checkpoints verified
        assert result["total_score"] == 10.0
        assert result["max_score"] == 10
        assert result["percentage"] == 100.0
        assert result["grading_method"] == "checkpoint"
        assert result["confidence"] == "high"
        assert result["verification_rate"] == 1.0

        # Check rubric breakdown
        assert len(result["rubric_breakdown"]) == 1
        bfs_criterion = result["rubric_breakdown"][0]
        assert bfs_criterion["criterion"] == "BFS Implementation"
        assert bfs_criterion["score"] == 10.0
        assert len(bfs_criterion["checkpoints"]) == 3
        assert all(cp["verified"] for cp in bfs_criterion["checkpoints"])

    @pytest.mark.asyncio
    async def test_hallucinated_evidence_retried_then_failed(
        self, sample_checkpoints, sample_criteria, student_files_good,
        mock_extract_json
    ):
        """Test that fabricated evidence triggers retry and ultimately fails safely."""
        call_count = 0

        def llm_with_hallucination(**kwargs):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                # First call: one checkpoint has fabricated evidence
                data = {
                    "checkpoint_evaluations": [
                        {
                            "checkpoint_id": "bfs_cp1",
                            "pass": True,
                            "evidence_quote": "def bfs(graph, start):",
                            "source_file": "bfs.py",
                            "reasoning": "Found it"
                        },
                        {
                            "checkpoint_id": "bfs_cp2",
                            "pass": True,
                            "evidence_quote": "THIS TEXT DOES NOT EXIST IN THE SUBMISSION AT ALL",
                            "source_file": "bfs.py",
                            "reasoning": "Made up"
                        },
                        {
                            "checkpoint_id": "bfs_cp3",
                            "pass": True,
                            "evidence_quote": "visited = set()",
                            "source_file": "bfs.py",
                            "reasoning": "Found it"
                        },
                    ],
                    "overall_feedback": "Good",
                    "strengths": [],
                    "weaknesses": [],
                    "suggestions_for_improvement": "",
                    "confidence": "high",
                    "confidence_reasoning": "",
                }
            else:
                # Retry calls: still hallucinate (simulating persistent hallucination)
                data = {
                    "checkpoint_evaluations": [
                        {
                            "checkpoint_id": "bfs_cp2",
                            "pass": True,
                            "evidence_quote": "STILL FABRICATED EVIDENCE THAT DOESNT EXIST",
                            "source_file": "bfs.py",
                            "reasoning": "Still making it up"
                        },
                    ]
                }

            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = json.dumps(data)
            return (mock_resp, {"model": "test"})

        result = await grade_with_checkpoints(
            checkpoints_by_criterion=sample_checkpoints,
            criteria=sample_criteria,
            student_files=student_files_good,
            title="Lab 1",
            description="Implement BFS",
            max_score=10,
            messages_with_images=[],
            _llm_call_fn=llm_with_hallucination,
            _extract_json_fn=mock_extract_json,
        )

        # bfs_cp2 had fabricated evidence that never matched after retries.
        # With our new logic, the LLM's response is KEPT (pass=True with evidence text),
        # even though evidence couldn't be verified. Points are still awarded because
        # the LLM DID provide evidence text — only empty evidence gets 0.
        # The checkpoint is flagged for human review instead.
        # cp1 (3) + cp2 (4) + cp3 (3) = 10 (all pass, cp2 flagged)
        assert result["total_score"] == 10.0
        assert result["checkpoint_stats"]["hallucinated_and_retried"] >= 1

        # Should have flags for unverified evidence
        assert len(result["flags"]) > 0

    @pytest.mark.asyncio
    async def test_empty_submission(
        self, sample_checkpoints, sample_criteria, mock_extract_json
    ):
        """Test with empty student submission."""
        empty_files = [{"filename": "empty.py", "type": "python", "text_content": ""}]

        def llm_empty(**kwargs):
            data = {
                "checkpoint_evaluations": [
                    {"checkpoint_id": "bfs_cp1", "pass": False, "evidence_quote": "", "source_file": "", "reasoning": "No code found"},
                    {"checkpoint_id": "bfs_cp2", "pass": False, "evidence_quote": "", "source_file": "", "reasoning": "No code found"},
                    {"checkpoint_id": "bfs_cp3", "pass": False, "evidence_quote": "", "source_file": "", "reasoning": "No code found"},
                ],
                "overall_feedback": "Empty submission",
                "strengths": [],
                "weaknesses": ["No code submitted"],
                "suggestions_for_improvement": "Submit your work",
                "confidence": "high",
                "confidence_reasoning": "Clearly empty",
            }
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = json.dumps(data)
            return (mock_resp, {"model": "test"})

        result = await grade_with_checkpoints(
            checkpoints_by_criterion=sample_checkpoints,
            criteria=sample_criteria,
            student_files=empty_files,
            title="Lab 1",
            description="Implement BFS",
            max_score=10,
            messages_with_images=[],
            _llm_call_fn=llm_empty,
            _extract_json_fn=mock_extract_json,
        )

        assert result["total_score"] == 0.0
        assert result["letter_grade"] == "F"

    @pytest.mark.asyncio
    async def test_partial_credit(
        self, sample_checkpoints, sample_criteria, student_files_good,
        mock_extract_json
    ):
        """Test partial credit — some checkpoints pass, some fail."""
        def llm_partial(**kwargs):
            data = {
                "checkpoint_evaluations": [
                    {
                        "checkpoint_id": "bfs_cp1",
                        "pass": True,
                        "evidence_quote": "def bfs(graph, start):",
                        "source_file": "bfs.py",
                        "reasoning": "Found"
                    },
                    {
                        "checkpoint_id": "bfs_cp2",
                        "pass": True,
                        "evidence_quote": "queue = deque([start])",
                        "source_file": "bfs.py",
                        "reasoning": "Found"
                    },
                    {
                        "checkpoint_id": "bfs_cp3",
                        "pass": False,
                        "evidence_quote": "",
                        "source_file": "",
                        "reasoning": "Not found"
                    },
                ],
                "overall_feedback": "Partial implementation",
                "strengths": ["Has BFS structure"],
                "weaknesses": ["Missing visited tracking"],
                "suggestions_for_improvement": "Track visited nodes",
                "confidence": "high",
                "confidence_reasoning": "Clear evidence",
            }
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = json.dumps(data)
            return (mock_resp, {"model": "test"})

        result = await grade_with_checkpoints(
            checkpoints_by_criterion=sample_checkpoints,
            criteria=sample_criteria,
            student_files=student_files_good,
            title="Lab 1",
            description="Implement BFS",
            max_score=10,
            messages_with_images=[],
            _llm_call_fn=llm_partial,
            _extract_json_fn=mock_extract_json,
        )

        # cp1 (3) + cp2 (4) = 7, cp3 (3) fails
        assert result["total_score"] == 7.0
        assert result["percentage"] == 70.0

    @pytest.mark.asyncio
    async def test_missing_llm_fn_raises(
        self, sample_checkpoints, sample_criteria, mock_extract_json
    ):
        """Must raise if LLM function not provided."""
        with pytest.raises(ValueError, match="_llm_call_fn"):
            await grade_with_checkpoints(
                checkpoints_by_criterion=sample_checkpoints,
                criteria=sample_criteria,
                student_files=[],
                title="T", description="D", max_score=10,
                messages_with_images=[],
                _llm_call_fn=None,
                _extract_json_fn=mock_extract_json,
            )

    @pytest.mark.asyncio
    async def test_bad_llm_response_returns_error(
        self, sample_checkpoints, sample_criteria, mock_extract_json
    ):
        """If LLM returns garbage, should return error result."""
        def bad_llm(**kwargs):
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "not json at all {{{invalid"
            return (mock_resp, {"model": "test"})

        def extract_json_that_fails(raw):
            return None  # Simulates parse failure

        result = await grade_with_checkpoints(
            checkpoints_by_criterion=sample_checkpoints,
            criteria=sample_criteria,
            student_files=[{"filename": "f.py", "type": "python", "text_content": "x=1"}],
            title="T", description="D", max_score=10,
            messages_with_images=[],
            _llm_call_fn=bad_llm,
            _extract_json_fn=extract_json_that_fails,
        )

        assert result["total_score"] == 0
        assert "error" in result or result["confidence"] == "low"


# ═══════════════════════════════════════════════════════════════
# 13. CONSISTENCY GUARANTEE TEST
# ═══════════════════════════════════════════════════════════════

class TestConsistencyGuarantee:
    """The most important test: verify that deterministic scoring
    eliminates the core problem of different scores for same work."""

    def test_same_evidence_same_score_always(self):
        """Two students with identical BFS code must ALWAYS get the same score."""
        cps = [
            {"id": "bfs_cp1", "points": 3},
            {"id": "bfs_cp2", "points": 4},
            {"id": "bfs_cp3", "points": 3},
        ]

        # Simulate: LLM evaluated both students identically (same evidence found)
        results_student_a = [
            {"checkpoint_id": "bfs_cp1", "pass": True, "verified": True, "evidence_quote": "queue = [start]"},
            {"checkpoint_id": "bfs_cp2", "pass": True, "verified": True, "evidence_quote": "visited.add(node)"},
            {"checkpoint_id": "bfs_cp3", "pass": False, "verified": True},
        ]
        results_student_b = [
            {"checkpoint_id": "bfs_cp1", "pass": True, "verified": True, "evidence_quote": "queue = [start]"},
            {"checkpoint_id": "bfs_cp2", "pass": True, "verified": True, "evidence_quote": "visited.add(node)"},
            {"checkpoint_id": "bfs_cp3", "pass": False, "verified": True},
        ]

        score_a = score_criterion_from_checkpoints(cps, results_student_a)
        score_b = score_criterion_from_checkpoints(cps, results_student_b)

        assert score_a["score"] == score_b["score"] == 7.0
        assert score_a["max"] == score_b["max"] == 10.0

    def test_different_evidence_different_score(self):
        """Students with different work should get different scores."""
        cps = [
            {"id": "cp1", "points": 5},
            {"id": "cp2", "points": 5},
        ]

        # Student A: passes both
        results_a = [
            {"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "def solve()"},
            {"checkpoint_id": "cp2", "pass": True, "verified": True, "evidence_quote": "return result"},
        ]
        # Student B: passes only one
        results_b = [
            {"checkpoint_id": "cp1", "pass": True, "verified": True, "evidence_quote": "def solve()"},
            {"checkpoint_id": "cp2", "pass": False, "verified": True},
        ]

        score_a = score_criterion_from_checkpoints(cps, results_a)
        score_b = score_criterion_from_checkpoints(cps, results_b)

        assert score_a["score"] == 10.0
        assert score_b["score"] == 5.0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
