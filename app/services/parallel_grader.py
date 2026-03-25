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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Callable
import random
import threading
import json

from app.config import RATE_LIMIT_RPM, ACMAG_ENABLED, PARALLEL_GRADING_WORKERS
from app.services.ai_grader_fixed import (
    grade_student,
    validate_submission_relevance,
    compute_grading_hash,
    evaluate_relevance_gate,
    build_relevance_block_result,
)
from app.services.file_parser_enhanced import process_student_submission

# NOTE: Do NOT import _broadcast_sse from app.main to avoid circular import
# Use the sse_callback parameter instead

logger = logging.getLogger(__name__)


@dataclass
class GradingWorker:
    """Single grading worker that processes one student at a time."""
    worker_id: int
    session_id: int
    
    # Rate limiting - shared across workers
    _rate_limiter_lock = None
    _request_timestamps: list = field(default_factory=list)
    
    # Callback for progress updates
    progress_callback: Optional[Callable] = None
    sse_callback: Optional[Callable] = None  # For SSE broadcast
    
    @classmethod
    def set_rate_limiter(cls, lock, timestamps):
        cls._rate_limiter_lock = lock
        cls._request_timestamps = timestamps
    
    async def acquire_rate_limit(self, rpm: int = RATE_LIMIT_RPM):
        """Acquire rate limit permission, waiting if necessary."""
        import time
        
        while True:
            with self._rate_limiter_lock:
                now = time.time()
                # Remove timestamps older than 1 minute
                self._request_timestamps[:] = [t for t in self._request_timestamps if now - t < 60]
                
                if len(self._request_timestamps) < rpm:
                    # Under limit, proceed
                    self._request_timestamps.append(now)
                    return
                
                # At limit, calculate wait time
                oldest = self._request_timestamps[0]
                wait_time = 60 - (now - oldest) + 0.1
            
            # Wait outside lock
            await asyncio.sleep(min(wait_time, 2))  # Cap at 2 seconds
    
    async def grade_single_student(
        self,
        submission,
        session,
        rubric_text: str,
        max_score: int,
        questions: list,
        use_acmag: bool = False,
        acmag_runtime=None,
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
            
            # Acquire rate limit before API call
            await self.acquire_rate_limit()
            
            # Grade
            if use_acmag and acmag_runtime:
                from app.services.acmag import grade_submission_acmag
                
                run_secondary = acmag_runtime.should_run_secondary(
                    worker_submission.id, str(student_identifier)
                )
                anchor_context = (
                    acmag_runtime.anchor_context_text() 
                    if acmag_runtime.calibration_complete 
                    else ""
                )
                try:
                    acmag_pack = await asyncio.wait_for(
                        grade_submission_acmag(
                            title=str(session.title),
                            description=str(session.description) or "",
                            rubric=rubric_text,
                            max_score=max_score,
                            student_files=extracted_contents,
                            questions=questions,
                            student_identifier=str(student_identifier),
                            anchor_context=anchor_context,
                            run_secondary=run_secondary,
                            moderation_delta=acmag_runtime.moderation_delta,
                        ),
                        timeout=45,
                    )

                    if not isinstance(acmag_pack, dict):
                        logger.error(f"ACMAG returned non-dict for {student_identifier}: {type(acmag_pack)}")
                        acmag_pack = {"result": {}}
                    result = dict(acmag_pack.get("result") or {})

                    # Update ACMAG runtime state
                    if not result.get("error"):
                        acmag_runtime.register_anchor(
                            worker_submission.id, str(student_identifier), result
                        )
                        secondary_result = acmag_pack.get("secondary_result")
                        if (
                            acmag_pack.get("secondary_executed")
                            and isinstance(secondary_result, dict)
                            and not secondary_result.get("error")
                        ):
                            acmag_runtime.record_secondary_pair(
                                primary=acmag_pack.get("primary_result") or result,
                                secondary=secondary_result,
                                from_calibration=acmag_runtime.is_calibration_submission(worker_submission.id),
                            )
                        result.setdefault("acmag", {})
                        result["acmag"]["runtime"] = acmag_runtime.reliability_snapshot()
                except asyncio.TimeoutError:
                    logger.warning(
                        f"ACMAG timed out for {student_identifier}; falling back to standard grading"
                    )
                    result = await grade_student(
                        title=str(session.title),
                        description=str(session.description) or "",
                        rubric=rubric_text,
                        max_score=max_score,
                        student_files=extracted_contents,
                        questions=questions,
                        reference_solution=getattr(session, "reference_solution", None) or None,
                        test_cases=getattr(session, "test_cases", None) or None,
                        run_command=getattr(session, "run_command", None) or None,
                        student_dir=str(student_dir) if student_dir else None,
                    )
            else:
                result = await grade_student(
                    title=str(session.title),
                    description=str(session.description) or "",
                    rubric=rubric_text,
                    max_score=max_score,
                    student_files=extracted_contents,
                    questions=questions,
                    reference_solution=getattr(session, "reference_solution", None) or None,
                    test_cases=getattr(session, "test_cases", None) or None,
                    run_command=getattr(session, "run_command", None) or None,
                    student_dir=str(student_dir) if student_dir else None,
                )
            
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
                flag_reasons.append(f"Low confidence: {result.get('confidence_reasoning', '')[:100]}")

            # 5. Ingestion warnings (nested student folders, cross-contamination, parse failures)
            if ingestion_report_dict:
                ing_warnings = ingestion_report_dict.get("warnings", [])
                ing_errors = ingestion_report_dict.get("errors", [])
                ing_failed = ingestion_report_dict.get("files_failed", [])
                if ing_warnings:
                    for w in ing_warnings[:3]:
                        flag_reasons.append(f"File issue: {str(w)[:120]}")
                if ing_errors:
                    for e in ing_errors[:2]:
                        flag_reasons.append(f"Parse error: {str(e)[:120]}")
                if ing_failed:
                    failed_names = [f.get("filename", "?") for f in ing_failed[:3]]
                    flag_reasons.append(f"Failed to parse: {', '.join(failed_names)}")

            # 6. LLM response was repaired (markdown→JSON, truncation repair)
            if llm_call.get("markdown_parsed"):
                flag_reasons.append("LLM returned non-JSON response — parsed from markdown (scores may be inaccurate)")
            if llm_call.get("json_repaired"):
                flag_reasons.append("LLM response had malformed JSON — repaired automatically")

            # 7. Score is suspiciously perfect (100%) or very high with low content
            if total_score >= max_possible and text_chars < 500 and has_content:
                flag_reasons.append(f"Perfect score ({total_score}/{max_possible}) with minimal content ({text_chars} chars)")

            # 8. Retries were needed (connection instability)
            retries = llm_call.get("retries", 0)
            if retries and retries >= 2:
                flag_reasons.append(f"Required {retries} retries to reach LLM — connection instability")

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

        # Shared rate limiter state
        self._rate_lock = threading.Lock()
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
        use_acmag: bool = False,
        acmag_runtime=None,
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
        
        while pending and iteration < max_iterations:
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
                        use_acmag=use_acmag, acmag_runtime=acmag_runtime,
                    )
                )
                in_progress[sub.id] = (sub, task, worker)
            
            if not in_progress:
                logger.warning("[PARALLEL_GRADER] in_progress is empty! pending={}, breaking loop".format(len(pending)))
                break
            
            # Create mapping of task to submission info BEFORE waiting
            task_to_sub = {t: (sub_id, sub, worker) for sub_id, (sub, t, worker) in in_progress.items()}
            
            # Wait for at least one to complete (with timeout)
            try:
                done, still_pending = await asyncio.wait(
                    list(task_to_sub.keys()),
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=30  # 30 second timeout
                )
            except Exception as e:
                logger.error(f"[PARALLEL_GRADER] Error in asyncio.wait: {e}")
                break
            
            # DEBUG: Log wait results
            logger.info(f"[PARALLEL_GRADER] Wait returned: {len(done)} done, {len(still_pending)} pending")
            
            if not done:
                logger.warning("[PARALLEL_GRADER] asyncio.wait timed out with no completed tasks!")
                # Cancel pending tasks and break
                for task in still_pending:
                    task.cancel()
                break
            
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
                        # Re-queue with backoff
                        await asyncio.sleep(random.uniform(1, 3))  # Random backoff
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
                        backoff = random.uniform(8, 15) if is_provider_error else random.uniform(1, 3)
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
    use_acmag: bool = False,
    acmag_runtime=None,
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
    
    grader = ParallelGrader(session_id, db, max_workers=max_workers)
    
    # Run async
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            grader.grade_batch_parallel(
                submissions, session, rubric_text, max_score, questions,
                use_acmag=use_acmag, acmag_runtime=acmag_runtime,
            )
        )
        return result
    finally:
        loop.close()
