"""AI Grading System - Clean, Fixed Version"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import statistics
import threading
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue, Empty
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Depends, File, Form, UploadFile, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import UPLOAD_DIR, BASE_DIR
from app.database import get_db, init_db, SessionLocal
from app.models import GradingSession, StudentSubmission, GradingProgress
from app.schemas import OverridePayload
from app.services.zip_processor import extract_master_archive_with_verification
from app.services.exporter import export_csv, export_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler("server.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Global storage for simple background task tracking (non-persistent for this version)
_active_grading: Dict[int, Dict[str, Any]] = {}
# SSE event queues: session_id -> list of Queue objects (one per connected client)
_sse_queues: Dict[int, List[Queue]] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AI Grading System v4.0...")
    init_db()
    UPLOAD_DIR.mkdir(exist_ok=True)
    logger.info("System ready")
    yield
    logger.info("System shutdown")

app = FastAPI(title="AI Grading System", version="4.0.0", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key="change-this-in-production")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

def _json_filter(value):
    if not value:
        return None
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return None

templates.env.filters["from_json"] = _json_filter

# ── Helper: serialize submissions for templates ──────────────────
def _serialize_submission(sub: StudentSubmission) -> dict:
    """Convert ORM submission to a JSON-serializable dict for templates."""
    ai_result = None
    if sub.ai_result:
        try:
            ai_result = json.loads(sub.ai_result)
        except (json.JSONDecodeError, TypeError):
            ai_result = None

    files = []
    if sub.files:
        try:
            files = json.loads(sub.files)
        except (json.JSONDecodeError, TypeError):
            files = []

    return {
        "id": sub.id,
        "session_id": sub.session_id,
        "student_identifier": sub.student_identifier,
        "status": sub.status,
        "file_count": sub.file_count or len(files),
        "ai_score": sub.ai_score,
        "ai_letter_grade": sub.ai_letter_grade or "",
        "ai_confidence": sub.ai_confidence or "",
        "final_score": sub.override_score if (sub.is_overridden and sub.override_score is not None) else sub.ai_score,
        "is_overridden": sub.is_overridden or False,
        "override_score": sub.override_score,
        "override_comments": sub.override_comments or "",
        "is_reviewed": sub.is_reviewed or False,
        "tests_passed": sub.tests_passed or 0,
        "tests_total": sub.tests_total or 0,
        "graded_at": sub.graded_at.isoformat() if sub.graded_at else None,
        "error_message": sub.error_message or "",
        "files": files,
        "ai_result": ai_result,
        "ai_feedback": ai_result.get("overall_feedback", "") if ai_result else "",
        "rubric_breakdown": ai_result.get("rubric_breakdown", []) if ai_result else [],
        "question_mapping": ai_result.get("question_mapping", []) if ai_result else [],
        "strengths": ai_result.get("strengths", []) if ai_result else [],
        "weaknesses": ai_result.get("weaknesses", []) if ai_result else [],
        "critical_errors": ai_result.get("critical_errors", []) if ai_result else [],
        "suggestions_for_improvement": ai_result.get("suggestions_for_improvement", "") if ai_result else "",
    }


def _compute_grade_distribution(submissions) -> dict:
    """Compute grade distribution from submissions list."""
    grades = []
    for sub in submissions:
        grade = sub.ai_letter_grade if hasattr(sub, 'ai_letter_grade') else sub.get('ai_letter_grade')
        if grade:
            grades.append(grade)
    return dict(Counter(grades))


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    sessions = db.query(GradingSession).order_by(GradingSession.created_at.desc()).all()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "sessions": sessions
    })

@app.get("/session/new", response_class=HTMLResponse)
def new_session_form(request: Request):
    return templates.TemplateResponse("new_session.html", {"request": request})

@app.post("/session/new")
async def create_session(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    rubric: str = Form(""),
    max_score: int = Form(100),
    run_command: Optional[str] = Form(None),
    test_cases: Optional[str] = Form(None),
    questions: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    session = GradingSession(
        title=title,
        description=description or "",
        rubric=rubric or "",
        max_score=max_score,
        run_command=run_command,
        test_cases=test_cases,
        questions=questions,
        status="pending",
        total_students=0,
        graded_count=0
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return RedirectResponse(url=f"/session/{session.id}", status_code=303)

@app.get("/session/{session_id}", response_class=HTMLResponse)
def session_detail(session_id: int, request: Request, db: Session = Depends(get_db)):
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    submissions = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id
    ).all()
    
    # Serialize submissions for JSON use in Alpine.js
    serialized = [_serialize_submission(sub) for sub in submissions]
    
    return templates.TemplateResponse("session.html", {
        "request": request,
        "session": session,
        "submissions": submissions,
        "submissions_json": json.dumps(serialized, default=str)
    })

@app.post("/session/{session_id}/upload")
async def upload_zip(
    session_id: int,
    zip_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    tmp_path = UPLOAD_DIR / f"master_{session_id}_{zip_file.filename}"
    with open(tmp_path, "wb") as f:
        content = await zip_file.read()
        f.write(content)
    
    try:
        report = extract_master_archive_with_verification(tmp_path, session_id)
        
        if not report['success']:
            raise HTTPException(status_code=400, detail=f"Extraction failed: {report.get('errors', [])}")
        
        for student_data in report['students']:
            file_meta = []
            for f in student_data["files"]:
                file_meta.append({
                    "filename": f.get("filename"),
                    "type": f.get("type"),
                    "size": f.get("size", 0),
                    "relative_path": f.get("relative_path"),
                })
            
            sub = StudentSubmission(
                session_id=session_id,
                student_identifier=student_data["student_identifier"],
                files=json.dumps(file_meta),
                file_count=len(file_meta),
                status="pending"
            )
            db.add(sub)
        
        session.total_students = len(report['students'])
        db.commit()
        
    finally:
        tmp_path.unlink(missing_ok=True)
    
    return RedirectResponse(url=f"/session/{session_id}", status_code=303)

@app.post("/session/{session_id}/grade")
async def start_grading(session_id: int, db: Session = Depends(get_db)):
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.status == "grading":
        return JSONResponse({"message": "Already grading", "session_id": session_id})
    
    session.status = "grading"
    session.graded_count = 0
    db.commit()
    
    # Initialize progress tracking
    _active_grading[session_id] = {
        "status": "grading",
        "current_student": "",
        "graded_count": 0,
        "failed_count": 0,
        "total": session.total_students,
        "stage": "Initializing...",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # Start background grading in a thread pool so DB I/O doesn't block the event loop
    asyncio.get_event_loop().run_in_executor(None, _grade_all_students_sync, session_id)
    
    return JSONResponse({
        "message": "Grading started",
        "session_id": session_id
    })

def _broadcast_sse(session_id: int, event: dict):
    """Push an SSE event to all connected clients for a session."""
    queues = _sse_queues.get(session_id, [])
    for q in queues:
        try:
            q.put_nowait(event)
        except Exception:
            pass


def _grade_all_students_sync(session_id: int):
    """Background grading task — runs in a thread pool, NOT in the event loop."""
    import asyncio as _asyncio
    db = SessionLocal()
    try:
        from app.services.ai_grader import grade_student
        from app.services.file_parser import parse_file

        session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
        if not session:
            return

        submissions = db.query(StudentSubmission).filter(
            StudentSubmission.session_id == session_id,
            StudentSubmission.status.in_(["pending", "error"])
        ).all()

        total = len(submissions)
        graded = 0
        failed = 0

        questions = []
        if session.questions:
            try:
                questions = json.loads(session.questions)
            except Exception:
                pass

        session_dir = UPLOAD_DIR / str(session_id)

        for i, sub in enumerate(submissions):
            try:
                sub.status = "grading"
                db.commit()

                # Update progress tracking
                if session_id in _active_grading:
                    _active_grading[session_id].update({
                        "current_student": sub.student_identifier,
                        "graded_count": graded,
                        "failed_count": failed,
                        "stage": f"Grading {i+1}/{total}",
                    })

                # Broadcast progress event
                _broadcast_sse(session_id, {
                    "type": "progress",
                    "student": sub.student_identifier,
                    "graded_count": graded,
                    "failed_count": failed,
                    "total": total,
                    "stage": f"Grading {i+1}/{total}",
                })

                # Load files
                file_meta = json.loads(sub.files) if sub.files else []
                student_files = []

                student_dir = session_dir / "_master" / sub.student_identifier
                if not student_dir.exists():
                    student_dir = session_dir / sub.student_identifier
                if student_dir.exists():
                    for f_meta in file_meta:
                        rel_path = f_meta.get('relative_path', f_meta.get('filename', ''))
                        file_path = student_dir / rel_path
                        if file_path.exists():
                            try:
                                parsed = parse_file(file_path)
                                student_files.append(parsed)
                            except Exception:
                                student_files.append(f_meta)
                        else:
                            student_files.append(f_meta)
                else:
                    student_files = file_meta

                # Grade — run the async function from this sync thread
                loop = _asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(grade_student(
                        title=str(session.title),
                        description=str(session.description) if session.description else "",
                        rubric=str(session.rubric) if session.rubric else "",
                        max_score=int(session.max_score) if session.max_score else 100,
                        student_files=student_files,
                        questions=questions
                    ))
                finally:
                    loop.close()

                if result.get("error"):
                    sub.status = "error"
                    sub.error_message = result.get('error')
                    sub.ai_result = json.dumps(result, default=str)
                    failed += 1
                else:
                    sub.ai_result = json.dumps(result, default=str)
                    sub.ai_score = result.get("total_score")
                    sub.ai_letter_grade = result.get("letter_grade")
                    sub.ai_confidence = result.get("confidence")
                    sub.status = "graded"
                    sub.graded_at = datetime.now(timezone.utc)
                    graded += 1

                session.graded_count = graded
                session.error_count = failed
                db.commit()

                # Broadcast student_complete event with full result
                _broadcast_sse(session_id, {
                    "type": "student_complete",
                    "student_id": sub.id,
                    "student_identifier": sub.student_identifier,
                    "score": sub.ai_score,
                    "grade": sub.ai_letter_grade or "",
                    "confidence": sub.ai_confidence or "",
                    "graded_count": graded,
                    "failed_count": failed,
                    "total": total,
                    "result": result,
                })

            except Exception as e:
                logger.exception(f"Failed to grade {sub.student_identifier}")
                sub.status = "error"
                sub.error_message = str(e)
                failed += 1
                db.commit()

                # Broadcast error for this student
                _broadcast_sse(session_id, {
                    "type": "student_error",
                    "student_id": sub.id,
                    "student_identifier": sub.student_identifier,
                    "error": str(e),
                    "graded_count": graded,
                    "failed_count": failed,
                    "total": total,
                })

        session.status = "completed" if failed == 0 else "completed_with_errors"
        session.graded_count = graded
        session.error_count = failed
        db.commit()

        # Update progress tracking
        if session_id in _active_grading:
            _active_grading[session_id].update({
                "status": "completed",
                "current_student": "",
                "graded_count": graded,
                "failed_count": failed,
                "stage": "Complete",
            })

        # Broadcast completion
        _broadcast_sse(session_id, {
            "type": "complete",
            "graded_count": graded,
            "failed_count": failed,
            "total": total,
            "message": f"Grading finished: {graded} graded, {failed} failed",
        })

    except Exception as e:
        logger.exception(f"Critical error grading session {session_id}")
        try:
            session.status = "failed"
            db.commit()
        except Exception:
            pass
        if session_id in _active_grading:
            _active_grading[session_id]["status"] = "failed"
        _broadcast_sse(session_id, {
            "type": "complete",
            "graded_count": 0,
            "failed_count": 0,
            "total": 0,
            "message": f"Grading failed: {e}",
        })
    finally:
        db.close()
        # Clean up queues after a delay
        def _cleanup():
            import time
            time.sleep(5)
            _active_grading.pop(session_id, None)
            _sse_queues.pop(session_id, None)
        threading.Thread(target=_cleanup, daemon=True).start()


# ── SSE / Polling for grading progress ───────────────────────────

@app.get("/session/{session_id}/grade-stream")
async def grade_stream(session_id: int):
    """Server-Sent Events endpoint for real-time grading progress.
    Uses a per-client queue so events are pushed instantly from the grading thread."""

    # Create a queue for this client
    client_queue: Queue = Queue()
    if session_id not in _sse_queues:
        _sse_queues[session_id] = []
    _sse_queues[session_id].append(client_queue)

    async def event_generator():
        idle_count = 0
        try:
            while True:
                # Try to get an event from the queue (non-blocking)
                try:
                    event = client_queue.get_nowait()
                    idle_count = 0
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                    if event.get("type") == "complete":
                        return
                except Empty:
                    pass

                # If no event, check if grading is still active
                progress = _active_grading.get(session_id)
                if progress is None:
                    db = SessionLocal()
                    try:
                        session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
                        if session and session.status in ("completed", "completed_with_errors", "failed"):
                            data = {
                                "type": "complete",
                                "graded_count": session.graded_count or 0,
                                "total": session.total_students or 0,
                                "message": f"Grading finished: {session.graded_count} graded",
                            }
                            yield f"data: {json.dumps(data)}\n\n"
                            return
                    finally:
                        db.close()
                    idle_count += 1
                    if idle_count > 120:
                        return

                await asyncio.sleep(0.3)
        finally:
            # Remove this client's queue on disconnect
            try:
                _sse_queues.get(session_id, []).remove(client_queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/session/{session_id}/status")
def get_session_status(session_id: int, db: Session = Depends(get_db)):
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Merge with live progress if available
    progress = _active_grading.get(session_id, {})
    
    return {
        "session_id": session.id,
        "status": progress.get("status", session.status),
        "total_students": session.total_students,
        "graded_count": progress.get("graded_count", session.graded_count),
        "failed_count": progress.get("failed_count", session.error_count or 0),
        "current_student": progress.get("current_student", ""),
        "progress_percentage": (session.graded_count / session.total_students * 100) if session.total_students > 0 else 0
    }

@app.get("/session/{session_id}/results", response_class=HTMLResponse)
def results_dashboard(session_id: int, request: Request, db: Session = Depends(get_db)):
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    submissions = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id
    ).all()
    
    scores = []
    for sub in submissions:
        score = sub.override_score if (sub.is_overridden and sub.override_score is not None) else sub.ai_score
        if score is not None:
            scores.append(score)
    
    stats = None
    if scores:
        stats = {
            "count": len(scores),
            "average": round(statistics.mean(scores), 2),
            "median": round(statistics.median(scores), 2),
            "stdev": round(statistics.stdev(scores), 2) if len(scores) > 1 else 0,
            "min": round(min(scores), 2),
            "max": round(max(scores), 2),
        }
    
    # Compute grade distribution
    grade_dist = _compute_grade_distribution(submissions)
    
    return templates.TemplateResponse("results.html", {
        "request": request,
        "session": session,
        "submissions": submissions,
        "stats": stats,
        "grade_dist": grade_dist,
    })

@app.get("/session/{session_id}/export/csv")
def export_csv_endpoint(session_id: int, db: Session = Depends(get_db)):
    try:
        csv_content = export_csv(db, session_id)
        return PlainTextResponse(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=session_{session_id}_results.csv"}
        )
    except Exception as e:
        logger.exception("CSV export failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/session/{session_id}/export/json")
def export_json_endpoint(session_id: int, db: Session = Depends(get_db)):
    try:
        json_content = export_json(db, session_id)
        return JSONResponse(
            content=json.loads(json_content),
            headers={"Content-Disposition": f"attachment; filename=session_{session_id}_results.json"}
        )
    except Exception as e:
        logger.exception("JSON export failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/session/{session_id}/student/{student_id}/override")
async def override_grade(
    session_id: int,
    student_id: int,
    payload: OverridePayload,
    db: Session = Depends(get_db),
):
    sub = db.query(StudentSubmission).filter(
        StudentSubmission.id == student_id,
        StudentSubmission.session_id == session_id
    ).first()
    
    if not sub:
        raise HTTPException(status_code=404, detail="Student not found")
    
    sub.override_score = payload.score
    sub.override_comments = payload.comments
    sub.is_overridden = True
    sub.is_reviewed = payload.is_reviewed
    
    db.commit()
    
    final = sub.override_score if sub.is_overridden else sub.ai_score
    
    return {
        "message": "Override saved",
        "student_id": student_id,
        "final_score": final
    }

@app.post("/session/{session_id}/retry-failed")
async def retry_failed(session_id: int, db: Session = Depends(get_db)):
    """Reset failed submissions to pending so they can be re-graded."""
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    failed_subs = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id,
        StudentSubmission.status == "error"
    ).all()
    
    count = 0
    for sub in failed_subs:
        sub.status = "pending"
        sub.error_message = None
        sub.retry_count = (sub.retry_count or 0) + 1
        count += 1
    
    if count > 0:
        session.status = "pending"
    
    db.commit()
    
    return JSONResponse({"message": f"Reset {count} failed submissions", "reset_count": count})


@app.post("/session/{session_id}/delete")
def delete_session(session_id: int, db: Session = Depends(get_db)):
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    import shutil
    session_dir = UPLOAD_DIR / str(session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir)
    
    db.delete(session)
    db.commit()
    
    return RedirectResponse(url="/", status_code=303)

# ── File serving endpoints ────────────────────────────────────────

@app.get("/session/{session_id}/student/{student_id}/files")
def list_student_files(session_id: int, student_id: int, db: Session = Depends(get_db)):
    """Return JSON list of all files for a student with view URLs."""
    sub = db.query(StudentSubmission).filter(
        StudentSubmission.id == student_id,
        StudentSubmission.session_id == session_id
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Student not found")

    file_meta = []
    if sub.files:
        try:
            file_meta = json.loads(sub.files)
        except Exception:
            pass

    # Determine the actual directory for this student
    session_dir = UPLOAD_DIR / str(session_id)
    student_dir = session_dir / "_master" / sub.student_identifier
    if not student_dir.exists():
        student_dir = session_dir / sub.student_identifier

    result = []
    for f in file_meta:
        rel_path = f.get('relative_path', f.get('filename', ''))
        file_path = student_dir / rel_path if student_dir.exists() else None
        ext = Path(rel_path).suffix.lower()

        # Determine display type
        CODE_EXTS = {".py", ".java", ".cpp", ".c", ".js", ".ts", ".cs", ".go", ".rb",
                     ".php", ".swift", ".kt", ".scala", ".rs", ".sh", ".sql", ".html",
                     ".css", ".r", ".m", ".h"}
        IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
        TEXT_EXTS = {".txt", ".md", ".csv", ".xml", ".json", ".rtf"}

        if ext in CODE_EXTS:
            display_type = "code"
        elif ext in IMAGE_EXTS:
            display_type = "image"
        elif ext == ".pdf":
            display_type = "pdf"
        elif ext == ".docx":
            display_type = "docx"
        elif ext == ".ipynb":
            display_type = "notebook"
        elif ext in TEXT_EXTS:
            display_type = "text"
        else:
            display_type = "binary"

        exists = file_path.exists() if file_path else False
        view_url = f"/session/{session_id}/student/{student_id}/file/{rel_path}" if exists else None

        result.append({
            "filename": f.get('filename', Path(rel_path).name),
            "relative_path": rel_path,
            "type": f.get('type', display_type),
            "display_type": display_type,
            "size": f.get('size', 0),
            "extension": ext,
            "exists": exists,
            "view_url": view_url,
        })

    return result


@app.get("/session/{session_id}/student/{student_id}/file/{file_path:path}")
def serve_student_file(session_id: int, student_id: int, file_path: str, db: Session = Depends(get_db)):
    """Serve a student's file for inline viewing."""
    sub = db.query(StudentSubmission).filter(
        StudentSubmission.id == student_id,
        StudentSubmission.session_id == session_id
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Student not found")

    session_dir = UPLOAD_DIR / str(session_id)
    student_dir = session_dir / "_master" / sub.student_identifier
    if not student_dir.exists():
        student_dir = session_dir / sub.student_identifier

    full_path = student_dir / file_path

    # Security: ensure the resolved path is within the student directory
    try:
        full_path = full_path.resolve()
        student_dir_resolved = student_dir.resolve()
        if not str(full_path).startswith(str(student_dir_resolved)):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid path")

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    ext = full_path.suffix.lower()

    # For code/text files, return raw text with proper content type
    CODE_EXTS = {".py", ".java", ".cpp", ".c", ".js", ".ts", ".cs", ".go", ".rb",
                 ".php", ".swift", ".kt", ".scala", ".rs", ".sh", ".sql", ".html",
                 ".css", ".r", ".m", ".h"}
    TEXT_EXTS = {".txt", ".md", ".csv", ".xml", ".json", ".rtf"}

    if ext in CODE_EXTS or ext in TEXT_EXTS:
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
            return PlainTextResponse(content=content, media_type="text/plain; charset=utf-8")
        except Exception:
            pass

    if ext == ".ipynb":
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
            return PlainTextResponse(content=content, media_type="application/json; charset=utf-8")
        except Exception:
            pass

    # For docx, extract text and return
    if ext == ".docx":
        try:
            from app.services.file_parser import _parse_docx
            result = _parse_docx(full_path)
            return PlainTextResponse(content=result.get("content", ""), media_type="text/plain; charset=utf-8")
        except Exception:
            pass

    # For images, PDFs, and other binary files — serve directly
    media_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
    return FileResponse(str(full_path), media_type=media_type)


@app.get("/health")
def health_check():
    return {"status": "healthy", "version": "4.0.0"}
