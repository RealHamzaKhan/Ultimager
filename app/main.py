"""AI Grading System - Enhanced Version with Full Feature Set"""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import statistics
import threading
import urllib.parse
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue, Empty
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Depends, File, Form, UploadFile, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import UPLOAD_DIR, BASE_DIR, ACMAG_ENABLED, SCORING_CONSISTENCY_ALERT_DELTA, PARALLEL_GRADING_ENABLED, PARALLEL_GRADING_WORKERS
from app.database import get_db, init_db, SessionLocal
from app.models import GradingSession, StudentSubmission, GradingProgress
from app.schemas import OverridePayload
from app.services.zip_processor import extract_master_archive_with_verification, cleanup_session_files
from app.services.exporter import export_csv, export_json
from app.services.file_parser_enhanced import process_student_submission, ExtractedContent
from app.services.ai_grader_fixed import (
    grade_student,
    generate_rubric_from_description,
    validate_submission_relevance,
    compute_grading_hash,
    evaluate_relevance_gate,
    build_relevance_block_result,
)
from app.services.acmag import ACMAGRuntime, grade_submission_acmag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.FileHandler("server.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Global storage for grading state
_active_grading: Dict[int, Dict[str, Any]] = {}
_stop_grading_flags: Dict[int, bool] = {}  # Session ID -> should stop
_sse_queues: Dict[int, List[Queue]] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AI Grading System v5.0...")
    init_db()
    UPLOAD_DIR.mkdir(exist_ok=True)
    logger.info("System ready")
    yield
    logger.info("System shutdown")

app = FastAPI(title="AI Grading System", version="5.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Location"],
)
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


def _run_coro_sync(coro):
    """Run an async coroutine in an isolated event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

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

    # Handle new fields that might not exist in old database records
    ingestion_report = None
    try:
        if hasattr(sub, 'ingestion_report') and sub.ingestion_report:
            ingestion_report = json.loads(sub.ingestion_report)
    except (json.JSONDecodeError, TypeError, AttributeError):
        ingestion_report = None

    relevance_flags = None
    try:
        if hasattr(sub, 'relevance_flags') and sub.relevance_flags:
            relevance_flags = json.loads(sub.relevance_flags)
    except (json.JSONDecodeError, TypeError, AttributeError):
        relevance_flags = None
    
    # Handle other new fields with defaults
    is_relevant = getattr(sub, 'is_relevant', True)
    is_flagged = getattr(sub, 'is_flagged', False)
    flag_reason = getattr(sub, 'flag_reason', None) or ""
    flagged_by = getattr(sub, 'flagged_by', None) or ""
    flagged_at = getattr(sub, 'flagged_at', None)

    ai_score_val = sub.ai_score
    final_score_val = sub.override_score if (sub.is_overridden and sub.override_score is not None) else sub.ai_score
    
    return {
        "id": sub.id,
        "session_id": sub.session_id,
        "student_identifier": sub.student_identifier,
        "status": sub.status,
        "file_count": sub.file_count or len(files),
        "ai_score": float(ai_score_val) if ai_score_val is not None else None,
        "ai_letter_grade": sub.ai_letter_grade or "",
        "ai_confidence": sub.ai_confidence or "",
        "final_score": float(final_score_val) if final_score_val is not None else None,
        "is_overridden": bool(sub.is_overridden),
        "override_score": float(sub.override_score) if sub.override_score is not None else None,
        "override_comments": sub.override_comments or "",
        "is_reviewed": bool(sub.is_reviewed),
        "tests_passed": sub.tests_passed or 0,
        "tests_total": sub.tests_total or 0,
        "graded_at": sub.graded_at.isoformat() if sub.graded_at else None,
        "error_message": sub.error_message or "",
        "files": files,
        "ai_result": ai_result,
        "ai_feedback": ai_result.get("overall_feedback", "") if ai_result else "",
        "rubric_breakdown": ai_result.get("rubric_breakdown", []) if ai_result else [],
        "question_mapping": ai_result.get("question_mapping", []) if ai_result else [],
        "file_analysis": ai_result.get("file_analysis", []) if ai_result else [],
        "strengths": ai_result.get("strengths", []) if ai_result else [],
        "weaknesses": ai_result.get("weaknesses", []) if ai_result else [],
        "critical_errors": ai_result.get("critical_errors", []) if ai_result else [],
        "suggestions_for_improvement": ai_result.get("suggestions_for_improvement", "") if ai_result else "",
        "visual_content_analysis": ai_result.get("visual_content_analysis") if ai_result else None,
        "confidence_reasoning": ai_result.get("confidence_reasoning", "") if ai_result else "",
        "percentage": ai_result.get("percentage", 0) if ai_result else 0,
        "grading_hash": ai_result.get("grading_hash", "") if ai_result else "",
        "transparency": ai_result.get("transparency") if ai_result else None,
        "ingestion_report": ingestion_report,
        "is_relevant": is_relevant,
        "relevance_flags": relevance_flags,
        "is_flagged": is_flagged,
        "flag_reason": flag_reason,
        "flagged_by": flagged_by,
        "flagged_at": flagged_at.isoformat() if flagged_at else None,
    }


def _compute_grade_distribution(submissions) -> dict:
    """Compute grade distribution from submissions list."""
    grades = []
    for sub in submissions:
        grade = sub.ai_letter_grade if hasattr(sub, 'ai_letter_grade') else sub.get('ai_letter_grade')
        if grade:
            grades.append(grade)
    return dict(Counter(grades))


def _safe_json_load(value: Any) -> dict:
    """Safely decode JSON text/object to dict."""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _build_results_tables(submissions: list[StudentSubmission], max_score: int) -> dict:
    """Build detailed table datasets for the results dashboard."""
    marks_rows = []
    rubric_rollup: dict[str, dict[str, Any]] = {}
    ingestion_rows = []
    llm_rows = []

    ingestion_overview = {
        "students_with_report": 0,
        "files_received": 0,
        "files_parsed": 0,
        "files_failed": 0,
        "total_text_chars": 0,
        "total_images": 0,
        "warnings": 0,
        "errors": 0,
    }

    for sub in submissions:
        ai = _safe_json_load(sub.ai_result)
        ingestion = _safe_json_load(sub.ingestion_report)
        final_score = sub.override_score if (sub.is_overridden and sub.override_score is not None) else sub.ai_score
        delta = None
        if sub.is_overridden and sub.override_score is not None and sub.ai_score is not None:
            delta = round(float(sub.override_score) - float(sub.ai_score), 2)

        marks_rows.append({
            "student_identifier": sub.student_identifier,
            "status": sub.status,
            "ai_score": float(sub.ai_score) if sub.ai_score is not None else None,
            "final_score": float(final_score) if final_score is not None else None,
            "delta": delta,
            "letter_grade": sub.ai_letter_grade or "",
            "confidence": sub.ai_confidence or "",
            "is_reviewed": bool(sub.is_reviewed),
            "is_overridden": bool(sub.is_overridden),
            "percentage": round((float(final_score) / max_score) * 100, 2) if (final_score is not None and max_score > 0) else None,
        })

        rubric_breakdown = ai.get("rubric_breakdown", [])
        if isinstance(rubric_breakdown, list):
            for item in rubric_breakdown:
                if not isinstance(item, dict):
                    continue
                criterion = str(item.get("criterion", "")).strip() or "Unlabeled Criterion"
                try:
                    score_val = float(item.get("score", 0) or 0)
                except (TypeError, ValueError):
                    score_val = 0.0
                try:
                    max_val = float(item.get("max", item.get("max_score", 0)) or 0)
                except (TypeError, ValueError):
                    max_val = 0.0

                if criterion not in rubric_rollup:
                    rubric_rollup[criterion] = {
                        "criterion": criterion,
                        "students_graded": 0,
                        "score_sum": 0.0,
                        "max_sum": 0.0,
                        "min_score": None,
                        "max_score": None,
                    }
                roll = rubric_rollup[criterion]
                roll["students_graded"] += 1
                roll["score_sum"] += score_val
                roll["max_sum"] += max_val
                roll["min_score"] = score_val if roll["min_score"] is None else min(roll["min_score"], score_val)
                roll["max_score"] = score_val if roll["max_score"] is None else max(roll["max_score"], score_val)

        if ingestion:
            summary = ingestion.get("summary", {}) if isinstance(ingestion.get("summary", {}), dict) else {}
            warnings = ingestion.get("warnings", [])
            errors = ingestion.get("errors", [])
            warnings_count = len(warnings) if isinstance(warnings, list) else 0
            errors_count = len(errors) if isinstance(errors, list) else 0

            received = int(summary.get("received", 0) or 0)
            parsed = int(summary.get("parsed", 0) or 0)
            failed = int(summary.get("failed", 0) or 0)
            text_chars = int(ingestion.get("total_text_chars", 0) or 0)
            images = int(ingestion.get("total_images", 0) or 0)

            ingestion_rows.append({
                "student_identifier": sub.student_identifier,
                "received": received,
                "parsed": parsed,
                "failed": failed,
                "total_text_chars": text_chars,
                "total_images": images,
                "warnings_count": warnings_count,
                "errors_count": errors_count,
                "content_truncated": bool(ingestion.get("content_truncated", False)),
                "timestamp": ingestion.get("timestamp", ""),
            })

            ingestion_overview["students_with_report"] += 1
            ingestion_overview["files_received"] += received
            ingestion_overview["files_parsed"] += parsed
            ingestion_overview["files_failed"] += failed
            ingestion_overview["total_text_chars"] += text_chars
            ingestion_overview["total_images"] += images
            ingestion_overview["warnings"] += warnings_count
            ingestion_overview["errors"] += errors_count

        transparency = ai.get("transparency", {})
        if isinstance(transparency, dict) and transparency:
            llm_call = transparency.get("llm_call", {}) if isinstance(transparency.get("llm_call", {}), dict) else {}
            usage = llm_call.get("usage", {}) if isinstance(llm_call.get("usage", {}), dict) else {}
            llm_rows.append({
                "student_identifier": sub.student_identifier,
                "model": llm_call.get("model", ""),
                "provider": llm_call.get("provider", ""),
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
                "text_chars_sent": int(transparency.get("text_chars_sent", 0) or 0),
                "images_sent": int(transparency.get("images_sent", 0) or 0),
                "fallback_used": bool(llm_call.get("fallback_used", False)),
                "consistency_alert": bool(llm_call.get("consistency_alert", False)),
                "confidence": sub.ai_confidence or "",
            })

    rubric_rows = []
    for _, item in sorted(rubric_rollup.items(), key=lambda kv: kv[0].lower()):
        students_graded = item["students_graded"] or 1
        max_sum = item["max_sum"] or 0.0
        rubric_rows.append({
            "criterion": item["criterion"],
            "students_graded": item["students_graded"],
            "avg_score": round(item["score_sum"] / students_graded, 2),
            "avg_max": round(max_sum / students_graded, 2) if students_graded > 0 else 0.0,
            "attainment_pct": round((item["score_sum"] / max_sum) * 100, 2) if max_sum > 0 else 0.0,
            "min_score": round(item["min_score"], 2) if item["min_score"] is not None else 0.0,
            "max_score": round(item["max_score"], 2) if item["max_score"] is not None else 0.0,
        })

    llm_overview = {
        "calls": len(llm_rows),
        "prompt_tokens": sum(r["prompt_tokens"] for r in llm_rows),
        "completion_tokens": sum(r["completion_tokens"] for r in llm_rows),
        "total_tokens": sum(r["total_tokens"] for r in llm_rows),
        "text_chars_sent": sum(r["text_chars_sent"] for r in llm_rows),
        "images_sent": sum(r["images_sent"] for r in llm_rows),
        "fallback_used_calls": sum(1 for r in llm_rows if r["fallback_used"]),
        "consistency_alerts": sum(1 for r in llm_rows if r["consistency_alert"]),
    }

    return {
        "marks_rows": marks_rows,
        "rubric_rows": rubric_rows,
        "ingestion_rows": ingestion_rows,
        "ingestion_overview": ingestion_overview,
        "llm_rows": llm_rows,
        "llm_overview": llm_overview,
    }


def _find_cached_result_by_hash(
    db: Session,
    session_id: int,
    grading_hash: str,
    exclude_submission_id: Optional[int] = None,
) -> Optional[dict]:
    """Find a prior successful grade with identical input hash."""
    if not grading_hash:
        return None

    candidates = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id,
    ).all()

    for cand in candidates:
        if exclude_submission_id is not None and cand.id == exclude_submission_id:
            continue
        parsed = _safe_json_load(cand.ai_result)
        if not parsed or parsed.get("error"):
            continue
        if parsed.get("grading_hash") != grading_hash:
            continue
        if str(parsed.get("confidence", "")).lower() == "low":
            continue

        reused = json.loads(json.dumps(parsed))
        transparency = reused.get("transparency")
        if not isinstance(transparency, dict):
            transparency = {}
            reused["transparency"] = transparency
        llm_call = transparency.get("llm_call")
        if not isinstance(llm_call, dict):
            llm_call = {}
            transparency["llm_call"] = llm_call
        llm_call["cache_reused_from_student"] = cand.student_identifier
        llm_call["cache_reused_at_utc"] = datetime.now(timezone.utc).isoformat()
        reused["reused_from_cache"] = True
        return reused

    return None


def _annotate_regrade_consistency(result: dict[str, Any], previous_score: Optional[float]) -> dict[str, Any]:
    """Attach explicit consistency metadata for regrades."""
    if not isinstance(result, dict):
        return result
    if result.get("error"):
        return result
    if previous_score is None:
        return result

    try:
        previous_value = float(previous_score)
        current_value = float(result.get("total_score", 0) or 0)
    except (TypeError, ValueError):
        return result

    delta = round(abs(current_value - previous_value), 3)
    threshold = float(SCORING_CONSISTENCY_ALERT_DELTA)
    alert = delta > threshold

    transparency = result.get("transparency")
    if not isinstance(transparency, dict):
        transparency = {}
        result["transparency"] = transparency

    llm_call = transparency.get("llm_call")
    if not isinstance(llm_call, dict):
        llm_call = {}
        transparency["llm_call"] = llm_call

    llm_call["regrade_previous_score"] = previous_value
    llm_call["regrade_score_delta"] = delta
    llm_call["consistency_alert_threshold"] = threshold
    llm_call["consistency_alert"] = alert

    result["consistency"] = {
        "previous_score": previous_value,
        "current_score": current_value,
        "absolute_delta": delta,
        "threshold": threshold,
        "alert": alert,
    }
    return result


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    sessions = db.query(GradingSession).order_by(GradingSession.created_at.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request, "sessions": sessions},
    )


@app.get("/api/sessions")
def api_list_sessions(db: Session = Depends(get_db)):
    sessions = db.query(GradingSession).order_by(GradingSession.created_at.desc()).all()
    return {
        "count": len(sessions),
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "status": s.status,
                "total_students": s.total_students or 0,
                "graded_count": s.graded_count or 0,
                "error_count": s.error_count or 0,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "max_score": s.max_score or 100,
            }
            for s in sessions
        ],
    }

@app.get("/session/new", response_class=HTMLResponse)
def new_session_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="new_session.html",
        context={"request": request},
    )

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
    reference_solution: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if max_score < 1:
        max_score = 1
    session = GradingSession(
        title=title,
        description=description or "",
        rubric=rubric or "",
        max_score=max_score,
        run_command=run_command,
        test_cases=test_cases,
        questions=questions,
        reference_solution=reference_solution,
        status="pending",
        total_students=0,
        graded_count=0
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return RedirectResponse(url=f"/session/{session.id}", status_code=303)


@app.post("/api/generate-rubric")
async def api_generate_rubric(
    description: str = Form(...),
    max_score: int = Form(100),
    strictness: str = Form("balanced"),
    detail_level: str = Form("balanced"),
):
    """Generate a rubric from assignment description."""
    if max_score < 1:
        return JSONResponse(
            {"success": False, "error": "max_score must be at least 1"},
            status_code=422,
        )
    if strictness not in ("lenient", "balanced", "strict"):
        strictness = "balanced"
    if detail_level not in ("simple", "balanced", "detailed"):
        detail_level = "balanced"
    try:
        result = await generate_rubric_from_description(
            description, max_score, strictness, detail_level=detail_level,
        )
        return JSONResponse(result)
    except Exception as e:
        logger.exception("Rubric generation failed")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/session/{session_id}", response_class=HTMLResponse)
def session_detail(session_id: int, request: Request, db: Session = Depends(get_db)):
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    submissions = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id
    ).all()
    
    serialized = [_serialize_submission(sub) for sub in submissions]
    
    return templates.TemplateResponse(
        request=request,
        name="session.html",
        context={
            "request": request,
            "session": session,
            "submissions": submissions,
            "submissions_json": json.dumps(serialized, default=str),
        },
    )

@app.post("/session/{session_id}/upload")
async def upload_zip(
    session_id: int,
    zip_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status == "grading":
        raise HTTPException(status_code=409, detail="Cannot upload while grading is in progress")

    # Replace existing upload content for this session to avoid stale/test-data contamination.
    _stop_grading_flags.pop(session_id, None)
    _active_grading.pop(session_id, None)
    _sse_queues.pop(session_id, None)

    old_submissions = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id
    ).all()
    for sub in old_submissions:
        db.delete(sub)

    old_progress = db.query(GradingProgress).filter(
        GradingProgress.session_id == session_id
    ).all()
    for progress in old_progress:
        db.delete(progress)

    session.total_students = 0
    session.graded_count = 0
    session.error_count = 0
    session.status = "pending"
    session.started_at = None
    session.completed_at = None
    session.current_student_index = 0
    session.grading_progress = {}
    db.commit()

    cleanup_session_files(session_id)
    
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


@app.get("/session/{session_id}/student/{student_id}/ingestion-report")
def get_ingestion_report(session_id: int, student_id: int, db: Session = Depends(get_db)):
    """Get the ingestion report for a specific student."""
    sub = db.query(StudentSubmission).filter(
        StudentSubmission.id == student_id,
        StudentSubmission.session_id == session_id
    ).first()
    
    if not sub:
        raise HTTPException(status_code=404, detail="Student not found")
    
    if sub.ingestion_report:
        try:
            report = json.loads(sub.ingestion_report)
            return JSONResponse(report)
        except:
            pass
    
    return JSONResponse({"error": "No ingestion report available"}, status_code=404)


@app.post("/session/{session_id}/student/{student_id}/flag")
async def flag_anomaly(
    session_id: int,
    student_id: int,
    reason: str = Form(...),
    db: Session = Depends(get_db),
):
    """Flag a student submission as anomalous."""
    sub = db.query(StudentSubmission).filter(
        StudentSubmission.id == student_id,
        StudentSubmission.session_id == session_id
    ).first()
    
    if not sub:
        raise HTTPException(status_code=404, detail="Student not found")
    
    sub.is_flagged = True
    sub.flag_reason = reason
    sub.flagged_by = "user"
    sub.flagged_at = datetime.now(timezone.utc)
    db.commit()
    
    return JSONResponse({"message": "Submission flagged", "student_id": student_id})


@app.post("/session/{session_id}/student/{student_id}/unflag")
async def unflag_anomaly(
    session_id: int,
    student_id: int,
    db: Session = Depends(get_db),
):
    """Remove anomaly flag from a student submission."""
    sub = db.query(StudentSubmission).filter(
        StudentSubmission.id == student_id,
        StudentSubmission.session_id == session_id
    ).first()
    
    if not sub:
        raise HTTPException(status_code=404, detail="Student not found")
    
    sub.is_flagged = False
    sub.flag_reason = None
    sub.flagged_by = None
    sub.flagged_at = None
    db.commit()
    
    return JSONResponse({"message": "Flag removed", "student_id": student_id})


@app.post("/session/{session_id}/grade")
async def start_grading(session_id: int, db: Session = Depends(get_db)):
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.status == "grading" or session_id in _active_grading:
        return JSONResponse({"message": "Already grading", "session_id": session_id})
    
    session.status = "grading"
    session.graded_count = 0
    db.commit()
    
    # Reset stop flag
    _stop_grading_flags[session_id] = False
    
    # Initialize progress tracking
    _active_grading[session_id] = {
        "status": "grading",
        "current_student": "",
        "graded_count": 0,
        "failed_count": 0,
        "total": session.total_students,
        "stage": "Initializing pipeline (upload/extract/OCR/vision/grading/moderation)...",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # Disable ACMAG in live grading runs for stability; fall back to standard grading.
    use_acmag = False
    if ACMAG_ENABLED:
        logger.warning("ACMAG is configured but disabled for this run to keep grading throughput stable.")

    # Determine if we should use parallel grading.
    use_parallel = PARALLEL_GRADING_ENABLED
    mode_label = "parallel" if use_parallel else "sequential"
    if not use_acmag:
        mode_label += "_no_acmag"
    
    if use_parallel:
        # Use parallel grading
        _active_grading[session_id]["stage"] = "Parallel grading (ACMAG disabled)"
        asyncio.get_event_loop().run_in_executor(
            None, _grade_all_students_parallel, session_id, use_acmag
        )
    else:
        # Use sequential grading
        _active_grading[session_id]["stage"] = "Sequential grading (ACMAG disabled)"
        asyncio.get_event_loop().run_in_executor(
            None, _grade_all_students_sync, session_id, use_acmag
        )
    
    return JSONResponse({
        "message": "Grading started",
        "session_id": session_id,
        "mode": mode_label,
    })


@app.post("/session/{session_id}/stop-grading")
async def stop_grading(session_id: int, db: Session = Depends(get_db)):
    """Stop an in-progress grading job."""
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.status != "grading":
        return JSONResponse({"message": "Not currently grading"})
    
    # Set stop flag
    _stop_grading_flags[session_id] = True
    
    # Update session status
    session.status = "paused"
    session.completed_at = datetime.now(timezone.utc)
    db.commit()
    
    # Update progress tracking
    if session_id in _active_grading:
        _active_grading[session_id]["status"] = "stopped"
        _active_grading[session_id]["stage"] = "Stopped by user"
    
    # Broadcast stop event
    _broadcast_sse(session_id, {
        "type": "stopped",
        "graded_count": session.graded_count or 0,
        "failed_count": session.error_count or 0,
        "total": session.total_students or 0,
        "message": "Grading stopped by user"
    })
    
    return JSONResponse({"message": "Grading stopped"})


@app.post("/session/{session_id}/regrade-all")
async def regrade_all(session_id: int, db: Session = Depends(get_db)):
    """Re-grade all students in the session."""
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Reset all submissions to pending
    submissions = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id
    ).all()
    
    for sub in submissions:
        sub.status = "pending"
        sub.error_message = None
        sub.is_relevant = True
        sub.relevance_flags = None
        if sub.flagged_by == "system":
            sub.is_flagged = False
            sub.flag_reason = None
            sub.flagged_by = None
            sub.flagged_at = None
    
    session.graded_count = 0
    session.error_count = 0
    session.status = "pending"
    db.commit()
    
    # Start grading
    return await start_grading(session_id, db)


@app.post("/session/{session_id}/student/{student_id}/regrade")
async def regrade_student(
    session_id: int,
    student_id: int,
    db: Session = Depends(get_db),
):
    """Re-grade a single student."""
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Prevent regrade while session is actively grading
    # Check actual grading status, not stale _active_grading entries
    active_info = _active_grading.get(session_id)
    is_actually_grading = (
        session.status == "grading"
        or (active_info is not None and active_info.get("status") == "grading")
    )
    if is_actually_grading:
        return JSONResponse(
            {"message": "Cannot regrade while grading is in progress. Stop grading first."},
            status_code=409,
        )
    # Clean up any stale active_grading entry
    if active_info is not None and active_info.get("status") != "grading":
        _active_grading.pop(session_id, None)
    
    sub = db.query(StudentSubmission).filter(
        StudentSubmission.id == student_id,
        StudentSubmission.session_id == session_id
    ).first()
    
    if not sub:
        raise HTTPException(status_code=404, detail="Student not found")
    
    # Reset submission
    sub.status = "pending"
    sub.error_message = None
    sub.is_relevant = True
    sub.relevance_flags = None
    if sub.flagged_by == "system":
        sub.is_flagged = False
        sub.flag_reason = None
        sub.flagged_by = None
        sub.flagged_at = None
    db.commit()

    # Track a lightweight active job so SSE remains open for single re-grade.
    _stop_grading_flags[session_id] = False
    _active_grading[session_id] = {
        "status": "grading",
        "current_student": sub.student_identifier,
        "graded_count": session.graded_count or 0,
        "failed_count": session.error_count or 0,
        "total": session.total_students or 0,
        "stage": f"Extraction + OCR (regrade) {sub.student_identifier}",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    
    # Grade this student immediately
    asyncio.get_event_loop().run_in_executor(None, _grade_single_student_sync, session_id, student_id)
    
    return JSONResponse({"message": "Re-grading started", "student_id": student_id})


def _broadcast_sse(session_id: int, event: dict):
    """Push an SSE event to all connected clients for a session."""
    progress = _active_grading.get(session_id)
    if progress is not None and isinstance(event, dict):
        event_type = event.get("type")

        # Prefer explicit counters from events; otherwise derive from worker signals.
        if "graded_count" in event:
            progress["graded_count"] = int(event.get("graded_count") or 0)
        elif event_type == "student_graded":
            progress["graded_count"] = int(progress.get("graded_count") or 0) + 1

        if "failed_count" in event:
            progress["failed_count"] = int(event.get("failed_count") or 0)
        elif event_type == "student_error" and not event.get("will_retry", False):
            progress["failed_count"] = int(progress.get("failed_count") or 0) + 1

        if "total" in event:
            progress["total"] = int(event.get("total") or 0)
        if "student" in event:
            progress["current_student"] = str(event.get("student") or "")
        if "stage" in event:
            progress["stage"] = str(event.get("stage") or "")

        if event_type == "complete":
            progress["status"] = "completed"
            progress["current_student"] = ""
            progress["stage"] = "Complete"
        elif event_type == "stopped":
            progress["status"] = "stopped"
            progress["current_student"] = ""
            progress["stage"] = "Stopped by user"
        elif event_type in {"progress", "student_graded", "student_error", "student_complete"}:
            progress["status"] = "grading"

    queues = _sse_queues.get(session_id, [])
    for q in queues:
        try:
            q.put_nowait(event)
        except Exception:
            pass


def _grade_single_student_sync(session_id: int, student_id: int):
    """Grade a single student in background."""
    db = SessionLocal()
    sub = None
    
    try:
        session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
        sub = db.query(StudentSubmission).filter(
            StudentSubmission.id == student_id,
            StudentSubmission.session_id == session_id
        ).first()
        
        if not session or not sub:
            return
        
        previous_ai_score = float(sub.ai_score) if sub.ai_score is not None else None
        sub.status = "grading"
        db.commit()

        if session_id in _active_grading:
            _active_grading[session_id].update({
                "status": "grading",
                "current_student": sub.student_identifier,
                "stage": f"Extraction + OCR (regrade) {sub.student_identifier}",
            })
        
        _broadcast_sse(session_id, {
            "type": "progress",
            "student": sub.student_identifier,
            "student_id": sub.id,
            "graded_count": session.graded_count or 0,
            "failed_count": session.error_count or 0,
            "total": session.total_students or 0,
            "stage": "Extraction + OCR (single regrade)"
        })
        
        # Load and process files
        session_dir = UPLOAD_DIR / str(session_id)
        
        possible_dirs = [
            session_dir / sub.student_identifier,
            session_dir / "_master" / sub.student_identifier,
        ]
        
        student_dir = None
        for d in possible_dirs:
            if d.exists() and d.is_dir():
                student_dir = d
                break
        
        student_files = []
        ingestion_report_dict = None
        if student_dir:
            extracted_contents, ingestion_report = process_student_submission(
                student_dir, sub.student_identifier, session_id
            )
            student_files = extracted_contents
            ingestion_report_dict = ingestion_report.to_dict()
            sub.ingestion_report = json.dumps(ingestion_report_dict)
            db.commit()
            _broadcast_sse(session_id, {
                "type": "ingestion_complete",
                "student_id": sub.id,
                "student_identifier": sub.student_identifier,
                "ingestion_report": ingestion_report_dict,
            })
        
        # Validate relevance
        _broadcast_sse(session_id, {
            "type": "progress",
            "student": sub.student_identifier,
            "student_id": sub.id,
            "graded_count": session.graded_count or 0,
            "failed_count": session.error_count or 0,
            "total": session.total_students or 0,
            "stage": "Relevance check (single regrade)",
        })
        relevance = _run_coro_sync(validate_submission_relevance(
            title=str(session.title),
            description=str(session.description) if session.description else "",
            student_files=student_files,
            rubric=str(session.rubric) if session.rubric else ""
        ))
        
        sub.is_relevant = relevance.get("is_relevant", True)
        sub.relevance_flags = json.dumps(relevance.get("flags", []))
        db.commit()
        
        # Grade (reuse cached deterministic result if an identical grading hash already exists)
        questions = []
        if session.questions:
            try:
                questions = json.loads(session.questions)
            except:
                pass

        rubric_text = str(session.rubric) if session.rubric else ""
        max_score_value = int(session.max_score) if session.max_score else 100
        grading_hash = compute_grading_hash(student_files, rubric_text, max_score_value)
        use_acmag = False
        _img_count = sum(
            len(getattr(f, "images", []) or [])
            for f in student_files
            if hasattr(f, "images")
        )
        relevance_gate = evaluate_relevance_gate(relevance, image_count=_img_count)
        result: dict[str, Any]

        if relevance_gate.get("block_grading"):
            logger.warning(
                "Single regrade blocked by relevance gate for %s: %s (flags=%s, confidence=%s)",
                sub.student_identifier,
                relevance_gate.get("reason", "irrelevant"),
                relevance_gate.get("flags", []),
                relevance_gate.get("confidence", "unknown"),
            )
            result = build_relevance_block_result(rubric_text, max_score_value, relevance, relevance_gate)
            result["grading_hash"] = grading_hash
            # Add transparency for Mapping tab
            result["transparency"] = {
                "text_chars_sent": 0,
                "images_sent": 0,
                "images_available_total": _img_count,
                "images_selected_total": 0,
                "files_processed": [
                    {
                        "filename": getattr(f, "filename", "unknown"),
                        "type": getattr(f, "file_type", "unknown"),
                        "text_length": len(getattr(f, "text_content", "") or ""),
                        "image_count": len(getattr(f, "images", []) or []),
                    }
                    for f in student_files
                    if hasattr(f, "filename")
                ],
                "images_info": [],
                "blocked_by_relevance_gate": True,
                "llm_call": {},
            }
        else:
            if use_acmag and ACMAG_ENABLED:
                _broadcast_sse(session_id, {
                    "type": "progress",
                    "student": sub.student_identifier,
                    "student_id": sub.id,
                    "graded_count": session.graded_count or 0,
                    "failed_count": session.error_count or 0,
                    "total": session.total_students or 0,
                    "stage": "Vision + ACMAG grading (single regrade)",
                })
                try:
                    acmag_pack = _run_coro_sync(asyncio.wait_for(grade_submission_acmag(
                        title=str(session.title),
                        description=str(session.description) if session.description else "",
                        rubric=rubric_text,
                        max_score=max_score_value,
                        student_files=student_files,
                        questions=questions,
                        student_identifier=str(sub.student_identifier),
                        anchor_context="",
                        run_secondary=True,
                        moderation_delta=1.0,
                    ), timeout=45))
                    result = dict(acmag_pack.get("result") or {})
                    if acmag_pack.get("moderation"):
                        _broadcast_sse(session_id, {
                            "type": "progress",
                            "student": sub.student_identifier,
                            "student_id": sub.id,
                            "graded_count": session.graded_count or 0,
                            "failed_count": session.error_count or 0,
                            "total": session.total_students or 0,
                            "stage": "Moderation review (single regrade)",
                        })
                except asyncio.TimeoutError:
                    logger.warning(
                        f"ACMAG timed out for single regrade of {sub.student_identifier}; falling back to standard grading"
                    )
                    result = _run_coro_sync(grade_student(
                        title=str(session.title),
                        description=str(session.description) if session.description else "",
                        rubric=rubric_text,
                        max_score=max_score_value,
                        student_files=student_files,
                        questions=questions,
                        reference_solution=getattr(session, "reference_solution", None) or None,
                        test_cases=getattr(session, "test_cases", None) or None,
                        run_command=getattr(session, "run_command", None) or None,
                        student_dir=str(student_dir) if student_dir else None,
                    ))
            else:
                cached_result = _find_cached_result_by_hash(db, session_id, grading_hash, exclude_submission_id=sub.id)

                if cached_result:
                    result = cached_result
                    _broadcast_sse(session_id, {
                        "type": "progress",
                        "student": sub.student_identifier,
                        "student_id": sub.id,
                        "graded_count": session.graded_count or 0,
                        "failed_count": session.error_count or 0,
                        "total": session.total_students or 0,
                        "stage": "Deterministic cache reuse (single regrade)",
                    })
                else:
                    _broadcast_sse(session_id, {
                        "type": "progress",
                        "student": sub.student_identifier,
                        "student_id": sub.id,
                        "graded_count": session.graded_count or 0,
                        "failed_count": session.error_count or 0,
                        "total": session.total_students or 0,
                        "stage": "Vision + grading (single regrade)",
                    })
                    result = _run_coro_sync(grade_student(
                        title=str(session.title),
                        description=str(session.description) if session.description else "",
                        rubric=rubric_text,
                        max_score=max_score_value,
                        student_files=student_files,
                        questions=questions,
                        reference_solution=getattr(session, "reference_solution", None) or None,
                        test_cases=getattr(session, "test_cases", None) or None,
                        run_command=getattr(session, "run_command", None) or None,
                        student_dir=str(student_dir) if student_dir else None,
                    ))

        if isinstance(result, dict) and not result.get("grading_hash"):
            result["grading_hash"] = grading_hash
        if isinstance(result, dict):
            result["relevance_gate"] = relevance_gate
        if isinstance(result, dict):
            result = _annotate_regrade_consistency(result, previous_ai_score)
        
        if result.get("error"):
            sub.status = "error"
            sub.error_message = result.get('error')
            sub.ai_result = json.dumps(result)
        else:
            sub.ai_result = json.dumps(result)
            sub.ai_score = result.get("total_score")
            sub.ai_letter_grade = result.get("letter_grade")
            sub.ai_confidence = result.get("confidence")
            sub.status = "graded"
            sub.graded_at = datetime.now(timezone.utc)
            if relevance_gate.get("block_grading") or relevance_gate.get("review_required"):
                sub.is_flagged = True
                if sub.flagged_by != "user":
                    sub.flag_reason = relevance_gate.get("reason") or "Relevance warnings require manual review"
                    sub.flagged_by = "system"
                    sub.flagged_at = datetime.now(timezone.utc)
            elif sub.flagged_by == "system":
                sub.is_flagged = False
                sub.flag_reason = None
                sub.flagged_by = None
                sub.flagged_at = None
        
        session.graded_count = db.query(StudentSubmission).filter(
            StudentSubmission.session_id == session_id,
            StudentSubmission.status == "graded"
        ).count()
        session.error_count = db.query(StudentSubmission).filter(
            StudentSubmission.session_id == session_id,
            StudentSubmission.status == "error"
        ).count()
        db.commit()
        
        if sub.status == "error":
            _broadcast_sse(session_id, {
                "type": "student_error",
                "student_id": sub.id,
                "student_identifier": sub.student_identifier,
                "error": sub.error_message or "Grading failed",
                "graded_count": session.graded_count or 0,
                "failed_count": session.error_count or 0,
                "total": session.total_students or 0,
                "result": result,
                "ingestion_report": ingestion_report_dict,
                "relevance": relevance,
                "relevance_gate": relevance_gate,
            })
        else:
            _broadcast_sse(session_id, {
                "type": "student_complete",
                "student_id": sub.id,
                "student_identifier": sub.student_identifier,
                "score": sub.ai_score,
                "grade": sub.ai_letter_grade or "",
                "confidence": sub.ai_confidence or "",
                "graded_count": session.graded_count or 0,
                "failed_count": session.error_count or 0,
                "total": session.total_students or 0,
                "result": result,
                "ingestion_report": ingestion_report_dict,
                "relevance": relevance,
                "relevance_gate": relevance_gate,
            })

        if session_id in _active_grading:
            _active_grading[session_id].update({
                "status": "completed",
                "current_student": "",
                "graded_count": session.graded_count or 0,
                "failed_count": session.error_count or 0,
                "stage": "Single student re-grade complete",
            })
        _broadcast_sse(session_id, {
            "type": "complete",
            "graded_count": session.graded_count or 0,
            "failed_count": session.error_count or 0,
            "total": session.total_students or 0,
            "message": f"Re-grade completed for {sub.student_identifier}",
        })
        
    except Exception as e:
        logger.exception(f"Failed to regrade student {student_id}")
        try:
            if sub is not None:
                sub.status = "error"
                sub.error_message = str(e)
                db.commit()
        except:
            pass
        _broadcast_sse(session_id, {
            "type": "student_error",
            "student_id": student_id,
            "error": str(e),
        })
    finally:
        db.close()
        _stop_grading_flags.pop(session_id, None)
        def _cleanup():
            import time
            time.sleep(3)
            _active_grading.pop(session_id, None)
            _sse_queues.pop(session_id, None)
        threading.Thread(target=_cleanup, daemon=True).start()


def _grade_all_students_parallel(session_id: int, use_acmag: bool = False):
    """Background parallel grading task - runs in a thread pool."""
    from app.services.parallel_grader import ParallelGrader
    from app.services.file_parser_enhanced import process_student_submission
    from app.services.ai_grader_fixed import validate_submission_relevance
    import asyncio
    
    db = SessionLocal()
    session = None
    
    try:
        session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
        if not session:
            return
        
        submissions = db.query(StudentSubmission).filter(
            StudentSubmission.session_id == session_id,
            StudentSubmission.status.in_(["pending", "error"])
        ).order_by(StudentSubmission.processing_order, StudentSubmission.id).all()
        
        total = len(submissions)
        
        if total == 0:
            session.status = "completed"
            session.completed_at = datetime.now(timezone.utc)
            db.commit()
            return
        
        questions = []
        if session.questions:
            try:
                questions = json.loads(session.questions)
            except:
                pass
        
        rubric_text = str(session.rubric) if session.rubric else ""
        max_score_value = int(session.max_score) if session.max_score else 100
        
        # Create ACMAG runtime if enabled for this run
        from app.services.acmag import ACMAGRuntime
        acmag_runtime = None
        if use_acmag and ACMAG_ENABLED:
            acmag_runtime = ACMAGRuntime(
                session_id=session_id,
                max_score=max_score_value,
                submission_ids=[int(s.id) for s in submissions],
                submission_identifiers={int(s.id): str(s.student_identifier) for s in submissions},
            )
            if acmag_runtime.enabled:
                _broadcast_sse(session_id, {
                    "type": "acmag_init",
                    "calibration_target": len(acmag_runtime.calibration_ids),
                    "blind_review_ratio": acmag_runtime.blind_review_ratio,
                    "kappa_threshold": acmag_runtime.kappa_threshold,
                })
        use_acmag_runtime = bool(acmag_runtime and acmag_runtime.enabled)
        
        # Broadcast start
        _broadcast_sse(session_id, {
            "type": "parallel_start",
            "total": total,
            "mode": "parallel_acmag" if use_acmag_runtime else "parallel",
            "message": f"Starting parallel grading for {total} submissions" + (" (with ACMAG)" if use_acmag_runtime else ""),
        })
        
        # Run parallel grading
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        grader = ParallelGrader(
            session_id=session_id,
            db=db,
            max_workers=PARALLEL_GRADING_WORKERS,
            sse_callback=_broadcast_sse,  # Pass SSE callback for progress updates
            stop_check=lambda: _stop_grading_flags.get(session_id, False),
        )

        result = loop.run_until_complete(
            grader.grade_batch_parallel(
                submissions=submissions,
                session=session,
                rubric_text=rubric_text,
                max_score=max_score_value,
                questions=questions,
                progress_callback=None,
                use_acmag=use_acmag_runtime,
                acmag_runtime=acmag_runtime,
            )
        )
        loop.close()

        was_stopped = result.get("stopped", False) or _stop_grading_flags.get(session_id, False)

        # Update session
        graded = db.query(StudentSubmission).filter(
            StudentSubmission.session_id == session_id,
            StudentSubmission.status == "graded"
        ).count()

        failed = db.query(StudentSubmission).filter(
            StudentSubmission.session_id == session_id,
            StudentSubmission.status == "error"
        ).count()

        session.graded_count = graded
        session.error_count = failed

        if was_stopped:
            # Don't overwrite status if stop endpoint already set it to "paused"
            if session.status != "paused":
                session.status = "paused"
            session.completed_at = datetime.now(timezone.utc)
            db.commit()

            if session_id in _active_grading:
                _active_grading[session_id].update({
                    "status": "stopped",
                    "current_student": "",
                    "graded_count": graded,
                    "failed_count": failed,
                    "stage": "Stopped by user",
                })

            _broadcast_sse(session_id, {
                "type": "stopped",
                "graded_count": graded,
                "failed_count": failed,
                "total": total,
                "message": f"Grading stopped: {graded} graded, {failed} failed",
            })

            logger.info(f"Parallel grading stopped for session {session_id}: {graded} graded, {failed} failed")
        else:
            session.status = "completed" if failed == 0 else "completed_with_errors"
            session.completed_at = datetime.now(timezone.utc)
            db.commit()

            # Broadcast completion
            _broadcast_sse(session_id, {
                "type": "complete",
                "graded_count": graded,
                "failed_count": failed,
                "total": total,
                "message": f"Grading finished: {graded} graded, {failed} failed (parallel mode)",
            })

            logger.info(f"Parallel grading completed for session {session_id}: {graded} graded, {failed} failed")
        
    except Exception as e:
        logger.exception(f"Critical error in parallel grading for session {session_id}")
        try:
            if session is not None:
                session.status = "failed"
                # Note: GradingSession has no error_message column;
                # store the error in the progress tracker instead.
                db.commit()
        except:
            pass
        
        _broadcast_sse(session_id, {
            "type": "error",
            "message": f"Grading failed: {str(e)[:200]}",
        })
    
    finally:
        db.close()
        _stop_grading_flags.pop(session_id, None)
        def _cleanup():
            import time
            time.sleep(3)
            _active_grading.pop(session_id, None)
            _sse_queues.pop(session_id, None)
        threading.Thread(target=_cleanup, daemon=True).start()


def _grade_all_students_sync(session_id: int, use_acmag: bool = False):
    """Background grading task - runs in a thread pool."""
    db = SessionLocal()
    session = None
    
    try:
        session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
        if not session:
            return

        submissions = db.query(StudentSubmission).filter(
            StudentSubmission.session_id == session_id,
            StudentSubmission.status.in_(["pending", "error"])
        ).order_by(StudentSubmission.processing_order, StudentSubmission.id).all()

        total = len(submissions)
        graded = session.graded_count or 0
        failed = session.error_count or 0

        questions = []
        if session.questions:
            try:
                questions = json.loads(session.questions)
            except:
                pass

        rubric_text = str(session.rubric) if session.rubric else ""
        max_score_value = int(session.max_score) if session.max_score else 100

        session_dir = UPLOAD_DIR / str(session_id)
        stop_requested = False

        acmag_runtime = None
        if use_acmag and ACMAG_ENABLED:
            acmag_runtime = ACMAGRuntime(
                session_id=session_id,
                max_score=max_score_value,
                submission_ids=[int(s.id) for s in submissions],
                submission_identifiers={int(s.id): str(s.student_identifier) for s in submissions},
            )
        if acmag_runtime and acmag_runtime.enabled:
            _broadcast_sse(session_id, {
                "type": "acmag_init",
                "calibration_target": len(acmag_runtime.calibration_ids),
                "blind_review_ratio": acmag_runtime.blind_review_ratio,
                "kappa_threshold": acmag_runtime.kappa_threshold,
            })

        for i, sub in enumerate(submissions):
            # Check stop flag
            if _stop_grading_flags.get(session_id, False):
                logger.info(f"Grading stopped for session {session_id}")
                stop_requested = True
                break
            
            try:
                previous_ai_score = float(sub.ai_score) if sub.ai_score is not None else None
                sub.status = "grading"
                db.commit()

                if session_id in _active_grading:
                    _active_grading[session_id].update({
                        "current_student": sub.student_identifier,
                        "graded_count": graded,
                        "failed_count": failed,
                        "stage": f"Extraction + OCR {i+1}/{total}",
                    })

                _broadcast_sse(session_id, {
                    "type": "progress",
                    "student": sub.student_identifier,
                    "graded_count": graded,
                    "failed_count": failed,
                    "total": total,
                    "stage": f"Extraction + OCR {i+1}/{total}",
                })

                # Load files and build ingestion report.
                possible_dirs = [
                    session_dir / sub.student_identifier,
                    session_dir / "_master" / sub.student_identifier,
                ]
                
                student_dir = None
                for d in possible_dirs:
                    if d.exists() and d.is_dir():
                        student_dir = d
                        break
                
                student_files = []
                ingestion_report_dict = None
                if student_dir:
                    try:
                        extracted_contents, ingestion_report = process_student_submission(
                            student_dir, sub.student_identifier, session_id
                        )
                        student_files = extracted_contents
                        ingestion_report_dict = ingestion_report.to_dict()
                        sub.ingestion_report = json.dumps(ingestion_report_dict)
                        db.commit()
                        _broadcast_sse(session_id, {
                            "type": "ingestion_complete",
                            "student_id": sub.id,
                            "student_identifier": sub.student_identifier,
                            "ingestion_report": ingestion_report_dict,
                        })
                    except Exception as e:
                        logger.error(f"Failed to process student files: {e}")
                
                _broadcast_sse(session_id, {
                    "type": "progress",
                    "student": sub.student_identifier,
                    "graded_count": graded,
                    "failed_count": failed,
                    "total": total,
                    "stage": f"Relevance check {i+1}/{total}",
                })

                # Validate relevance.
                relevance = {"is_relevant": True, "flags": []}
                _batch_img_count = sum(
                    len(getattr(f, "images", []) or [])
                    for f in student_files
                    if hasattr(f, "images")
                )
                relevance_gate = evaluate_relevance_gate(relevance, image_count=_batch_img_count)
                try:
                    relevance = _run_coro_sync(validate_submission_relevance(
                        title=str(session.title),
                        description=str(session.description) if session.description else "",
                        student_files=student_files,
                        rubric=str(session.rubric) if session.rubric else ""
                    ))

                    sub.is_relevant = relevance.get("is_relevant", True)
                    sub.relevance_flags = json.dumps(relevance.get("flags", []))
                    db.commit()
                    relevance_gate = evaluate_relevance_gate(relevance, image_count=_batch_img_count)
                    
                    if not sub.is_relevant:
                        logger.warning(f"Submission from {sub.student_identifier} flagged as irrelevant")
                except Exception as e:
                    logger.warning(f"Relevance validation failed: {e}")
                
                grading_hash = compute_grading_hash(student_files, rubric_text, max_score_value)
                result: dict[str, Any]

                if relevance_gate.get("block_grading"):
                    logger.warning(
                        "Blocking grading for %s due to relevance gate: %s (flags=%s, confidence=%s)",
                        sub.student_identifier,
                        relevance_gate.get("reason", "irrelevant"),
                        relevance_gate.get("flags", []),
                        relevance_gate.get("confidence", "unknown"),
                    )
                    result = build_relevance_block_result(rubric_text, max_score_value, relevance, relevance_gate)
                    result["grading_hash"] = grading_hash
                    result["transparency"] = {
                        "text_chars_sent": 0,
                        "images_sent": 0,
                        "images_available_total": _batch_img_count,
                        "images_selected_total": 0,
                        "files_processed": [
                            {
                                "filename": getattr(f, "filename", "unknown"),
                                "type": getattr(f, "file_type", "unknown"),
                                "text_length": len(getattr(f, "text_content", "") or ""),
                                "image_count": len(getattr(f, "images", []) or []),
                            }
                            for f in student_files
                            if hasattr(f, "filename")
                        ],
                        "images_info": [],
                        "blocked_by_relevance_gate": True,
                        "llm_call": {},
                    }
                elif acmag_runtime and acmag_runtime.enabled:
                    run_secondary = acmag_runtime.should_run_secondary(sub.id, str(sub.student_identifier))
                    anchor_context = acmag_runtime.anchor_context_text() if acmag_runtime.calibration_complete else ""
                    phase = "calibration" if acmag_runtime.is_calibration_submission(sub.id) else "main"
                    _broadcast_sse(session_id, {
                        "type": "progress",
                        "student": sub.student_identifier,
                        "graded_count": graded,
                        "failed_count": failed,
                        "total": total,
                        "stage": f"Vision + ACMAG {phase} grading {i+1}/{total}",
                    })

                    try:
                        acmag_pack = _run_coro_sync(asyncio.wait_for(grade_submission_acmag(
                            title=str(session.title),
                            description=str(session.description) if session.description else "",
                            rubric=rubric_text,
                            max_score=max_score_value,
                            student_files=student_files,
                            questions=questions,
                            student_identifier=str(sub.student_identifier),
                            anchor_context=anchor_context,
                            run_secondary=run_secondary,
                            moderation_delta=acmag_runtime.moderation_delta,
                        ), timeout=45))
                        result = dict(acmag_pack.get("result") or {})

                        if acmag_pack.get("moderation"):
                            _broadcast_sse(session_id, {
                                "type": "progress",
                                "student": sub.student_identifier,
                                "graded_count": graded,
                                "failed_count": failed,
                                "total": total,
                                "stage": f"Moderation review {i+1}/{total}",
                            })

                        if not result.get("error"):
                            acmag_runtime.register_anchor(sub.id, str(sub.student_identifier), result)
                            secondary_result = acmag_pack.get("secondary_result")
                            if (
                                acmag_pack.get("secondary_executed")
                                and isinstance(secondary_result, dict)
                                and not secondary_result.get("error")
                            ):
                                acmag_runtime.record_secondary_pair(
                                    primary=acmag_pack.get("primary_result") or result,
                                    secondary=secondary_result,
                                    from_calibration=acmag_runtime.is_calibration_submission(sub.id),
                                )
                            result.setdefault("acmag", {})
                            result["acmag"]["runtime"] = acmag_runtime.reliability_snapshot()

                        if acmag_runtime.halted:
                            stop_requested = True
                            _stop_grading_flags[session_id] = True
                            _broadcast_sse(session_id, {
                                "type": "acmag_halt",
                                "reason": acmag_runtime.halt_reason,
                                "reliability": acmag_runtime.reliability_snapshot(),
                            })
                    except asyncio.TimeoutError:
                        logger.warning(
                            f"ACMAG timed out for {sub.student_identifier}; falling back to standard grading"
                        )
                        result = _run_coro_sync(grade_student(
                            title=str(session.title),
                            description=str(session.description) if session.description else "",
                            rubric=rubric_text,
                            max_score=max_score_value,
                            student_files=student_files,
                            questions=questions,
                            reference_solution=getattr(session, "reference_solution", None) or None,
                            test_cases=getattr(session, "test_cases", None) or None,
                            run_command=getattr(session, "run_command", None) or None,
                            student_dir=str(student_dir) if student_dir else None,
                        ))
                else:
                    cached_result = _find_cached_result_by_hash(db, session_id, grading_hash, exclude_submission_id=sub.id)
                    if cached_result:
                        _broadcast_sse(session_id, {
                            "type": "progress",
                            "student": sub.student_identifier,
                            "graded_count": graded,
                            "failed_count": failed,
                            "total": total,
                            "stage": f"Deterministic cache reuse {i+1}/{total}",
                        })
                        result = cached_result
                    else:
                        _broadcast_sse(session_id, {
                            "type": "progress",
                            "student": sub.student_identifier,
                            "graded_count": graded,
                            "failed_count": failed,
                            "total": total,
                            "stage": f"Vision + grading {i+1}/{total}",
                        })

                        # Grade with LLM.
                        result = _run_coro_sync(grade_student(
                            title=str(session.title),
                            description=str(session.description) if session.description else "",
                            rubric=rubric_text,
                            max_score=max_score_value,
                            student_files=student_files,
                            questions=questions,
                            reference_solution=getattr(session, "reference_solution", None) or None,
                            test_cases=getattr(session, "test_cases", None) or None,
                            run_command=getattr(session, "run_command", None) or None,
                            student_dir=str(student_dir) if student_dir else None,
                        ))

                if isinstance(result, dict) and not result.get("grading_hash"):
                    result["grading_hash"] = grading_hash
                if isinstance(result, dict):
                    result["relevance_gate"] = relevance_gate
                if isinstance(result, dict):
                    result = _annotate_regrade_consistency(result, previous_ai_score)

                if result.get("error"):
                    sub.status = "error"
                    sub.error_message = result.get('error')
                    sub.ai_result = json.dumps(result)
                else:
                    sub.ai_result = json.dumps(result)
                    sub.ai_score = result.get("total_score")
                    sub.ai_letter_grade = result.get("letter_grade")
                    sub.ai_confidence = result.get("confidence")
                    sub.status = "graded"
                    sub.graded_at = datetime.now(timezone.utc)
                    if relevance_gate.get("block_grading") or relevance_gate.get("review_required"):
                        sub.is_flagged = True
                        if sub.flagged_by != "user":
                            sub.flag_reason = relevance_gate.get("reason") or "Relevance warnings require manual review"
                            sub.flagged_by = "system"
                            sub.flagged_at = datetime.now(timezone.utc)
                    elif sub.flagged_by == "system":
                        sub.is_flagged = False
                        sub.flag_reason = None
                        sub.flagged_by = None
                        sub.flagged_at = None

                graded = db.query(StudentSubmission).filter(
                    StudentSubmission.session_id == session_id,
                    StudentSubmission.status == "graded"
                ).count()
                failed = db.query(StudentSubmission).filter(
                    StudentSubmission.session_id == session_id,
                    StudentSubmission.status == "error"
                ).count()
                session.graded_count = graded
                session.error_count = failed
                db.commit()

                if sub.status == "error":
                    _broadcast_sse(session_id, {
                        "type": "student_error",
                        "student_id": sub.id,
                        "student_identifier": sub.student_identifier,
                        "error": sub.error_message or "Grading failed",
                        "graded_count": graded,
                        "failed_count": failed,
                        "total": total,
                        "result": result,
                        "ingestion_report": ingestion_report_dict,
                        "relevance": relevance,
                        "relevance_gate": relevance_gate,
                    })
                else:
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
                        "ingestion_report": ingestion_report_dict,
                        "relevance": relevance,
                        "relevance_gate": relevance_gate,
                    })

            except Exception as e:
                logger.exception(f"Failed to grade {sub.student_identifier}")
                sub.status = "error"
                sub.error_message = str(e)
                graded = db.query(StudentSubmission).filter(
                    StudentSubmission.session_id == session_id,
                    StudentSubmission.status == "graded"
                ).count()
                failed = db.query(StudentSubmission).filter(
                    StudentSubmission.session_id == session_id,
                    StudentSubmission.status == "error"
                ).count()
                session.graded_count = graded
                session.error_count = failed
                db.commit()

                _broadcast_sse(session_id, {
                    "type": "student_error",
                    "student_id": sub.id,
                    "student_identifier": sub.student_identifier,
                    "error": str(e),
                    "graded_count": graded,
                    "failed_count": failed,
                    "total": total,
                })

        graded = db.query(StudentSubmission).filter(
            StudentSubmission.session_id == session_id,
            StudentSubmission.status == "graded"
        ).count()
        failed = db.query(StudentSubmission).filter(
            StudentSubmission.session_id == session_id,
            StudentSubmission.status == "error"
        ).count()
        session.graded_count = graded
        session.error_count = failed

        if stop_requested:
            session.status = "paused"
            session.completed_at = datetime.now(timezone.utc)
            if session_id in _active_grading:
                _active_grading[session_id].update({
                    "status": "stopped",
                    "current_student": "",
                    "graded_count": graded,
                    "failed_count": failed,
                    "stage": "Stopped by user",
                })
            db.commit()
            _broadcast_sse(session_id, {
                "type": "stopped",
                "graded_count": graded,
                "failed_count": failed,
                "total": total,
                "message": f"Grading stopped: {graded} graded, {failed} failed",
            })
            return

        session.status = "completed" if failed == 0 else "completed_with_errors"
        session.completed_at = datetime.now(timezone.utc)
        db.commit()

        if session_id in _active_grading:
            _active_grading[session_id].update({
                "status": "completed",
                "current_student": "",
                "graded_count": graded,
                "failed_count": failed,
                "stage": "Complete",
            })

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
            if session is not None:
                session.status = "failed"
                session.completed_at = datetime.now(timezone.utc)
                db.commit()
        except:
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
        _stop_grading_flags.pop(session_id, None)
        def _cleanup():
            import time
            time.sleep(5)
            _active_grading.pop(session_id, None)
            _sse_queues.pop(session_id, None)
        threading.Thread(target=_cleanup, daemon=True).start()


@app.get("/session/{session_id}/grade-stream")
async def grade_stream(session_id: int):
    """Server-Sent Events endpoint for real-time grading progress."""
    client_queue: Queue = Queue()
    if session_id not in _sse_queues:
        _sse_queues[session_id] = []
    _sse_queues[session_id].append(client_queue)

    async def event_generator():
        idle_count = 0
        try:
            while True:
                try:
                    event = client_queue.get_nowait()
                    idle_count = 0
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                    if event.get("type") in {"complete", "stopped"}:
                        return
                except Empty:
                    pass

                progress = _active_grading.get(session_id)
                if progress is None:
                    db = SessionLocal()
                    try:
                        session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
                        if session and session.status in ("completed", "completed_with_errors", "failed", "paused"):
                            event_type = "stopped" if session.status == "paused" else "complete"
                            message = (
                                "Grading paused"
                                if session.status == "paused"
                                else f"Grading finished: {session.graded_count} graded"
                            )
                            data = {
                                "type": event_type,
                                "graded_count": session.graded_count or 0,
                                "failed_count": session.error_count or 0,
                                "total": session.total_students or 0,
                                "message": message,
                            }
                            yield f"data: {json.dumps(data)}\n\n"
                            return
                    finally:
                        db.close()
                    idle_count += 1
                    if idle_count > 120:
                        yield f"data: {json.dumps({'type': 'timeout', 'message': 'Stream idle timeout'})}\n\n"
                        return

                await asyncio.sleep(0.3)
        finally:
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
    
    progress = _active_grading.get(session_id, {})
    total_students = session.total_students or 0

    # Derive live counts from submissions so status polling stays accurate even if
    # a background worker has not yet synchronized session-level counters.
    live_graded = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id,
        StudentSubmission.status == "graded",
    ).count()
    live_failed = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id,
        StudentSubmission.status == "error",
    ).count()

    graded_count = max(
        int(progress.get("graded_count", 0) or 0),
        int(session.graded_count or 0),
        int(live_graded or 0),
    )
    failed_count = max(
        int(progress.get("failed_count", 0) or 0),
        int(session.error_count or 0),
        int(live_failed or 0),
    )

    return {
        "id": session.id,
        "session_id": session.id,
        "title": session.title or "",
        "description": session.description or "",
        "rubric": session.rubric or "",
        "max_score": session.max_score or 100,
        "status": progress.get("status", session.status),
        "total_students": total_students,
        "graded_count": graded_count,
        "error_count": failed_count,
        "failed_count": failed_count,
        "current_student": progress.get("current_student", ""),
        "progress_percentage": (graded_count / total_students * 100) if total_students > 0 else 0,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
    }


@app.get("/api/session/{session_id}/students")
def api_list_students(session_id: int, db: Session = Depends(get_db)):
    """JSON API: list all student submissions for a session."""
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    submissions = (
        db.query(StudentSubmission)
        .filter(StudentSubmission.session_id == session_id)
        .order_by(StudentSubmission.student_identifier)
        .all()
    )

    result = []
    for sub in submissions:
        ai_result = None
        rubric_breakdown = []
        strengths = []
        weaknesses = []
        ai_feedback = ""
        suggestions = ""
        critical_errors = []

        if sub.ai_result:
            try:
                ai_result = json.loads(sub.ai_result) if isinstance(sub.ai_result, str) else sub.ai_result
                if isinstance(ai_result, dict):
                    rubric_breakdown = ai_result.get("rubric_breakdown", [])
                    strengths = ai_result.get("strengths", [])
                    weaknesses = ai_result.get("weaknesses", [])
                    ai_feedback = ai_result.get("overall_feedback", "") or ai_result.get("feedback", "")
                    suggestions = ai_result.get("suggestions_for_improvement", "")
                    critical_errors = ai_result.get("critical_errors", [])
            except (json.JSONDecodeError, TypeError):
                pass

        final_score = sub.override_score if (sub.is_overridden and sub.override_score is not None) else sub.ai_score

        files = []
        if sub.files:
            try:
                files = json.loads(sub.files) if isinstance(sub.files, str) else sub.files
            except (json.JSONDecodeError, TypeError):
                pass

        result.append({
            "id": sub.id,
            "session_id": sub.session_id,
            "student_identifier": sub.student_identifier,
            "status": sub.status or "pending",
            "file_count": sub.file_count or 0,
            "ai_score": sub.ai_score,
            "ai_letter_grade": sub.ai_letter_grade or "",
            "ai_confidence": sub.ai_confidence or "",
            "final_score": final_score,
            "is_overridden": bool(sub.is_overridden),
            "override_score": sub.override_score,
            "override_comments": sub.override_comments or "",
            "is_reviewed": bool(sub.is_reviewed),
            "tests_passed": sub.tests_passed or 0,
            "tests_total": sub.tests_total or 0,
            "graded_at": sub.graded_at.isoformat() if sub.graded_at else None,
            "error_message": sub.error_message or "",
            "files": files,
            "ai_result": ai_result,
            "ai_feedback": ai_feedback,
            "rubric_breakdown": rubric_breakdown,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "critical_errors": critical_errors,
            "suggestions_for_improvement": suggestions,
            "is_flagged": bool(sub.is_flagged),
            "flag_reason": sub.flag_reason or "",
        })

    return result


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
    
    grade_dist = _compute_grade_distribution(submissions)
    tables = _build_results_tables(submissions, int(session.max_score or 100))
    
    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "request": request,
            "session": session,
            "submissions": submissions,
            "stats": stats,
            "grade_dist": grade_dist,
            "marks_rows": tables["marks_rows"],
            "rubric_rows": tables["rubric_rows"],
            "ingestion_rows": tables["ingestion_rows"],
            "ingestion_overview": tables["ingestion_overview"],
            "llm_rows": tables["llm_rows"],
            "llm_overview": tables["llm_overview"],
        },
    )


@app.get("/session/{session_id}/export/csv")
def export_csv_endpoint(session_id: int, db: Session = Depends(get_db)):
    try:
        csv_content = export_csv(db, session_id)
        return PlainTextResponse(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=session_{session_id}_results.csv"}
        )
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
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
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))
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
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if payload.score > session.max_score:
        raise HTTPException(
            status_code=422,
            detail=f"Score cannot exceed session max score ({session.max_score})",
        )

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
    
    # Clean up in-memory grading state to prevent orphan threads and memory leaks
    _stop_grading_flags[session_id] = True  # Signal any running grading to stop
    _active_grading.pop(session_id, None)
    _sse_queues.pop(session_id, None)
    _stop_grading_flags.pop(session_id, None)
    
    import shutil
    session_dir = UPLOAD_DIR / str(session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir)
    
    db.delete(session)
    db.commit()
    
    return RedirectResponse(url="/", status_code=303)


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

    session_dir = UPLOAD_DIR / str(session_id)
    
    possible_dirs = [
        session_dir / sub.student_identifier,
        session_dir / "_master" / sub.student_identifier,
    ]
    
    student_dir = None
    for d in possible_dirs:
        if d.exists() and d.is_dir():
            student_dir = d
            break

    result = []
    for f in file_meta:
        rel_path = f.get('relative_path', f.get('filename', ''))
        ext = Path(rel_path).suffix.lower()

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

        file_path = student_dir / rel_path if student_dir else None
        exists = file_path.exists() if file_path else False
        
        encoded_path = urllib.parse.quote(rel_path, safe='')
        view_url = f"/session/{session_id}/student/{student_id}/file/{encoded_path}" if exists else None

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
    
    decoded_file_path = urllib.parse.unquote(file_path)
    
    possible_dirs = [
        session_dir / sub.student_identifier,
        session_dir / "_master" / sub.student_identifier,
    ]
    
    student_dir = None
    for d in possible_dirs:
        if d.exists() and d.is_dir():
            student_dir = d
            break
    
    if not student_dir:
        raise HTTPException(status_code=404, detail="Student directory not found")

    full_path = student_dir / decoded_file_path

    try:
        full_path = full_path.resolve()
        student_dir_resolved = student_dir.resolve()
        if not str(full_path).startswith(str(student_dir_resolved)):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=403, detail="Invalid path")

    if not full_path.exists():
        logger.warning(f"File not found: {full_path}")
        raise HTTPException(status_code=404, detail="File not found")

    ext = full_path.suffix.lower()

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

    if ext == ".docx":
        try:
            from app.services.file_parser import _parse_docx
            result = _parse_docx(full_path)
            return PlainTextResponse(content=result.get("content", ""), media_type="text/plain; charset=utf-8")
        except Exception:
            pass

    media_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
    return FileResponse(str(full_path), media_type=media_type)


@app.get("/health")
def health_check():
    return {"status": "healthy", "version": "5.0.0"}
