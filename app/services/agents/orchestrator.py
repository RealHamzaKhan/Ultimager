"""Grading Orchestrator — coordinates all agents.

Pipeline per student:
  1. For each checkpoint → Domain Judge (LLM, partial credit)
  2. Evidence Verifier   (deterministic, retries Judge if needed)
  3. Scorer             (deterministic Python)
  4. Critic             (deterministic, flags issues)

The Orchestrator is the only entry point external code should call.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional, Callable, Any

from app.config import MAX_IMAGES_FOR_FINAL_GRADE
from app.services.agents.base import CheckpointResult, GradingResult
from app.services.agents.domain_judge import judge_checkpoint
from app.services.agents.verifier import run_verifier, verify_evidence
from app.services.agents.scorer import run_scorer
from app.services.agents.critic import run_critic

logger = logging.getLogger(__name__)

# Max retries when evidence cannot be verified
MAX_VERIFY_RETRIES = 2


# ── Question-scoped content helpers ──────────────────────────────

def _q_number_from_filename(filename: str) -> Optional[str]:
    """Extract question number from a filename.

    Examples:
      'Q2.ipynb'          → '2'
      'Q3.ipynb'          → '3'
      'Q1 THEORY.txt'     → '1'
      'solution.py'       → None  (no question prefix)
      'task2.py'          → None  (task-prefixed handled separately by heuristic)
    """
    m = re.match(r'^[Qq](\d+)', filename.strip())
    return m.group(1) if m else None


def filter_routing_map_by_question(
    file_routing_map: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Remove cross-question file mappings from a routing result.

    The LLM router uses semantic matching which can cause false positives:
    e.g. 'Q1b(iii) - Return count of matches' might map to Q3.ipynb because
    Q3 also contains a count operation.  This post-processing step enforces a
    hard rule: if a criterion is labelled Q<N>, files named Q<M>.* (M ≠ N)
    are removed from its routing list.

    Only applies when BOTH the criterion AND the filename carry an explicit
    Q-number.  Generic filenames (solution.py, main.py) and generic criteria
    ('Code Quality') are left unchanged.

    Returns a new dict — does not mutate the input.
    """
    result: dict[str, list[str]] = {}
    for criterion, files in file_routing_map.items():
        crit_q = _extract_question_number(criterion)
        if crit_q is None:
            # Criterion has no Q-number — keep all files as-is
            result[criterion] = list(files)
            continue

        filtered: list[str] = []
        removed: list[str] = []
        for fn in files:
            file_q = _q_number_from_filename(fn)
            if file_q is not None and file_q != crit_q:
                removed.append(fn)
            else:
                filtered.append(fn)

        if removed:
            logger.debug(
                "Q-number filter: criterion '%s' (Q%s) — removed cross-question files %s",
                criterion, crit_q, removed,
            )
        result[criterion] = filtered

    return result


def _extract_question_number(criterion: str) -> Optional[str]:
    """Extract question number from criterion name.

    Examples:
      'Q1a(i) - Count messages per sender' → '1'
      'Q2d - Implement BFS'                → '2'
      'Q3c(iii) - Extract approved orders' → '3'
      'Code Quality'                       → None  (no question prefix)
    """
    m = re.match(r'^Q(\d+)', criterion.strip(), re.IGNORECASE)
    return m.group(1) if m else None


def _filter_for_question(
    q_num: str,
    student_files: list,
    full_text: str,
) -> str:
    """Return submission text filtered to only the files for question q_num.

    Matching is intentionally broad to handle common naming patterns:
      Q1 → q1.py, Q1_solution.py, question1.py, question 1 theory.txt, task1.ipynb …
      Q2 → q2.py, task2.ipynb, Question 2 theory part.txt …

    Falls back to full_text when no match is found (student put everything
    in a single file, or used a non-standard naming convention).
    """
    patterns = [
        f"q{q_num}",          # q1, q1.py, q1_solution.py
        f"question{q_num}",   # question1.py
        f"question {q_num}",  # question 1 theory.txt
        f"question-{q_num}",  # question-1.txt
        f"task{q_num}",       # task1.py, task2.ipynb
        f"task {q_num}",      # task 2.ipynb
        f"task-{q_num}",      # task-2.ipynb
        f"part{q_num}",       # part1.py
        f"part {q_num}",      # part 1 answer.txt
    ]

    parts = []
    for f in student_files:
        if hasattr(f, "filename"):
            fn = getattr(f, "filename", "") or ""
            text = getattr(f, "text_content", "") or ""
        elif isinstance(f, dict):
            fn = f.get("filename", "") or ""
            text = f.get("text_content", "") or ""
        else:
            continue

        # Flow-audit gap #6 fix: ensure fn and text are always strings
        if not isinstance(fn, str):
            fn = str(fn) if fn else ""
        if not isinstance(text, str):
            text = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else str(text) if text else ""

        if not text.strip():
            continue
        fn_lower = fn.lower()
        if any(p in fn_lower for p in patterns):
            parts.append(f"--- FILE: {fn} ---\n{text}")

    if parts:
        logger.debug(
            "Question-scoped grading: Q%s → %d file(s) matched (total full_text len=%d)",
            q_num, len(parts), len(full_text),
        )
        return "\n\n".join(parts)

    # No files matched the question number — fall back to full submission text.
    # This covers single-file submissions where all answers are in one file.
    logger.debug(
        "Question-scoped grading: Q%s → no matching files; using full submission text",
        q_num,
    )
    return full_text


def _collect_images_for_question(q_num: str, student_files: list) -> list[dict]:
    """Return images from files whose name matches the given question number.

    Used by the Q-number heuristic path so image content is scoped to the
    same question as the text content (prevents cross-question image bleed).
    """
    patterns = [f"q{q_num}", f"question{q_num}", f"question {q_num}", f"task{q_num}"]
    images: list[dict] = []
    for f in student_files:
        fn = (getattr(f, "filename", "") if hasattr(f, "filename") else f.get("filename", "")) or ""
        fn_lower = fn.lower()
        if not any(p in fn_lower for p in patterns):
            continue
        file_imgs = list(getattr(f, "images", None) or (f.get("images") if isinstance(f, dict) else []) or [])
        images.extend(img for img in file_imgs if img.get("base64"))
    return images[:MAX_IMAGES_FOR_FINAL_GRADE]


def _collect_all_images(student_files: list) -> list[dict]:
    """Return images from all student files.

    Used when no scoping is possible (single-file submission or no Q-number).
    """
    images: list[dict] = []
    for f in student_files:
        file_imgs = list(getattr(f, "images", None) or (f.get("images") if isinstance(f, dict) else []) or [])
        images.extend(img for img in file_imgs if img.get("base64"))
    return images[:MAX_IMAGES_FOR_FINAL_GRADE]


def _flag_unverified_nonzero(cp: "CheckpointResult") -> None:
    """Flag a checkpoint that awarded marks but evidence could not be verified.

    Called when benefit-of-the-doubt is applied (both original and retry are
    unverified). Does nothing for verified checkpoints or zero-score checkpoints.
    """
    if not cp.verified and cp.points_awarded > 0:
        cp.needs_review = True
        flag = (
            f"Unverified: {cp.points_awarded}/{cp.points_max} pts awarded "
            "but evidence quote not found in submission text — AI judgment only"
        )
        if flag not in (cp.flags or []):
            cp.flags = list(cp.flags or []) + [flag]


class GradingOrchestrator:
    """Runs the full multi-agent grading pipeline for one student."""

    def __init__(self, rate_limiter: Optional[Callable] = None):
        """
        Args:
            rate_limiter: async callable that enforces API rate limits.
                          Called before each LLM request.
        """
        self._rate_limiter = rate_limiter

    async def grade_student(
        self,
        student_id: int,
        session_id: int,
        checkpoints_by_criterion: dict[str, list[dict]],
        criteria: list[dict],
        submission_text: str,
        student_files: list,
        title: str,
        max_score: float,
        file_routing_map: Optional[dict[str, list[str]]] = None,
    ) -> GradingResult:
        """Grade one student through the full pipeline.

        Args:
            student_id: DB id of the student submission
            session_id: DB id of the grading session
            checkpoints_by_criterion: {"criterion name": [checkpoint dicts]}
            criteria: [{"criterion": "...", "max": N, "description": "..."}]
            submission_text: full text content of all student files
            student_files: list of file dicts (for metadata)
            title: assignment title
            max_score: total possible score
            file_routing_map: optional dict mapping criterion_name → [relevant filenames].
                When provided, each criterion is graded using ONLY its relevant files
                (no Q-number heuristic, no truncation of unrelated content).
                When absent, falls back to Q-number scoping then full submission text.

        Returns:
            GradingResult with full transparency data
        """
        start_time = time.time()
        all_results: list[CheckpointResult] = []

        # Guard against empty submissions — but image-only submissions (scanned PDFs,
        # photos, engineering drawings) have empty text_content yet ARE valid.
        # Check both text and images before declaring a submission empty.
        has_text = bool(
            submission_text
            and submission_text.strip() not in ("", "[EMPTY SUBMISSION — no text content found in any submitted file]")
        )
        has_images = any(
            bool(getattr(f, "images", None) or (isinstance(f, dict) and f.get("images")))
            for f in student_files
        )
        submission_is_empty = not has_text and not has_images
        if submission_is_empty:
            logger.warning(
                "grade_student called with empty submission for student_id=%d — "
                "awarding 0 marks and flagging for review",
                student_id,
            )
            empty_result = run_scorer(
                checkpoints=[],
                criteria=criteria,
                student_id=student_id,
                session_id=session_id,
                max_score=max_score,
            )
            empty_result.needs_review = True
            empty_result.review_flags = ["Empty submission — no content found; awarded 0 marks"]
            empty_result.confidence = "high"  # We are very confident it's empty
            empty_result.agent_trace.append({
                "agent": "orchestrator",
                "note": "skipped grading — empty submission",
                "elapsed_seconds": 0,
            })
            return empty_result

        # Grade each checkpoint — sequentially to respect rate limits
        for crit in criteria:
            crit_name = crit.get("criterion", "")
            crit_checkpoints = checkpoints_by_criterion.get(crit_name, [])

            if not crit_checkpoints:
                # M-1 fix: WARNING so a criterion silently scoring 0 is never invisible
                logger.warning(
                    "No checkpoints for criterion '%s' (max %.1f marks) — "
                    "this criterion will score 0 for every student. "
                    "Regenerate checkpoints or check the rubric.",
                    crit_name, float(crit.get("max", 0)),
                )
                continue

            # ── Per-criterion scoped content ────────────────────────────────────
            # Priority 1: LLM file routing map (most accurate — uses semantic
            # understanding of file content vs criterion meaning).
            #   • Router returned filenames  → use only those files' content.
            #   • Router returned []         → student has NO file for this criterion;
            #                                  award 0 immediately, do NOT fall back to
            #                                  full text (that causes cross-contamination).
            # Priority 2: Q-number heuristic (keyword-based fallback for
            # assignments with standard naming like q1.py, task2.ipynb).
            # Priority 3: Full submission text (single-file submissions or
            # when no scoping method has any matching files).
            scoped_text: str
            scoped_images: list[dict] = []
            router_said_no_file = False  # True when routing definitively found no file

            if file_routing_map is not None and crit_name in file_routing_map:
                relevant_fns = set(file_routing_map.get(crit_name, []))
                if relevant_fns:
                    # Build text AND collect images from only the routed files
                    routed_text_parts: list[str] = []
                    routed_images: list[dict] = []
                    for f in student_files:
                        if hasattr(f, "filename"):
                            fn = getattr(f, "filename", "") or ""
                            text = getattr(f, "text_content", "") or ""
                            file_imgs = list(getattr(f, "images", None) or [])
                        elif isinstance(f, dict):
                            fn = f.get("filename", "") or ""
                            text = f.get("text_content", "") or ""
                            file_imgs = list(f.get("images") or [])
                        else:
                            continue
                        if not isinstance(text, str):
                            text = text.decode("utf-8", errors="replace") if isinstance(text, bytes) else str(text) if text else ""
                        if fn not in relevant_fns:
                            continue
                        if text.strip():
                            routed_text_parts.append(f"--- FILE: {fn} ---\n{text}")
                        routed_images.extend(img for img in file_imgs if img.get("base64"))

                    scoped_text = "\n\n".join(routed_text_parts)
                    scoped_images = routed_images[:MAX_IMAGES_FOR_FINAL_GRADE]

                    if scoped_text or scoped_images:
                        logger.debug(
                            "Routing-scoped grading: criterion '%s' → %d text file(s), %d image(s)",
                            crit_name, len(routed_text_parts), len(scoped_images),
                        )
                    else:
                        # Router named files but all were empty/missing (no text, no images)
                        scoped_text = ""
                        scoped_images = []
                        router_said_no_file = True
                        logger.debug(
                            "Routing-scoped grading: criterion '%s' → routed files all empty/missing → scoring 0",
                            crit_name,
                        )
                else:
                    # Routing returned [] — the LLM router examined every batch of
                    # submitted files and found none that address this criterion.
                    # This is the primary fix for cross-file contamination: when a
                    # student submits Q2.ipynb + Q3.ipynb but no Q1 file, the router
                    # correctly returns [] for all Q1 criteria.  We must NOT fall back
                    # to full submission text here — that is exactly what caused the
                    # bug where Q3 content was used to award Q1 marks.
                    scoped_text = ""
                    router_said_no_file = True
                    logger.debug(
                        "Routing returned [] for criterion '%s' — no matching file found; scoring 0",
                        crit_name,
                    )
            else:
                # No routing map — use Q-number heuristic
                # When grading Q1 criteria, only send Q1 files to the judge so it
                # cannot accidentally quote Q2/Q3 code as evidence for Q1 (and vice
                # versa).  Falls back to full submission text if no per-question files
                # are found (single-file submissions, non-standard naming, etc.).
                q_num = _extract_question_number(crit_name)
                if q_num:
                    scoped_text = _filter_for_question(q_num, student_files, submission_text)
                    scoped_images = _collect_images_for_question(q_num, student_files)
                else:
                    scoped_text = submission_text
                    scoped_images = _collect_all_images(student_files)

            # ── Short-circuit: no file found → award 0 without calling the LLM ──
            # Sending empty content to the judge risks hallucination (the model may
            # invent evidence it cannot actually see).  Instead, emit zero-scored
            # CheckpointResults directly so the scorer/critic can process them
            # normally and the teacher sees a clear "no file submitted" note.
            if router_said_no_file:
                for cp in crit_checkpoints:
                    zero_cp = CheckpointResult(
                        checkpoint_id=cp.get("id", ""),
                        criterion=crit_name,
                        description=cp.get("description", ""),
                        points_max=float(cp.get("points", 0)),
                        score_percent=0,
                        points_awarded=0.0,
                        evidence_quote="",
                        source_file="",
                        reasoning=(
                            "No file submitted for this question. "
                            "The file router found no matching file for this criterion "
                            "across all submitted files."
                        ),
                        confidence="high",
                        verified=True,
                        verification_method="router_no_file",
                        needs_review=False,
                        flags=[],
                        model_used="none",
                        retry_count=0,
                    )
                    all_results.append(zero_cp)
                continue  # next criterion

            for cp in crit_checkpoints:
                cp_result = await self._grade_one_checkpoint(
                    cp=cp,
                    criterion=crit_name,
                    submission_text=scoped_text,
                    title=title,
                    submission_images=scoped_images or None,
                )
                all_results.append(cp_result)

        # Score (deterministic)
        result = run_scorer(
            checkpoints=all_results,
            criteria=criteria,
            student_id=student_id,
            session_id=session_id,
            max_score=max_score,
        )

        # Critic (deterministic flags)
        result = run_critic(result, submission_text)

        # Aggregate judge_truncated flag from all checkpoints
        any_truncated = any(getattr(cp, "judge_truncated", False) for cp in all_results)
        result.judge_truncated = any_truncated
        if any_truncated:
            result.needs_review = True
            result.review_flags.append(
                "Submission content was truncated to fit AI context window (28,000 chars). "
                "Some submitted work may not have been evaluated. Manual review of large submissions recommended."
            )

        elapsed = round(time.time() - start_time, 1)
        result.agent_trace.append({
            "agent": "orchestrator",
            "checkpoints_graded": len(all_results),
            "elapsed_seconds": elapsed,
        })

        return result

    async def _grade_one_checkpoint(
        self,
        cp: dict,
        criterion: str,
        submission_text: str,
        title: str,
        submission_images: list[dict] | None = None,
    ) -> CheckpointResult:
        """Run Judge → Verify → retry loop for one checkpoint.

        IMPORTANT: `criterion` must be the RUBRIC CRITERION NAME (e.g. "Q1a(i) - Count
        messages per sender"), NOT the checkpoint description.  The scorer groups
        CheckpointResults by criterion name to compute criterion scores, so this
        must match the keys used in the criteria list passed to run_scorer().
        """
        cp_id = cp.get("id", criterion)
        points_max = float(cp.get("points", 1))  # already fixed to non-zero by parallel_grader
        description = cp.get("description", criterion)
        pass_desc = cp.get("pass_description", "")
        fail_desc = cp.get("fail_description", "")

        # Build pass/fail descriptions if not present
        if not pass_desc:
            pass_desc = f"Student clearly demonstrates: {description}"
        if not fail_desc:
            fail_desc = f"Student does not address: {description}"

        # The judge prompt uses `criterion` as the label shown to the model.
        # We put the specific checkpoint description there so the judge knows exactly
        # what to look for.  BUT we tag the returned CheckpointResult with the RUBRIC
        # CRITERION NAME so the scorer can group checkpoints by criterion correctly.
        result = await judge_checkpoint(
            checkpoint_id=cp_id,
            criterion=description,          # judge prompt label = checkpoint description
            points_max=points_max,
            pass_description=pass_desc,
            fail_description=fail_desc,
            submission_content=submission_text,
            title=title,
            rate_limiter=self._rate_limiter,
            submission_images=submission_images,
        )

        # H-5 fix: store the specific checkpoint description so the frontend can show
        # what was actually being checked (not just the rubric group name).
        result.description = description

        # Override the criterion field to be the RUBRIC criterion name so the
        # scorer can group checkpoints by criterion correctly.
        result.criterion = criterion

        # Verify evidence
        result = run_verifier(result, submission_text)

        # If evidence not verified AND marks awarded AND similarity very low → retry
        if (
            not result.verified
            and result.score_percent > 0
            and "evidence_likely_hallucinated" in result.flags
            and result.retry_count < MAX_VERIFY_RETRIES
        ):
            logger.debug(
                "Retrying Judge for checkpoint %s (retry %d)", cp_id, result.retry_count + 1
            )
            # Retry with explicit hint to quote more carefully
            retry_result = await judge_checkpoint(
                checkpoint_id=cp_id,
                criterion=f"{description}\n\nIMPORTANT: Your previous evidence quote could not be found in the submission. Please re-read and quote EXACT text.",
                points_max=points_max,
                pass_description=pass_desc,
                fail_description=fail_desc,
                submission_content=submission_text,
                title=title,
                rate_limiter=self._rate_limiter,
                submission_images=submission_images,
            )
            retry_result.retry_count = result.retry_count + 1
            retry_result.description = description   # keep checkpoint description on retry
            retry_result.criterion = criterion       # keep rubric criterion name on retry too
            retry_result = run_verifier(retry_result, submission_text)

            # C-2 fix: only switch to retry when it is actually better for the student.
            # "Better" means: retry got verified evidence, OR retry is verified AND scored higher.
            # Never penalise a student just because a retry happened to be more conservative —
            # if both are unverified keep whichever awarded more marks.
            if retry_result.verified:
                # Retry found verifiable evidence → always use it (most trustworthy)
                result = retry_result
            elif not result.verified:
                # Both unverified — keep the original (benefit of the doubt; don't penalise
                # the student for the judge's second-guess on an already-awarded checkpoint)
                # But flag it so the teacher sees the uncertainty.
                _flag_unverified_nonzero(result)
            # else: original was verified, retry wasn't → keep original (already the case)

        # Flag any unverified nonzero score even when no retry happened
        _flag_unverified_nonzero(result)
        return result


# ── Convenience wrapper ───────────────────────────────────────────

async def grade_student_with_agents(
    student_id: int,
    session_id: int,
    checkpoints_by_criterion: dict,
    criteria: list[dict],
    submission_text: str,
    student_files: list,
    title: str,
    max_score: float,
    rate_limiter: Optional[Callable] = None,
    file_routing_map: Optional[dict[str, list[str]]] = None,
) -> dict:
    """Top-level convenience function — returns a plain dict compatible
    with the existing ai_result JSON format stored in the database."""

    orchestrator = GradingOrchestrator(rate_limiter=rate_limiter)

    result = await orchestrator.grade_student(
        student_id=student_id,
        session_id=session_id,
        checkpoints_by_criterion=checkpoints_by_criterion,
        criteria=criteria,
        submission_text=submission_text,
        student_files=student_files,
        title=title,
        max_score=max_score,
        file_routing_map=file_routing_map,
    )

    return _result_to_ai_result_dict(result)


def _result_to_ai_result_dict(result: GradingResult) -> dict:
    """Convert GradingResult → the dict format expected by the DB/frontend."""
    # Build flags list for frontend
    all_flags = []
    for cp in result.checkpoints:
        for flag in cp.flags:
            if flag:
                all_flags.append(f"[{cp.criterion}] {flag}")
    all_flags.extend(result.review_flags)

    feedback = result.overall_feedback or _generate_feedback(result)
    pct = result.score_percent

    return {
        "total_score": result.total_score,
        "max_score": result.max_score,
        "percentage": pct,
        "letter_grade": _percentage_to_letter(pct),
        "overall_feedback": feedback,
        "strengths": result.strengths,
        "weaknesses": result.weaknesses,
        "rubric_breakdown": result.rubric_breakdown,
        "confidence": result.confidence,
        "needs_review": result.needs_review,
        "flags": all_flags,
        "grading_method": "multi_agent",
        "checkpoint_stats": result.checkpoint_stats,
        "agent_trace": result.agent_trace,
        "judge_truncated": result.judge_truncated,
        "routing_fallback_used": result.routing_fallback_used,
    }


def _percentage_to_letter(pct: float) -> str:
    if pct >= 97: return "A+"
    if pct >= 93: return "A"
    if pct >= 90: return "A-"
    if pct >= 87: return "B+"
    if pct >= 83: return "B"
    if pct >= 80: return "B-"
    if pct >= 77: return "C+"
    if pct >= 73: return "C"
    if pct >= 70: return "C-"
    if pct >= 67: return "D+"
    if pct >= 60: return "D"
    return "F"


def _generate_feedback(result: GradingResult) -> str:
    """Generate overall feedback when none was provided."""
    pct = result.score_percent
    if pct >= 90:
        quality = "Excellent work"
    elif pct >= 75:
        quality = "Good work"
    elif pct >= 60:
        quality = "Satisfactory work"
    elif pct >= 40:
        quality = "Work shows some understanding but needs improvement"
    else:
        quality = "Work needs significant improvement"

    flagged = len(result.review_flags)
    flag_note = f" ({flagged} item(s) flagged for review)" if flagged else ""

    return f"{quality}. Score: {result.total_score}/{result.max_score} ({pct}%){flag_note}."
