#!/usr/bin/env python3
"""Tests for generalized image sampling/selection logic."""
from collections import Counter

from app.services.ai_grader_fixed import (
    _pick_diverse_images_for_grading,
    _select_images_for_preanalysis,
)


def _make_image(file_idx: int, page_idx: int, kind: str) -> dict:
    is_focus = kind == "focus"
    is_full = kind == "full"
    return {
        "filename": f"file_{file_idx}.pdf",
        "page": page_idx,
        "description": f"Page {page_idx} {kind}",
        "size_bytes": 1000 + file_idx * 10 + page_idx,
        "media_type": "image/png",
        "base64": f"img-{file_idx}-{page_idx}-{kind}",
        "is_focus": is_focus,
        "is_full": is_full,
        "content_score": float((file_idx * 10) + page_idx + (1 if is_focus else 0)),
    }


def _make_synthetic_images(files: int, pages_per_file: int) -> list[dict]:
    out: list[dict] = []
    for file_idx in range(1, files + 1):
        for page_idx in range(1, pages_per_file + 1):
            out.append(_make_image(file_idx, page_idx, "focus"))
            out.append(_make_image(file_idx, page_idx, "full"))
    return out


def test_preanalysis_selection_balances_large_batches():
    images = _make_synthetic_images(files=6, pages_per_file=5)  # 60 images
    selected = _select_images_for_preanalysis(images, max_images=50)

    assert len(selected) == 50
    counts = Counter(img["filename"] for img in selected)
    assert len(counts) == 6  # all files covered
    # With 50 slots over 6 files and round-robin complement fill, distribution is near-even.
    assert set(counts.values()).issubset({8, 9})


def test_preanalysis_unlimited_returns_all():
    images = _make_synthetic_images(files=4, pages_per_file=3)  # 24 images
    selected = _select_images_for_preanalysis(images, max_images=0)
    assert len(selected) == len(images)


def test_final_selection_keeps_file_coverage_when_possible():
    images = _make_synthetic_images(files=3, pages_per_file=2)  # 12 images
    selected = _pick_diverse_images_for_grading(images, max_images=5)

    assert len(selected) == 5
    counts = Counter(img["filename"] for img in selected)
    assert len(counts) == 3  # at least one image from each file
