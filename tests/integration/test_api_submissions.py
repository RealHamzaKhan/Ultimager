"""Integration tests for submission-related endpoints."""
import json
from datetime import datetime, timezone

import pytest

from app.models import GradingSession, StudentSubmission


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_session_and_submission(db_session, *, status="graded", ai_score=80.0, ai_result=None):
    """Insert a session + submission directly into the test DB and return their ids."""
    session = GradingSession(
        title="Sub Test",
        description="",
        rubric="Quality (100)",
        max_score=100,
        status="completed",
        total_students=1,
        graded_count=1 if status == "graded" else 0,
        error_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    if ai_result is None:
        ai_result = json.dumps({
            "rubric_breakdown": [{"criterion": "Quality", "score": 80, "max": 100, "justification": "ok"}],
            "total_score": ai_score,
            "overall_feedback": "Good.",
            "strengths": ["Nice"],
            "weaknesses": [],
            "suggestions_for_improvement": "",
            "confidence": "high",
            "confidence_reasoning": "clear",
        })

    sub = StudentSubmission(
        session_id=session.id,
        student_identifier="alice",
        files=json.dumps([{"filename": "main.py", "type": "code"}]),
        file_count=1,
        status=status,
        ai_result=ai_result if status == "graded" else None,
        ai_score=ai_score if status == "graded" else None,
        ai_letter_grade="B" if status == "graded" else None,
        ai_confidence="high" if status == "graded" else None,
        graded_at=datetime.now(timezone.utc) if status == "graded" else None,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return session.id, sub.id


# ---------------------------------------------------------------------------
# Tests: file listing
# ---------------------------------------------------------------------------

class TestStudentFiles:
    """GET /session/{id}/student/{sid}/files"""

    def test_returns_file_list(self, test_client, db_session):
        sid, subid = _create_session_and_submission(db_session)
        resp = test_client.get(f"/session/{sid}/student/{subid}/files")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        assert body[0]["filename"] == "main.py"

    def test_nonexistent_student_returns_404(self, test_client, db_session):
        sid, _ = _create_session_and_submission(db_session)
        resp = test_client.get(f"/session/{sid}/student/99999/files")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: flagging
# ---------------------------------------------------------------------------

class TestFlagUnflag:
    """POST /session/{id}/student/{sid}/flag and /unflag"""

    def test_flag_student(self, test_client, db_session):
        sid, subid = _create_session_and_submission(db_session)
        resp = test_client.post(
            f"/session/{sid}/student/{subid}/flag",
            data={"reason": "Suspected plagiarism"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["message"] == "Submission flagged"
        assert body["student_id"] == subid

    def test_unflag_student(self, test_client, db_session):
        sid, subid = _create_session_and_submission(db_session)
        # Flag first
        test_client.post(f"/session/{sid}/student/{subid}/flag", data={"reason": "test"})
        # Then unflag
        resp = test_client.post(f"/session/{sid}/student/{subid}/unflag")
        assert resp.status_code == 200
        assert resp.json()["message"] == "Flag removed"

    def test_flag_nonexistent_student_returns_404(self, test_client, db_session):
        sid, _ = _create_session_and_submission(db_session)
        resp = test_client.post(
            f"/session/{sid}/student/99999/flag",
            data={"reason": "ghost"},
        )
        assert resp.status_code == 404

    def test_unflag_nonexistent_student_returns_404(self, test_client, db_session):
        sid, _ = _create_session_and_submission(db_session)
        resp = test_client.post(f"/session/{sid}/student/99999/unflag")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: override
# ---------------------------------------------------------------------------

class TestOverrideGrade:
    """POST /session/{id}/student/{sid}/override"""

    def test_valid_override(self, test_client, db_session):
        sid, subid = _create_session_and_submission(db_session)
        resp = test_client.post(
            f"/session/{sid}/student/{subid}/override",
            json={"score": 95, "comments": "Excellent work", "is_reviewed": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["message"] == "Override saved"
        assert body["final_score"] == 95

    def test_score_exceeds_max_returns_422(self, test_client, db_session):
        sid, subid = _create_session_and_submission(db_session)
        resp = test_client.post(
            f"/session/{sid}/student/{subid}/override",
            json={"score": 150, "comments": "too high"},
        )
        assert resp.status_code == 422

    def test_override_nonexistent_student_returns_404(self, test_client, db_session):
        sid, _ = _create_session_and_submission(db_session)
        resp = test_client.post(
            f"/session/{sid}/student/99999/override",
            json={"score": 50},
        )
        assert resp.status_code == 404

    def test_override_nonexistent_session_returns_404(self, test_client, db_session):
        resp = test_client.post(
            "/session/99999/student/1/override",
            json={"score": 50},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: retry-failed
# ---------------------------------------------------------------------------

class TestRetryFailed:
    """POST /session/{id}/retry-failed"""

    def test_resets_error_submissions(self, test_client, db_session):
        sid, subid = _create_session_and_submission(db_session, status="error", ai_score=None)
        resp = test_client.post(f"/session/{sid}/retry-failed")
        assert resp.status_code == 200
        body = resp.json()
        assert body["reset_count"] >= 1

    def test_no_errors_resets_zero(self, test_client, db_session):
        sid, _ = _create_session_and_submission(db_session, status="graded")
        resp = test_client.post(f"/session/{sid}/retry-failed")
        assert resp.status_code == 200
        assert resp.json()["reset_count"] == 0

    def test_nonexistent_session_returns_404(self, test_client):
        resp = test_client.post("/session/99999/retry-failed")
        assert resp.status_code == 404
