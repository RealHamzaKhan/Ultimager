"""
Parallel Grading Engine - Maximizes throughput while respecting rate limits.

Key features:
- Concurrent student processing using worker pool
- Rate limiting with smart queuing
- Automatic retry on rate limit errors
- Progress tracking per worker
- ACMAG support for consistent grading
- Proper database session management per worker
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Callable
import json
import time

from app.config import RATE_LIMIT_RPM, PARALLEL_GRADING_WORKERS
from app.services.ai_grader_fixed import (
    validate_submission_relevance,
    compute_grading_hash,
    evaluate_relevance_gate,
    build_relevance_block_result,
)
from app.services.file_parser_enhanced import process_student_submission
from app.services.agents.orchestrator import grade_student_with_agents, filter_routing_map_by_question
from app.services.checkpoint_grader import (
    collect_student_text,
    filter_submission_files,
    route_files_to_criteria,
)

# NOTE: Do NOT import _broadcast_sse from app.main to avoid circular import
# Use the sse_callback parameter instead

logger = logging.getLogger(__name__)


# H-1 FIX: Use asyncio.Lock (not threading.Lock) so the event loop is never
# blocked while waiting for the rate-limit slot.  A threading.Lock inside an
# async function stalls the *entire* event loop, causing all 8 workers to
# queue behind a single lock acquisition.
#
# The lock is created lazily (on first acquire) because asyncio primitives must
# be created inside a running event loop — module-level creation breaks on
# Python < 3.10 when no loop exists yet at import time.
_shared_rate_limiter_lock: Optional[asyncio.Lock] = None
_shared_request_timestamps: list = []


def _get_rate_limiter_lock() -> asyncio.Lock:
    """Return (or lazily create) the shared asyncio.Lock for rate limiting."""
    global _shared_rate_limiter_lock
    if _shared_rate_limiter_lock is None:
        _shared_rate_limiter_lock = asyncio.Lock()
    return _shared_rate_limiter_lock


@dataclass
class GradingWorker:
    """Single grading worker that processes one student at a time."""
    worker_id: int
    session_id: int

    # Callback for progress updates
    progress_callback: Optional[Callable] = None
    sse_callback: Optional[Callable] = None  # For SSE broadcast

    @classmethod
    def set_rate_limiter(cls, lock, timestamps):
        """Legacy entry point kept for back-compat.  The lock arg is now ignored;
        the module always uses its own asyncio.Lock.  Timestamps list is still
        shared if provided."""
        global _shared_rate_limiter_lock, _shared_request_timestamps
        # Accept an asyncio.Lock from callers that already create one, or create ours
        if isinstance(lock, asyncio.Lock):
            _shared_rate_limiter_lock = lock
        else:
            # Discard threading.Lock — create a proper asyncio one instead
            _shared_rate_limiter_lock = asyncio.Lock()
        if timestamps is not None:
            _shared_request_timestamps = timestamps

    async def acquire_rate_limit(self, rpm: int = RATE_LIMIT_RPM):
        """Acquire rate limit permission, yielding to the event loop while waiting.

        Uses the module-level ``_shared_request_timestamps`` list so that
        the combined request rate across *all* workers stays within rpm.
        The asyncio.Lock ensures mutual exclusion without blocking the loop.
        """
        lock = _get_rate_limiter_lock()

        while True:
            async with lock:
                now = time.monotonic()
                # Remove timestamps older than 1 minute
                _shared_request_timestamps[:] = [
                    t for t in _shared_request_timestamps if now - t < 60
                ]

                if len(_shared_request_timestamps) < rpm:
                    # Under limit — claim a slot and return immediately
                    _shared_request_timestamps.append(now)
                    return

                # At limit — compute how long until the oldest slot expires
                oldest = _shared_request_timestamps[0]
                wait_time = max(0.05, 60.0 - (now - oldest) + 0.05)

            # Wait OUTSIDE the lock so other workers can check their own slots
            await asyncio.sleep(min(wait_time, 2.0))
    
    async def grade_single_student(
        self,
        submission,
        session,
        rubric_text: str,
        max_score: int,
        questions: list,
        checkpoints: dict = None,
    ):
        """Grade a single student submission."""
        from pathlib import Path
        from app.config import UPLOAD_DIR
        from app.database import SessionLocal
        from app.models import StudentSubmission
        
        # Create a new database session for this worker
        db = SessionLocal()
        
        student_id = submission.id
        worker_submission = db.query(StudentSubmission).filter(
            StudentSubmission.id == student_id,
            StudentSubmission.session_id == self.session_id,
        ).first()
        if worker_submission is None:
            db.close()
            return {"status": "error", "student_id": student_id, "error": "Submission not found"}

        student_identifier = worker_submission.student_identifier
        
        try:
            # Get student files
            session_dir = UPLOAD_DIR / str(self.session_id)
            possible_dirs = [
                session_dir / student_identifier,
                session_dir / "_master" / student_identifier,
            ]
            
            student_dir = None
            for d in possible_dirs:
                if d.exists() and d.is_dir():
                    student_dir = d
                    break
            
            if not student_dir:
                raise ValueError(f"No directory found for {student_identifier}")
            
            # Extract files
            extracted_contents, ingestion_report = process_student_submission(
                student_dir, student_identifier, self.session_id
            )
            ingestion_report_dict = ingestion_report.to_dict() if hasattr(ingestion_report, "to_dict") else None

            # ── Remove system / hidden / IDE files ────────────────────────────
            # macOS junk (.DS_Store, ._*, __MACOSX/), Python artifacts (__pycache__,
            # .pyc), IDE configs (.vscode, .idea), VCS dirs (.git) etc. carry no
            # submission content and can confuse the grader.
            before_count = len(extracted_contents)
            extracted_contents = filter_submission_files(extracted_contents)
            after_count = len(extracted_contents)
            if before_count != after_count:
                logger.info(
                    "[FILTER] %s: removed %d system file(s), %d remain",
                    student_identifier, before_count - after_count, after_count,
                )

            # Store ingestion report
            if hasattr(ingestion_report, 'to_dict'):
                worker_submission.ingestion_report = json.dumps(ingestion_report.to_dict())
            else:
                worker_submission.ingestion_report = str(ingestion_report)
            
            # Validate relevance
            relevance = await validate_submission_relevance(
                title=str(session.title),
                description=str(session.description) or "",
                student_files=extracted_contents,
                rubric=rubric_text
            )
            # Safety: ensure relevance is always a dict
            if not isinstance(relevance, dict):
                logger.warning(f"[RELEVANCE] Non-dict relevance for {student_identifier}: {type(relevance)}")
                relevance = {"is_relevant": True, "confidence": "low", "flags": [], "reasoning": "relevance check returned invalid type"}

            # DEBUG: Log relevance result
            logger.info(f"[RELEVANCE] Student {student_identifier}: is_relevant={relevance.get('is_relevant')}, flags={relevance.get('flags')}, reasoning={relevance.get('reasoning', '')[:100]}")
            
            worker_submission.is_relevant = relevance.get("is_relevant", True)
            worker_submission.relevance_flags = json.dumps(relevance.get("flags", []))
            db.commit()
            # Count images so the gate knows about handwritten/visual submissions
            _img_count = sum(
                len(getattr(f, "images", []) or [])
                for f in extracted_contents
                if hasattr(f, "images")
            )
            relevance_gate = evaluate_relevance_gate(relevance, image_count=_img_count)
            if relevance_gate.get("block_grading"):
                logger.warning(
                    "[RELEVANCE] Blocking grading for %s due to %s (confidence=%s, flags=%s)",
                    student_identifier,
                    relevance_gate.get("reason", "irrelevant"),
                    relevance_gate.get("confidence"),
                    relevance_gate.get("flags"),
                )
                grading_hash = compute_grading_hash(extracted_contents, rubric_text, max_score)
                result = build_relevance_block_result(rubric_text, max_score, relevance, relevance_gate)
                result["grading_hash"] = grading_hash
                _blocked_img_total = int(sum(
                    len(getattr(f, "images", []) or [])
                    for f in extracted_contents
                    if hasattr(f, "images")
                ))
                _blocked_text_total = len("".join([
                    str(getattr(f, "text_content", "") or "") for f in extracted_contents
                ]))
                result["images_processed"] = _blocked_img_total
                result["text_chars_processed"] = _blocked_text_total
                # Add transparency so Mapping tab can show what was found
                result["transparency"] = {
                    "text_chars_sent": 0,
                    "images_sent": 0,
                    "images_available_total": _blocked_img_total,
                    "images_selected_total": 0,
                    "files_processed": [
                        {
                            "filename": getattr(f, "filename", "unknown"),
                            "type": getattr(f, "file_type", "unknown"),
                            "text_length": len(getattr(f, "text_content", "") or ""),
                            "image_count": len(getattr(f, "images", []) or []),
                        }
                        for f in extracted_contents
                        if hasattr(f, "filename")
                    ],
                    "images_info": [],
                    "blocked_by_relevance_gate": True,
                    "llm_call": {},
                }

                worker_submission.ai_result = json.dumps(result)
                worker_submission.ai_score = result.get("total_score", 0)
                worker_submission.ai_letter_grade = result.get("letter_grade", "F")
                worker_submission.ai_confidence = result.get("confidence", "medium")
                worker_submission.status = "graded"
                worker_submission.graded_at = datetime.utcnow()
                worker_submission.is_flagged = True
                worker_submission.flag_reason = relevance_gate.get("reason", "Blocked by relevance gate")
                worker_submission.flagged_by = "system"
                worker_submission.flagged_at = datetime.utcnow()
                db.commit()

                graded_count = db.query(StudentSubmission).filter(
                    StudentSubmission.session_id == self.session_id,
                    StudentSubmission.status == "graded",
                ).count()
                failed_count = db.query(StudentSubmission).filter(
                    StudentSubmission.session_id == self.session_id,
                    StudentSubmission.status == "error",
                ).count()

                if self.sse_callback:
                    self.sse_callback(self.session_id, {
                        "type": "student_graded",
                        "worker_id": self.worker_id,
                        "student_id": student_id,
                        "student": student_identifier,
                        "score": result.get("total_score"),
                        "grade": result.get("letter_grade"),
                        "result": result,
                        "relevance": relevance,
                        "ingestion_report": ingestion_report_dict,
                        "graded_count": graded_count,
                        "failed_count": failed_count,
                        "blocked_by_relevance": True,
                    })
                if self.progress_callback:
                    self.progress_callback(student_identifier, result.get("total_score", 0))
                return {"status": "success", "student_id": student_id, "score": result.get("total_score"), "blocked_by_relevance": True}

            if relevance_gate.get("review_required"):
                logger.warning(
                    "[RELEVANCE] Review required for %s (confidence=%s, flags=%s) - continuing grading",
                    student_identifier,
                    relevance_gate.get("confidence"),
                    relevance_gate.get("flags"),
                )
                if self.sse_callback:
                    self.sse_callback(self.session_id, {
                        "type": "student_warning",
                        "worker_id": self.worker_id,
                        "student_id": student_id,
                        "student": student_identifier,
                        "warning": "Submission has relevance warnings; grade should be reviewed.",
                        "flags": relevance_gate.get("flags", []),
                    })
            
            # NOTE: Rate limiting is handled inside ai_grader_fixed.py on each
            # actual LLM call.  Do NOT call acquire_rate_limit() here — doing so
            # causes double rate-limiting and reduces effective RPM far below the
            # configured limit.

            # Grade
            if not checkpoints:
                logger.error(
                    "[WORKER %d] %s: no checkpoints available — ensure generate_checkpoints() ran before grading",
                    self.worker_id, student_identifier,
                )
                raise RuntimeError(f"No checkpoints for session {self.session_id} — cannot grade {student_identifier}")

            if checkpoints:
                # ── Multi-agent grading pipeline ──────────────────────
                # Checkpoints available → use the new agent system:
                # Domain Judge (partial credit) → Verifier → Scorer → Critic

                # ── Normalise checkpoint format ───────────────────────
                # Checkpoints are stored as {"criterion_name": [cp, ...]}
                # We need criteria=[{"criterion": name, "max": N}] and
                # checkpoints_by_criterion={"criterion_name": [cp, ...]}

                # Build checkpoints_by_criterion from the stored dict
                checkpoints_by_criterion: dict = {}
                if isinstance(checkpoints, dict):
                    # Stored format: {"criterion_name": [cp_dicts]}
                    # Filter out any non-list values (e.g. metadata keys)
                    checkpoints_by_criterion = {
                        k: v for k, v in checkpoints.items()
                        if isinstance(v, list)
                    }

                # Parse rubric text to get max points per criterion
                from app.services.ai_grader_fixed import parse_rubric as _parse_rubric
                rubric_criteria = _parse_rubric(rubric_text) if rubric_text else []

                # Build a lookup: criterion_name → max_points
                rubric_max_lookup = {c["criterion"]: c["max"] for c in rubric_criteria}

                # Build criteria list. For each criterion in checkpoints,
                # look up max from rubric; fall back to sum of checkpoint points,
                # then to equal share of max_score.
                criteria = []
                for crit_name, crit_cps in checkpoints_by_criterion.items():
                    if crit_name in rubric_max_lookup:
                        crit_max = float(rubric_max_lookup[crit_name])
                    else:
                        # Fallback: sum checkpoint points (may be 0 if not set)
                        cp_sum = sum(float(cp.get("points", 0)) for cp in crit_cps)
                        if cp_sum > 0:
                            crit_max = cp_sum
                        else:
                            # Last resort: equal share of total max_score
                            n = len(checkpoints_by_criterion) or 1
                            crit_max = round(max_score / n, 2)
                    criteria.append({"criterion": crit_name, "max": crit_max})

                # Fix checkpoints that have points: 0 by distributing criterion max
                fixed_checkpoints_by_criterion: dict = {}
                for crit_name, crit_cps in checkpoints_by_criterion.items():
                    crit_max = next(
                        (c["max"] for c in criteria if c["criterion"] == crit_name), 0.0
                    )
                    cp_sum = sum(float(cp.get("points", 0)) for cp in crit_cps)
                    if cp_sum == 0 and crit_max > 0 and crit_cps:
                        # Distribute evenly (last checkpoint gets remainder)
                        each = crit_max / len(crit_cps)
                        fixed_cps = []
                        total_assigned = 0.0
                        for i, cp in enumerate(crit_cps):
                            cp = dict(cp)
                            if i < len(crit_cps) - 1:
                                cp["points"] = round(each, 2)
                                total_assigned += cp["points"]
                            else:
                                cp["points"] = round(crit_max - total_assigned, 2)
                            fixed_cps.append(cp)
                        fixed_checkpoints_by_criterion[crit_name] = fixed_cps
                    else:
                        fixed_checkpoints_by_criterion[crit_name] = list(crit_cps)
                checkpoints_by_criterion = fixed_checkpoints_by_criterion

                submission_text = collect_student_text(extracted_contents)

                # Rate limiter wrapper for the orchestrator
                async def _rate_limit_fn():
                    await self.acquire_rate_limit()

                # ── File routing: map rubric criteria → relevant files ──────────
                # Instead of grading each criterion with the full (possibly
                # truncated) submission, we ask the LLM which files are relevant
                # for each criterion.  This eliminates input truncation and
                # cross-question contamination in one step.
                #
                # Only run routing when there are multiple files — single-file
                # submissions don't benefit (routing would just return the one
                # file for every criterion anyway).
                file_routing_map: dict | None = None
                _routing_fallback = False
                if len(extracted_contents) > 1:
                    try:
                        file_routing_map, _routing_fallback = await route_files_to_criteria(
                            criteria=criteria,
                            student_files=extracted_contents,
                            rate_limiter=_rate_limit_fn,
                            files_per_batch=5,
                        )
                        # Post-process: remove cross-question file mappings
                        # (e.g. LLM may route Q3.ipynb to Q1b criteria due to
                        # semantic similarity — this enforces Q-number consistency).
                        file_routing_map = filter_routing_map_by_question(file_routing_map)
                        # Sanity check: if routing returned nothing useful, discard
                        total_mapped = sum(len(v) for v in file_routing_map.values())
                        if total_mapped == 0:
                            logger.warning(
                                "[ROUTING] %s: routing returned 0 mappings after Q-filter — falling back to full text",
                                student_identifier,
                            )
                            file_routing_map = None
                    except Exception as route_err:
                        logger.warning(
                            "[ROUTING] %s: routing failed (%s) — grading with full text",
                            student_identifier, route_err,
                        )
                        file_routing_map = None
                        _routing_fallback = True
                else:
                    logger.debug(
                        "[ROUTING] %s: single-file submission — skipping routing",
                        student_identifier,
                    )

                result = await grade_student_with_agents(
                    student_id=worker_submission.id,
                    session_id=self.session_id,
                    checkpoints_by_criterion=checkpoints_by_criterion,
                    criteria=criteria,
                    submission_text=submission_text,
                    student_files=extracted_contents,
                    title=str(session.title),
                    max_score=max_score,
                    rate_limiter=_rate_limit_fn,
                    file_routing_map=file_routing_map,
                )

                # Propagate routing fallback flag to result dict
                if isinstance(result, dict) and _routing_fallback:
                    result["routing_fallback_used"] = True
                    result.setdefault("needs_review", True)
                    flags = result.get("review_flags") or []
                    flags.append(
                        "File routing failed for ≥1 batch — some criteria may have received wrong files. "
                        "Manual review recommended."
                    )
                    result["review_flags"] = flags

            # Safety: ensure result is always a dict
            if not isinstance(result, dict):
                logger.error(f"[GRADER] grade_student returned non-dict for {student_identifier}: {type(result)}")
                result = {
                    "error": f"Unexpected result type: {type(result).__name__}",
                    "total_score": 0,
                    "max_score": max_score,
                    "percentage": 0,
                    "letter_grade": "F",
                    "confidence": "low",
                    "rubric_breakdown": [],
                    "_provider_error": True,
                }

            # Detect provider errors that grade_student handled internally
            # (returns total_score=0 with _provider_error=True instead of raising)
            if result.get("_provider_error"):
                error_msg = result.get("error", "Provider error")
                logger.warning(
                    f"[GRADER] Provider error for {student_identifier}: {error_msg[:200]}"
                )
                worker_submission.status = "error"
                worker_submission.error_message = error_msg[:500]
                worker_submission.retry_count = (worker_submission.retry_count or 0) + 1
                db.commit()

                if self.sse_callback:
                    self.sse_callback(self.session_id, {
                        "type": "student_error",
                        "worker_id": self.worker_id,
                        "student_id": student_id,
                        "student": student_identifier,
                        "error": error_msg[:200],
                        "will_retry": True,
                    })
                return {"status": "error", "student_id": student_id, "error": error_msg}

            # ── Post-grading validation ──
            # Flag submissions where something looks suspicious so teacher can verify
            total_score = float(result.get("total_score", 0))
            max_possible = float(max_score) if max_score else 50
            has_error = bool(result.get("error"))
            breakdown = result.get("rubric_breakdown", [])
            if not isinstance(breakdown, list):
                breakdown = []
            # Safety: ensure each item in breakdown is a dict
            breakdown = [item for item in breakdown if isinstance(item, dict)]
            all_zero = all(float(item.get("score", 0)) == 0 for item in breakdown) if breakdown else True
            all_not_assessed = all(
                "not assessed" in str(item.get("justification", "")).lower()
                for item in breakdown
            ) if breakdown else False
            text_chars = int(result.get("text_chars_processed", 0))
            has_content = text_chars > 100  # Student submitted meaningful content
            transparency = result.get("transparency") or {}
            if not isinstance(transparency, dict):
                transparency = {}
            llm_call = transparency.get("llm_call") or {}
            if not isinstance(llm_call, dict):
                llm_call = {}
            score_verification = transparency.get("score_verification") or {}
            if not isinstance(score_verification, dict):
                score_verification = {}
            vision_preanalysis = transparency.get("vision_preanalysis") or {}
            if not isinstance(vision_preanalysis, dict):
                vision_preanalysis = {}

            flag_reasons = []

            # 1. Grading errors
            if has_error:
                flag_reasons.append(f"Grading error: {str(result.get('error', ''))[:100]}")

            # 2. All criteria scored 0 despite having content
            if all_zero and has_content and not has_error:
                flag_reasons.append(
                    f"All criteria scored 0 despite {text_chars} chars of content — likely grading failure"
                )

            # 3. All criteria "Not assessed by AI"
            if all_not_assessed and has_content:
                flag_reasons.append("All criteria 'Not assessed by AI' — LLM response may not have parsed correctly")

            # 4. Low confidence from LLM
            if str(result.get("confidence", "")).lower() == "low":
                verification_rate = result.get("verification_rate")
                if verification_rate is not None:
                    flag_reasons.append(f"Low grading confidence ({round(verification_rate * 100)}% evidence verification rate)")
                else:
                    flag_reasons.append("Low grading confidence — manual review recommended")

            # 5. Ingestion warnings (nested student folders, cross-contamination, parse failures)
            if ingestion_report_dict:
                ing_warnings = ingestion_report_dict.get("warnings", [])
                ing_errors = ingestion_report_dict.get("errors", [])
                ing_failed = ingestion_report_dict.get("files_failed", [])
                if ing_warnings:
                    for w in ing_warnings[:3]:
                        flag_reasons.append(f"Ingestion warning: {str(w)[:120]}")
                if ing_errors:
                    for e in ing_errors[:2]:
                        flag_reasons.append(f"Ingestion parse error: {str(e)[:120]}")
                if ing_failed:
                    failed_names = [f.get("filename", "?") for f in ing_failed[:3]]
                    flag_reasons.append(f"Failed to parse files during ingestion: {', '.join(failed_names)}")

            # 6. LLM response was repaired (markdown→JSON, truncation repair)
            if llm_call.get("json_repaired"):
                flag_reasons.append(
                    "LLM response had malformed JSON that was auto-repaired — scores may differ from what the AI intended"
                )
            if llm_call.get("markdown_parsed"):
                flag_reasons.append(
                    "LLM returned non-JSON response — scores were extracted from markdown fallback parsing (less reliable)"
                )

            # 7. Score verification made adjustments
            if score_verification.get("adjustments_applied"):
                adjustments = score_verification.get("adjustments_applied")
                if isinstance(adjustments, list):
                    adj_desc = "; ".join(str(a)[:80] for a in adjustments[:3])
                else:
                    adj_desc = str(adjustments)[:200]
                flag_reasons.append(
                    f"Score verification adjusted results after grading: {adj_desc}"
                )

            # 8. Vision pre-analysis had errors
            if vision_preanalysis.get("error"):
                flag_reasons.append(
                    f"Vision/image pre-analysis failed: {str(vision_preanalysis['error'])[:150]} — "
                    "handwritten or image content may not have been graded accurately"
                )

            # 9. Retries / fallback attempts were needed
            fallback_attempts = llm_call.get("fallback_attempts", 0)
            retries = llm_call.get("retries", 0)
            if fallback_attempts and int(fallback_attempts) > 0:
                flag_reasons.append(
                    f"Grading required {fallback_attempts} fallback attempt(s) — "
                    "initial LLM call(s) failed before a successful response was obtained"
                )
            elif retries and int(retries) >= 2:
                flag_reasons.append(f"Required {retries} retries to reach LLM — connection instability")

            # 10. Relevance gate flagged for review (but did not block)
            if relevance_gate.get("review_required"):
                gate_flags = relevance_gate.get("flags", [])
                gate_confidence = relevance_gate.get("confidence", "unknown")
                flag_reasons.append(
                    f"Relevance gate flagged for teacher review (confidence={gate_confidence}, "
                    f"flags={', '.join(str(f) for f in gate_flags[:5])})"
                )

            # 11. Score is suspiciously perfect (100%) or very high with low content
            if total_score >= max_possible and text_chars < 500 and has_content:
                flag_reasons.append(f"Perfect score ({total_score}/{max_possible}) with minimal content ({text_chars} chars)")

            # 12. Regrade showed score changed by more than 20%
            regrade_info = result.get("regrade") or transparency.get("regrade") or {}
            if isinstance(regrade_info, dict):
                original_score = regrade_info.get("original_score")
                new_score = regrade_info.get("new_score")
                if original_score is not None and new_score is not None:
                    try:
                        orig = float(original_score)
                        new = float(new_score)
                        if max_possible > 0:
                            pct_change = abs(new - orig) / max_possible * 100
                            if pct_change > 20:
                                flag_reasons.append(
                                    f"Regrade changed score by {pct_change:.0f}% "
                                    f"(from {orig} to {new} out of {max_possible}) — "
                                    "large discrepancy suggests grading inconsistency"
                                )
                    except (ValueError, TypeError):
                        pass

            if flag_reasons:
                result["_grading_warnings"] = flag_reasons
                logger.warning(
                    f"[GRADER] Flagging {student_identifier}: {'; '.join(flag_reasons)}"
                )

            # Save result
            worker_submission.ai_result = json.dumps(result)
            worker_submission.ai_score = result.get("total_score", 0)
            worker_submission.ai_letter_grade = result.get("letter_grade", "F")
            worker_submission.ai_confidence = result.get("confidence", "medium")
            worker_submission.status = "graded"
            worker_submission.graded_at = datetime.utcnow()
            # Store code test results if available
            _tr = result.get("transparency", {}).get("test_results") or result.get("_test_results")
            if _tr and isinstance(_tr, dict):
                worker_submission.test_results = json.dumps(_tr)
                worker_submission.tests_passed = int(_tr.get("passed", 0))
                worker_submission.tests_total = int(_tr.get("total", 0))
            # Flag for relevance warnings OR grading anomalies
            if flag_reasons and worker_submission.flagged_by != "user":
                worker_submission.is_flagged = True
                worker_submission.flag_reason = "; ".join(flag_reasons)[:500]
                worker_submission.flagged_by = "system"
                worker_submission.flagged_at = datetime.utcnow()
            elif relevance_gate.get("review_required"):
                worker_submission.is_flagged = True
                if worker_submission.flagged_by != "user":
                    worker_submission.flag_reason = "Relevance warnings require manual review"
                    worker_submission.flagged_by = "system"
                    worker_submission.flagged_at = datetime.utcnow()
            elif worker_submission.flagged_by == "system":
                worker_submission.is_flagged = False
                worker_submission.flag_reason = None
                worker_submission.flagged_by = None
                worker_submission.flagged_at = None
            db.commit()

            graded_count = db.query(StudentSubmission).filter(
                StudentSubmission.session_id == self.session_id,
                StudentSubmission.status == "graded",
            ).count()
            failed_count = db.query(StudentSubmission).filter(
                StudentSubmission.session_id == self.session_id,
                StudentSubmission.status == "error",
            ).count()

            # Keep session.graded_count / error_count live so the status endpoint
            # always returns up-to-date counts (frontend invalidates on each SSE event).
            try:
                from app.models import GradingSession as _GS
                _sess = db.query(_GS).filter(_GS.id == self.session_id).first()
                if _sess:
                    _sess.graded_count = graded_count
                    _sess.error_count = failed_count
                    db.commit()
            except Exception:
                pass  # Non-critical; totals will be fixed at run-end regardless

            # Broadcast progress
            if self.sse_callback:
                self.sse_callback(self.session_id, {
                    "type": "student_graded",
                    "worker_id": self.worker_id,
                    "student_id": student_id,
                    "student": student_identifier,
                    "score": result.get("total_score"),
                    "grade": result.get("letter_grade"),
                    "result": result,
                    "relevance": relevance,
                    "ingestion_report": ingestion_report_dict,
                    "graded_count": graded_count,
                    "failed_count": failed_count,
                })
            
            # Call progress callback if provided
            if self.progress_callback:
                self.progress_callback(student_identifier, result.get("total_score", 0))
            
            return {"status": "success", "student_id": student_id, "score": result.get("total_score")}
            
        except Exception as e:
            error_msg = str(e)
            is_rate_limit = "429" in error_msg or "rate" in error_msg.lower() or "too many" in error_msg.lower()
            
            worker_submission.status = "error"
            worker_submission.error_message = error_msg[:500]
            worker_submission.retry_count = (worker_submission.retry_count or 0) + 1
            db.commit()
            
            # Broadcast error
            if self.sse_callback:
                self.sse_callback(self.session_id, {
                    "type": "student_error",
                    "worker_id": self.worker_id,
                    "student_id": student_id,
                    "student": student_identifier,
                    "error": error_msg[:200],
                    "will_retry": is_rate_limit and worker_submission.retry_count < 3
                })
            
            # Return special status for rate limiting so caller can retry
            if is_rate_limit:
                return {"status": "rate_limited", "student_id": student_id, "error": error_msg}
            
            return {"status": "error", "student_id": student_id, "error": error_msg}
        
        finally:
            db.close()


class ParallelGrader:
    """
    Manages parallel grading of multiple students.
    
    Uses a worker pool to process multiple students concurrently,
    while respecting rate limits.
    """
    
    def __init__(
        self,
        session_id: int,
        db,
        max_workers: int = PARALLEL_GRADING_WORKERS,
        rpm: int = RATE_LIMIT_RPM,
        sse_callback: Optional[Callable] = None,  # For SSE broadcast
        stop_check: Optional[Callable] = None,  # Callable returning True when stop requested
    ):
        self.session_id = session_id
        self.db = db
        self.max_workers = max_workers
        self.rpm = rpm
        self.sse_callback = sse_callback
        self.stop_check = stop_check

        # Shared rate limiter state — asyncio.Lock so the event loop is never blocked
        self._rate_lock = asyncio.Lock()
        self._request_timestamps: list[float] = []

        # Set up worker class
        GradingWorker.set_rate_limiter(self._rate_lock, self._request_timestamps)

        # Stats
        self.processed = 0
        self.failed = 0
        self.rate_limited = 0
        self.stopped = False
    
    async def grade_batch_parallel(
        self,
        submissions: list,
        session,
        rubric_text: str,
        max_score: int,
        questions: list,
        progress_callback=None,
        checkpoints: dict = None,
    ) -> dict:
        """
        Grade a batch of submissions in parallel.
        
        Returns: {
            "total": total submissions,
            "success": successfully graded,
            "failed": failed (non-retryable),
            "rate_limited": hit rate limit,
            "results": list of results
        }
        """
        results = []
        pending = list(submissions)  # Submissions to process
        in_progress = {}  # submission_id -> worker task
        
        # Create workers
        workers = [
            GradingWorker(
                worker_id=i, 
                session_id=self.session_id,
                progress_callback=progress_callback,
                sse_callback=self.sse_callback,
            )
            for i in range(self.max_workers)
        ]
        
        # Retry tracking
        retry_queue = {}  # submission_id -> retry_count
        max_retries = 3
        
        # Max batches to prevent infinite loop
        max_iterations = len(submissions) * (max_retries + 1)
        iteration = 0
        
        while (pending or in_progress) and iteration < max_iterations:
            iteration += 1

            # Check stop flag before starting new work
            if self.stop_check and self.stop_check():
                logger.info(f"[PARALLEL_GRADER] Stop requested for session {self.session_id}, cancelling {len(in_progress)} in-progress tasks")
                # Cancel any in-progress tasks
                for sub_id, (sub, task, worker) in in_progress.items():
                    task.cancel()
                self.stopped = True
                break

            # DEBUG: Log iteration status
            logger.info(f"[PARALLEL_GRADER] Iteration {iteration}: {len(pending)} pending, {len(in_progress)} in progress, max_iter={max_iterations}")

            # Start as many workers as we have capacity for
            while len(in_progress) < self.max_workers and pending:
                sub = pending.pop(0)
                worker = workers[len(in_progress) % self.max_workers]
                
                # DEBUG: Log worker assignment
                logger.info(f"[PARALLEL_GRADER] Starting worker {worker.worker_id} for student {sub.student_identifier} (ID: {sub.id})")
                
                # Create async task for this worker
                task = asyncio.create_task(
                    worker.grade_single_student(
                        sub, session, rubric_text, max_score, questions,
                        checkpoints=checkpoints,
                    )
                )
                in_progress[sub.id] = (sub, task, worker)
            
            if not in_progress:
                logger.warning("[PARALLEL_GRADER] in_progress is empty! pending={}, breaking loop".format(len(pending)))
                break
            
            # Create mapping of task to submission info BEFORE waiting
            task_to_sub = {t: (sub_id, sub, worker) for sub_id, (sub, t, worker) in in_progress.items()}
            
            # Wait for at least one to complete.  Poll in 2-second slices so
            # stop requests are acted on within ~2 seconds rather than waiting
            # for the full 300-second timeout or the next task to finish.
            done: set = set()
            still_pending: set = set(task_to_sub.keys())
            _total_wait = 0
            _MAX_WAIT = 300
            try:
                while still_pending and _total_wait < _MAX_WAIT:
                    # Check stop flag before each slice
                    if self.stop_check and self.stop_check():
                        break
                    _slice_done, still_pending = await asyncio.wait(
                        list(still_pending),
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=2,
                    )
                    done |= _slice_done
                    _total_wait += 2
                    if done:
                        break  # at least one task finished — process it
            except Exception as e:
                logger.error(f"[PARALLEL_GRADER] Error in asyncio.wait: {e}")
                break

            # DEBUG: Log wait results
            logger.info(f"[PARALLEL_GRADER] Wait returned: {len(done)} done, {len(still_pending)} pending")

            if not done:
                # Either stop was requested or genuine timeout
                if self.stop_check and self.stop_check():
                    pass  # handled below
                else:
                    logger.warning("[PARALLEL_GRADER] asyncio.wait timed out (%ds) with no completed tasks!", _total_wait)
                    if not hasattr(self, '_timeout_count'):
                        self._timeout_count = 0
                    self._timeout_count += 1
                    if self._timeout_count >= 3:
                        logger.error("[PARALLEL_GRADER] 3 consecutive timeouts — cancelling remaining tasks")
                        for task in still_pending:
                            task.cancel()
                        break
                    continue

            # Reset timeout counter since we got results
            self._timeout_count = 0

            # Check stop flag after wait returns
            if self.stop_check and self.stop_check():
                logger.info(f"[PARALLEL_GRADER] Stop requested after wait for session {self.session_id}")
                for task in still_pending:
                    task.cancel()
                # Still process completed tasks below so their results are recorded
                self.stopped = True

            # Rebuild in_progress from still_pending (empty if stopped)
            in_progress = {}
            if not self.stopped:
                for task in still_pending:
                    sub_id, sub, worker = task_to_sub[task]
                    in_progress[sub_id] = (sub, task, worker)

            # Process completed tasks
            for task in done:
                completed_sub_id, completed_sub, completed_worker = task_to_sub[task]
                
                # DEBUG: Log task completion
                logger.info(f"[PARALLEL_GRADER] Worker {completed_worker.worker_id} completed student {completed_sub.student_identifier} (ID: {completed_sub_id})")
                
                try:
                    result = task.result()
                except Exception as task_exc:
                    logger.exception(f"[PARALLEL_GRADER] Task exception for {completed_sub.student_identifier}")
                    result = {"status": "error", "error": str(task_exc), "student_id": completed_sub_id}

                # Safety: ensure result is always a dict with a status key
                if not isinstance(result, dict):
                    logger.error(f"[PARALLEL_GRADER] Non-dict result for {completed_sub.student_identifier}: {type(result)}")
                    result = {"status": "error", "error": f"Unexpected result type: {type(result).__name__}", "student_id": completed_sub_id}
                if "status" not in result:
                    result["status"] = "error"

                results.append(result)

                # DEBUG: Log result status
                logger.info(f"[PARALLEL_GRADER] Result for {completed_sub.student_identifier}: status={result.get('status')}, reason={result.get('reason', 'N/A')}")

                if result["status"] == "success":
                    self.processed += 1

                elif result["status"] == "rate_limited":
                    self.rate_limited += 1
                    retry_count = retry_queue.get(completed_sub_id, 0)

                    if retry_count < max_retries:
                        # Re-queue with deterministic exponential backoff
                        backoff_secs = 2 ** retry_count  # 1s, 2s, 4s, ...
                        await asyncio.sleep(backoff_secs)
                        pending.append(completed_sub)
                        retry_queue[completed_sub_id] = retry_count + 1
                        logger.info(f"Rate limited, will retry ({retry_count + 1}/{max_retries})")
                    else:
                        self.failed += 1
                        logger.warning(f"Max retries reached for {completed_sub.student_identifier}")

                elif result["status"] == "skipped":
                    self.processed += 1  # Count as processed (skipped)

                else:  # error
                    retry_count = retry_queue.get(completed_sub_id, 0)
                    if retry_count < max_retries:
                        # Exponential backoff: longer for provider errors
                        error_msg = str(result.get("error", ""))
                        is_provider_error = (
                            "provider" in error_msg.lower()
                            or "connection" in error_msg.lower()
                            or "timeout" in error_msg.lower()
                        )
                        backoff = (2 ** retry_count) * (10 if is_provider_error else 2)  # 10/20/40s or 2/4/8s
                        logger.info(
                            f"[PARALLEL_GRADER] Retrying {completed_sub.student_identifier} "
                            f"(attempt {retry_count + 1}/{max_retries}, backoff={backoff:.1f}s, "
                            f"provider_error={is_provider_error})"
                        )
                        await asyncio.sleep(backoff)
                        pending.append(completed_sub)
                        retry_queue[completed_sub_id] = retry_count + 1
                    else:
                        self.failed += 1
                        logger.warning(
                            f"[PARALLEL_GRADER] Max retries ({max_retries}) reached for "
                            f"{completed_sub.student_identifier}, marking as failed"
                        )

            # If stopped, break out of the retry/pending loop
            if self.stopped:
                break

        return {
            "total": len(submissions),
            "processed": self.processed,
            "failed": self.failed,
            "rate_limited": self.rate_limited,
            "results": results,
            "stopped": self.stopped,
        }


# Helper function for synchronous code
def grade_session_parallel_sync(
    session_id: int,
    db,
    max_workers: int = PARALLEL_GRADING_WORKERS,
) -> dict:
    """Synchronous wrapper for parallel grading."""
    from app.models import GradingSession, StudentSubmission
    
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        return {"error": "Session not found"}
    
    submissions = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id,
        StudentSubmission.status.in_(["pending", "error"])
    ).all()
    
    if not submissions:
        return {"total": 0, "processed": 0}
    
    rubric_text = str(session.rubric) or ""
    max_score = int(session.max_score) or 100
    questions = []
    if session.questions:
        try:
            questions = json.loads(session.questions)
        except:
            pass

    # Load checkpoints for checkpoint-based grading
    session_checkpoints = None
    if getattr(session, "checkpoints", None):
        try:
            session_checkpoints = json.loads(session.checkpoints)
            if not isinstance(session_checkpoints, dict):
                session_checkpoints = None
        except (json.JSONDecodeError, TypeError):
            session_checkpoints = None
    
    grader = ParallelGrader(session_id, db, max_workers=max_workers)
    
    # Run async
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            grader.grade_batch_parallel(
                submissions, session, rubric_text, max_score, questions,
                checkpoints=session_checkpoints,
            )
        )
        return result
    finally:
        loop.close()
