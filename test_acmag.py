#!/usr/bin/env python3
"""Tests for ACMAG helper logic."""

from dataclasses import dataclass

from app.services.acmag import (
    ACMAGRuntime,
    build_evidence_trail,
    quadratic_weighted_kappa,
)


@dataclass
class _MockContent:
    filename: str
    text_content: str
    images: list


def test_quadratic_weighted_kappa_sanity():
    a = [0, 2, 4, 6, 8, 10]
    b = [0, 2, 4, 6, 8, 10]
    assert round(quadratic_weighted_kappa(a, b, 10), 6) == 1.0

    c = [10, 8, 6, 4, 2, 0]
    k = quadratic_weighted_kappa(a, c, 10)
    assert k < 0.0


def test_acmag_runtime_initialization_and_sampling():
    submission_ids = list(range(1, 21))
    runtime = ACMAGRuntime(
        session_id=99,
        max_score=10,
        submission_ids=submission_ids,
        submission_identifiers={sid: f"s{sid}" for sid in submission_ids},
        enabled=True,
        calibration_ratio=0.10,
        min_calibration=3,
        max_calibration=12,
        blind_review_ratio=0.30,
        kappa_threshold=0.60,
    )
    assert len(runtime.calibration_ids) >= 3
    assert len(runtime.calibration_ids) <= 12
    assert runtime.secondary_quota >= 0


def test_build_evidence_trail_has_entries_per_criterion():
    files = [
        _MockContent(
            filename="q1.pdf",
            text_content="Implemented BFS and UCS from A to D.\nCompared path cost and node expansions.",
            images=[{"page": 1, "description": "Page 1 focus region 1 showing graph diagram"}],
        ),
    ]
    result = {
        "rubric_breakdown": [
            {"criterion": "Problem 1: Implement BFS and UCS", "score": 2, "max": 3},
            {"criterion": "Code Quality", "score": 1, "max": 1},
        ],
        "transparency": {
            "vision_preanalysis": {
                "notes_preview": "Image shows handwritten graph and BFS queue states."
            }
        },
    }
    trail = build_evidence_trail(files, result)
    assert len(trail) == 2
    assert all("criterion" in item and "evidence" in item for item in trail)
