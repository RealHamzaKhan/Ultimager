"""Export grading results as CSV or JSON."""
from __future__ import annotations

import csv
import io
import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import GradingSession, StudentSubmission


def export_csv(db: "Session", session_id: int, include_pending: bool = False) -> str:
    """Return CSV string with one row per student."""
    from app.models import GradingSession, StudentSubmission
    
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise ValueError(f"Session {session_id} not found")
    
    # Build query
    query = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id
    )
    
    # Filter out pending if not requested
    if not include_pending:
        query = query.filter(StudentSubmission.status.in_(["graded", "error", "overridden"]))
    
    submissions = query.all()
    
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    header = [
        "Student ID", "AI Score", "Override Score", "Final Score",
        "Letter Grade", "Confidence", "Status", "Is Reviewed",
        "Is Overridden", "Graded At", "Feedback Summary",
    ]

    # Try to extract rubric criteria from the first successful submission
    rubric_criteria: list[str] = []
    for sub in submissions:
        if sub.ai_result:
            try:
                result = json.loads(sub.ai_result)
                breakdown = result.get("rubric_breakdown", [])
                rubric_criteria = [item["criterion"] for item in breakdown]
                break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    header.extend(rubric_criteria)
    header.append("Override Comments")
    writer.writerow(header)

    # Rows
    for sub in submissions:
        row: list[Any] = [
            sub.student_identifier,
            sub.ai_score,
            sub.override_score,
            sub.final_score,
            sub.ai_letter_grade or "",
            sub.ai_confidence or "",
            sub.status,
            sub.is_reviewed,
            sub.is_overridden,
            sub.graded_at.strftime("%Y-%m-%d %H:%M:%S") if sub.graded_at else "",
        ]

        # Feedback summary
        feedback = ""
        criterion_scores: dict[str, str] = {}
        if sub.ai_result:
            try:
                result = json.loads(sub.ai_result)
                feedback = result.get("overall_feedback", "")
                for item in result.get("rubric_breakdown", []):
                    criterion_scores[item["criterion"]] = f"{item['score']}/{item['max']}"
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        row.append(feedback)

        # Add rubric scores in order
        for crit in rubric_criteria:
            row.append(criterion_scores.get(crit, ""))

        row.append(sub.override_comments or "")
        writer.writerow(row)

    return output.getvalue()


def export_json(db: "Session", session_id: int, include_pending: bool = False) -> str:
    """Return a JSON string with full grading data."""
    from app.models import GradingSession, StudentSubmission
    
    session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
    if not session:
        raise ValueError(f"Session {session_id} not found")
    
    # Build query
    query = db.query(StudentSubmission).filter(
        StudentSubmission.session_id == session_id
    )
    
    # Filter out pending if not requested
    if not include_pending:
        query = query.filter(StudentSubmission.status.in_(["graded", "error", "overridden"]))
    
    submissions = query.all()
    
    data = {
        "session": {
            "id": session.id,
            "title": session.title,
            "description": session.description,
            "rubric": session.rubric,
            "max_score": session.max_score,
            "status": session.status,
            "total_students": session.total_students,
            "graded_count": session.graded_count,
            "created_at": session.created_at.isoformat() if session.created_at else None,
        },
        "students": [],
    }

    for sub in submissions:
        student_data: dict[str, Any] = {
            "student_identifier": sub.student_identifier,
            "status": sub.status,
            "ai_score": sub.ai_score,
            "ai_letter_grade": sub.ai_letter_grade,
            "ai_confidence": sub.ai_confidence,
            "final_score": sub.final_score,
            "is_overridden": sub.is_overridden,
            "override_score": sub.override_score,
            "override_comments": sub.override_comments,
            "is_reviewed": sub.is_reviewed,
            "graded_at": sub.graded_at.isoformat() if sub.graded_at else None,
            "files": [],
        }

        if sub.files:
            try:
                student_data["files"] = json.loads(sub.files)
            except (json.JSONDecodeError, TypeError):
                student_data["files"] = []

        if sub.ai_result:
            try:
                student_data["ai_result"] = json.loads(sub.ai_result)
            except (json.JSONDecodeError, TypeError):
                student_data["ai_result"] = sub.ai_result

        if sub.test_results:
            try:
                student_data["test_results"] = json.loads(sub.test_results)
            except (json.JSONDecodeError, TypeError):
                student_data["test_results"] = sub.test_results

        data["students"].append(student_data)

    return json.dumps(data, indent=2, default=str)
