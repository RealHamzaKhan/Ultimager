#!/usr/bin/env python3
"""Tests for rubric generation quality gates and fallback logic."""

from app.services.ai_grader_fixed import (
    _build_fallback_rubric,
    _is_generic_criterion_name,
    _repair_or_fallback_rubric,
)


SAMPLE_ASSIGNMENT = """
Lab 8
Problem 1: Campus Navigation System
Draw a graph of the campus with buildings as nodes
Implement BFS and UCS from A to D
Compare BFS vs UCS results

Problem 2: Ride-Sharing Route Optimization
Define the state space and actions
Implement A* with an admissible heuristic
Explain why the heuristic is admissible

Problem 3: 8-Puzzle Solver
Implement A* with Manhattan distance
Compare node expansions with BFS
"""


def test_generic_criterion_name_detection():
    assert _is_generic_criterion_name("Criterion 1")
    assert _is_generic_criterion_name("Problem 2: Campus Navigation System")
    assert not _is_generic_criterion_name("Problem 2: Implement A* search and heuristic analysis")


def test_fallback_rubric_is_assignment_specific_and_balanced():
    criteria = _build_fallback_rubric(SAMPLE_ASSIGNMENT, max_score=10, strictness="balanced")
    assert len(criteria) >= 3
    assert sum(int(c["max"]) for c in criteria) == 10
    assert all(not _is_generic_criterion_name(c["criterion"]) for c in criteria)


def test_repair_or_fallback_fixes_generic_model_output():
    weak_model_criteria = [
        {"criterion": "Criterion 1", "max": 3, "description": ""},
        {"criterion": "Criterion 2", "max": 3, "description": ""},
        {"criterion": "Problem 3", "max": 4, "description": ""},
    ]
    criteria, _issues = _repair_or_fallback_rubric(
        assignment_description=SAMPLE_ASSIGNMENT,
        model_criteria=weak_model_criteria,
        max_score=10,
        strictness="balanced",
    )
    assert len(criteria) >= 3
    assert sum(int(c["max"]) for c in criteria) == 10
    assert all(not _is_generic_criterion_name(c["criterion"]) for c in criteria)
