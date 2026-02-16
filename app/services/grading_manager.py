"""Background task manager for persistent grading operations."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
import threading

from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.models import GradingSession, StudentSubmission, BackgroundTask, GradingProgress
from app.services.ai_grader import grade_student, parse_rubric, _validate_and_fix_rubric
from app.services.code_executor import run_test_cases
from app.config import UPLOAD_DIR

logger = logging.getLogger(__name__)

# Thread-local storage for database sessions
_thread_local = threading.local()


def get_thread_db():
    """Get or create thread-local database session."""
    if not hasattr(_thread_local, 'db'):
        _thread_local.db = SessionLocal()
    return _thread_local.db


class PersistentGradingManager:
    """Manages background grading tasks that persist across server restarts."""
    
    def __init__(self):
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self._shutdown_event = asyncio.Event()
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="grading_worker")
        self._lock = asyncio.Lock()
        
    async def start_session_grading(self, session_id: int) -> str:
        """Start grading a session in the background.
        
        Returns:
            task_id: Unique identifier for this grading task
        """
        task_id = str(uuid.uuid4())
        
        # Run database setup in executor to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            self._executor,
            self._setup_grading_task,
            session_id,
            task_id
        )
        
        # Start grading in background (non-blocking)
        asyncio.create_task(
            self._grade_session_task_wrapper(task_id, session_id)
        )
        
        logger.info(f"Started grading task {task_id} for session {session_id}")
        return task_id
    
    def _setup_grading_task(self, session_id: int, task_id: str):
        """Setup grading task in a separate thread."""
        db = SessionLocal()
        try:
            session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
            if not session:
                raise ValueError(f"Session {session_id} not found")
            
            # Update session
            session.task_id = task_id
            session.is_background_task = True
            session.status = "grading"
            session.started_at = datetime.now(timezone.utc)
            
            # Create task record
            bg_task = BackgroundTask(
                task_id=task_id,
                session_id=session_id,
                status="running",
                total_items=session.total_students,
                started_at=datetime.now(timezone.utc)
            )
            db.add(bg_task)
            db.commit()
            
        finally:
            db.close()
    
    async def _grade_session_task_wrapper(self, task_id: str, session_id: int):
        """Wrapper to run grading in executor."""
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                self._executor,
                self._grade_session_task_sync,
                task_id,
                session_id
            )
        except Exception as e:
            logger.exception(f"Error in grading task {task_id}")
    
    def _grade_session_task_sync(self, task_id: str, session_id: int):
        """Synchronous grading task to run in thread pool."""
        db = SessionLocal()
        
        try:
            session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
            if not session:
                logger.error(f"Session {session_id} not found for task {task_id}")
                return
            
            # Get all pending/error submissions
            submissions = db.query(StudentSubmission).filter(
                StudentSubmission.session_id == session_id,
                StudentSubmission.status.in_(["pending", "error"])
            ).order_by(StudentSubmission.processing_order).all()
            
            total = len(submissions)
            graded = session.graded_count or 0
            failed = session.error_count or 0
            
            # Update task record
            bg_task = db.query(BackgroundTask).filter(BackgroundTask.task_id == task_id).first()
            if bg_task:
                bg_task.total_items = total
                db.commit()
            
            # Parse rubric
            rubric_dict = {}
            if session.rubric:
                try:
                    rubric_criteria = parse_rubric(str(session.rubric))
                    rubric_dict = {item['criterion'].lower().strip(): item['max'] for item in rubric_criteria}
                except Exception as e:
                    logger.warning(f"Could not parse rubric: {e}")
            
            # Parse questions
            questions = []
            if session.questions:
                try:
                    questions = json.loads(str(session.questions))
                except:
                    pass
            
            # Get session directory
            session_dir = UPLOAD_DIR / str(session_id)
            
            for i, sub in enumerate(submissions):
                # Check if shutdown requested
                if self._shutdown_event.is_set():
                    logger.info(f"Task {task_id} received shutdown signal, pausing")
                    session.status = "paused"
                    if bg_task:
                        bg_task.status = "paused"
                    db.commit()
                    return
                
                try:
                    # Update progress
                    session.current_student_index = i
                    session.graded_count = graded
                    session.error_count = failed
                    session.last_updated = datetime.now(timezone.utc)
                    
                    if bg_task:
                        bg_task.completed_items = graded
                        bg_task.error_items = failed
                        bg_task.last_activity = datetime.now(timezone.utc)
                    
                    sub.status = "grading"
                    db.commit()
                    
                    # Log progress
                    self._log_progress_sync(session_id, sub.id, "progress", 
                                     f"Grading {sub.student_identifier} ({i+1}/{total})",
                                     {"current": i+1, "total": total, "student": sub.student_identifier})
                    
                    # Load student files with content
                    # ZIP processor extracts to {session_dir}/_master/{student_id}/
                    student_dir = session_dir / "_master" / sub.student_identifier
                    if not student_dir.exists():
                        # Fallback: try without _master (for backwards compatibility)
                        student_dir = session_dir / sub.student_identifier
                    file_meta = json.loads(sub.files) if sub.files else []
                    student_files = []
                    
                    if student_dir.exists():
                        from app.services.file_parser import parse_file
                        for f_meta in file_meta:
                            rel_path = f_meta.get('relative_path', f_meta.get('filename', ''))
                            file_path = student_dir / rel_path
                            if file_path.exists():
                                try:
                                    parsed = parse_file(file_path)
                                    student_files.append(parsed)
                                except Exception as e:
                                    logger.warning(f"Failed to parse file {file_path}: {e}")
                                    student_files.append(f_meta)
                            else:
                                student_files.append(f_meta)
                    else:
                        student_files = file_meta
                    
                    # Run test cases if provided
                    test_results = None
                    if session.test_cases and session.run_command and student_dir.exists():
                        try:
                            test_results = run_test_cases(
                                str(student_dir),
                                str(session.test_cases),
                                str(session.run_command)
                            )
                            sub.test_results = json.dumps(test_results)
                            sub.tests_passed = test_results.get('passed', 0)
                            sub.tests_total = test_results.get('total', 0)
                            db.commit()
                        except Exception as e:
                            logger.warning(f"Test execution failed for {sub.student_identifier}: {e}")
                    
                    # Grade student - this is the async part, run it in a new event loop
                    try:
                        result = asyncio.run(self._grade_single_student(
                            title=str(session.title),
                            description=str(session.description) if session.description else "",
                            rubric=str(session.rubric) if session.rubric else "",
                            max_score=int(session.max_score) if session.max_score else 100,
                            student_files=student_files,
                            questions=questions,
                            test_results_str=json.dumps(test_results) if test_results else None,
                        ))
                        
                        # Check for errors
                        if result.get("error"):
                            logger.error(f"AI grading error for {sub.student_identifier}: {result.get('error')}")
                            sub.status = "error"
                            sub.error_message = result.get('error')
                            sub.ai_result = json.dumps(result)
                            failed += 1
                        else:
                            # Update with results
                            sub.ai_result = json.dumps(result)
                            sub.ai_score = result.get("total_score")
                            sub.ai_letter_grade = result.get("letter_grade")
                            sub.ai_confidence = result.get("confidence")
                            sub.status = "graded"
                            sub.graded_at = datetime.now(timezone.utc)
                            graded += 1
                    except Exception as e:
                        logger.exception(f"Failed to grade student {sub.student_identifier}")
                        sub.status = "error"
                        sub.error_message = str(e)
                        sub.retry_count = (sub.retry_count or 0) + 1
                        failed += 1
                    
                    sub.retry_count = 0
                    db.commit()
                    
                except Exception as e:
                    logger.exception(f"Failed to grade student {sub.student_identifier}")
                    sub.status = "error"
                    sub.error_message = str(e)
                    sub.retry_count = (sub.retry_count or 0) + 1
                    failed += 1
                    db.commit()
            
            # Mark session complete
            session.status = "completed" if failed == 0 else "completed_with_errors"
            session.graded_count = graded
            session.error_count = failed
            session.completed_at = datetime.now(timezone.utc)
            session.current_student_index = total
            
            if bg_task:
                bg_task.status = "completed"
                bg_task.completed_items = graded
                bg_task.error_items = failed
                bg_task.completed_at = datetime.now(timezone.utc)
            
            self._log_progress_sync(session_id, None, "complete", 
                             f"Grading completed: {graded} graded, {failed} failed",
                             {"graded": graded, "failed": failed, "total": total})
            
            db.commit()
            logger.info(f"Task {task_id} completed: {graded} graded, {failed} failed")
            
        except Exception as e:
            logger.exception(f"Critical error in grading task {task_id}")
            if bg_task:
                bg_task.status = "failed"
                bg_task.error_message = str(e)
            session.status = "failed"
            db.commit()
        finally:
            db.close()
            if task_id in self.active_tasks:
                del self.active_tasks[task_id]
    
    async def _grade_single_student(self, **kwargs):
        """Grade a single student - async wrapper."""
        return await grade_student(**kwargs)
    
    def _log_progress_sync(self, session_id: int, submission_id: Optional[int], 
                     event_type: str, message: str, details: dict):
        """Log grading progress to database (synchronous version)."""
        db = SessionLocal()
        try:
            progress = GradingProgress(
                session_id=session_id,
                submission_id=submission_id,
                event_type=event_type,
                message=message,
                details=details
            )
            db.add(progress)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to log progress: {e}")
            db.rollback()
        finally:
            db.close()
    
    async def resume_interrupted_tasks(self):
        """Resume any tasks that were interrupted by server restart."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._resume_tasks_sync)
    
    def _resume_tasks_sync(self):
        """Synchronous resume function."""
        db = SessionLocal()
        try:
            # Check if tables exist first
            from sqlalchemy import inspect
            inspector = inspect(db.bind)
            
            # Only proceed if grading_sessions table exists
            if not inspector.has_table("grading_sessions"):
                logger.info("Database tables not created yet, skipping resume")
                return
            
            # Find sessions that were in progress
            interrupted_sessions = db.query(GradingSession).filter(
                GradingSession.status.in_(["grading", "paused"])
            ).all()
            
            for session in interrupted_sessions:
                logger.info(f"Found interrupted grading for session {session.id}")
                # Don't auto-resume, just mark as paused
                session.status = "paused"
                db.commit()
                
        except Exception as e:
            logger.warning(f"Could not resume interrupted tasks: {e}")
        finally:
            db.close()
    
    async def pause_task(self, task_id: str) -> bool:
        """Pause a running grading task."""
        if task_id in self.active_tasks:
            self._shutdown_event.set()
            # Wait for task to pause
            await asyncio.sleep(2)
            self._shutdown_event.clear()
            return True
        return False
    
    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a grading task."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._get_task_status_sync, task_id)
    
    def _get_task_status_sync(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Synchronous task status retrieval."""
        db = SessionLocal()
        try:
            bg_task = db.query(BackgroundTask).filter(BackgroundTask.task_id == task_id).first()
            if not bg_task:
                return None
            
            return {
                "task_id": bg_task.task_id,
                "session_id": bg_task.session_id,
                "status": bg_task.status,
                "total_items": bg_task.total_items,
                "completed_items": bg_task.completed_items,
                "error_items": bg_task.error_items,
                "progress_percentage": (bg_task.completed_items / bg_task.total_items * 100) if bg_task.total_items > 0 else 0,
                "started_at": bg_task.started_at.isoformat() if bg_task.started_at else None,
                "completed_at": bg_task.completed_at.isoformat() if bg_task.completed_at else None,
                "last_activity": bg_task.last_activity.isoformat() if bg_task.last_activity else None,
            }
        finally:
            db.close()
    
    async def shutdown(self):
        """Graceful shutdown - pause all active tasks."""
        logger.info("Shutting down grading manager, pausing active tasks...")
        self._shutdown_event.set()
        
        # Wait for all tasks to complete or pause
        if self.active_tasks:
            await asyncio.gather(*self.active_tasks.values(), return_exceptions=True)
        
        self._executor.shutdown(wait=True)
        logger.info("Grading manager shutdown complete")


# Global grading manager instance
grading_manager = PersistentGradingManager()
