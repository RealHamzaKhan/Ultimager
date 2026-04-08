"""Shared data structures for all agents.

Every agent communicates using these structures — no agent
knows about another agent's internals, only the message contract.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CheckpointResult:
    """Result of grading a single checkpoint — produced by Domain Judge,
    verified by Evidence Verifier, consumed by Scorer and Critic."""

    checkpoint_id: str
    criterion: str      # Rubric criterion name — used by Scorer to group by criterion
    points_max: float

    # Graduated score: 0, 25, 50, 75, or 100 (percent of points_max)
    score_percent: int = 0          # 0 | 25 | 50 | 75 | 100
    points_awarded: float = 0.0

    # H-5 fix: the specific observable requirement being checked (set by orchestrator).
    # Distinct from `criterion` (which is the rubric group name).
    # Example: criterion = "Q1a(i) - Count messages per sender"
    #          description = "Correctly iterates over all messages and counts per sender"
    description: str = ""

    # Judge decision
    reasoning: str = ""             # Always stored — shown to teacher
    evidence_quote: str = ""        # Exact text from submission
    source_file: str = ""
    confidence: str = "low"         # high | medium | low

    # Verification
    verified: bool = False
    verification_method: str = ""   # exact | fuzzy | semantic | visual | unverified
    retry_count: int = 0

    # Flags — set by Critic
    flags: list[str] = field(default_factory=list)
    needs_review: bool = False

    # Transparency flags (set by orchestrator)
    judge_truncated: bool = False   # True when submission content was cut to fit 28K token limit

    # Which model made this decision
    model_used: str = ""

    @property
    def passed(self) -> bool:
        return self.score_percent > 0

    @property
    def fully_passed(self) -> bool:
        return self.score_percent == 100


@dataclass
class GradingResult:
    """Final result for one student — produced by Orchestrator."""

    student_id: int
    session_id: int

    # Score
    total_score: float = 0.0
    max_score: float = 0.0
    score_percent: float = 0.0

    # Checkpoints
    checkpoints: list = field(default_factory=list)  # list[CheckpointResult]

    # Rubric breakdown (for frontend compatibility)
    rubric_breakdown: list = field(default_factory=list)

    # Overall assessment
    overall_feedback: str = ""
    strengths: list = field(default_factory=list)
    weaknesses: list = field(default_factory=list)

    # Critic output
    needs_review: bool = False
    review_flags: list = field(default_factory=list)
    confidence: str = "high"        # high | medium | low

    # Grading method label
    grading_method: str = "multi_agent"

    # Checkpoint stats
    checkpoint_stats: dict = field(default_factory=dict)

    # Full agent trace for transparency
    agent_trace: list = field(default_factory=list)

    # Transparency flags
    judge_truncated: bool = False       # True when ≥1 checkpoint had truncated submission content
    routing_fallback_used: bool = False  # True when routing API failed for ≥1 batch

    # Error handling
    error: Optional[str] = None
