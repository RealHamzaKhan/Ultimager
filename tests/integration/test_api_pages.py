"""Tests for HTML page endpoints and additional API routes."""
import json
from datetime import datetime, timezone

import pytest
from app.models import GradingSession, StudentSubmission


# ── Helper ──────────────────────────────────────────────────────

def _create_session_with_students(db_session, status="complete", num_graded=3, num_error=0):
    """Create a session with graded students for testing."""
    session = GradingSession(
        title="Test Session",
        description="Testing",
        rubric="Code Quality (50): Correctness\nDocumentation (50): Comments",
        max_score=100,
        status=status,
        total_students=num_graded + num_error,
        graded_count=num_graded,
        error_count=num_error,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    subs = []
    for i in range(num_graded):
        ai_result = {
            "rubric_breakdown": [
                {"criterion": "Code Quality", "score": 40 - i * 5, "max": 50, "justification": "Good"},
                {"criterion": "Documentation", "score": 40 - i * 3, "max": 50, "justification": "OK"},
            ],
            "total_score": (40 - i * 5) + (40 - i * 3),
            "overall_feedback": f"Student {i} feedback",
            "strengths": ["Good"],
            "weaknesses": ["Improve"],
            "confidence": "high",
            "confidence_reasoning": "Clear",
            "grading_hash": f"hash_{i}",
            "transparency": {
                "llm_call": {
                    "model": "test-model",
                    "provider": "nvidia",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                    "fallback_used": False,
                },
                "text_chars_sent": 500,
                "images_sent": 0,
            },
        }
        sub = StudentSubmission(
            session_id=session.id,
            student_identifier=f"student_{i}",
            files=json.dumps([{"filename": f"solution_{i}.py", "type": "code"}]),
            file_count=1,
            status="graded",
            ai_result=json.dumps(ai_result),
            ai_score=float(ai_result["total_score"]),
            ai_letter_grade="A" if ai_result["total_score"] >= 90 else "B",
            ai_confidence="high",
            graded_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(sub)
        subs.append(sub)

    for i in range(num_error):
        sub = StudentSubmission(
            session_id=session.id,
            student_identifier=f"error_student_{i}",
            files=json.dumps([]),
            file_count=0,
            status="error",
            error_message="LLM timeout",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(sub)
        subs.append(sub)

    db_session.commit()
    for s in subs:
        db_session.refresh(s)
    return session, subs


# ── HTML Page Tests ─────────────────────────────────────────────

class TestHomePage:
    def test_home_page_renders(self, test_client):
        resp = test_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_home_page_with_sessions(self, test_client, db_session):
        _create_session_with_students(db_session)
        resp = test_client.get("/")
        assert resp.status_code == 200


class TestNewSessionPage:
    def test_new_session_form_renders(self, test_client):
        resp = test_client.get("/session/new")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")


class TestSessionDetailPage:
    def test_session_detail_renders(self, test_client, db_session):
        session, _ = _create_session_with_students(db_session)
        resp = test_client.get(f"/session/{session.id}")
        assert resp.status_code == 200

    def test_session_detail_nonexistent(self, test_client):
        resp = test_client.get("/session/99999")
        assert resp.status_code == 404


class TestResultsPage:
    def test_results_page_renders(self, test_client, db_session):
        session, _ = _create_session_with_students(db_session)
        resp = test_client.get(f"/session/{session.id}/results")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_results_page_nonexistent(self, test_client):
        resp = test_client.get("/session/99999/results")
        assert resp.status_code == 404

    def test_results_page_with_errors(self, test_client, db_session):
        session, _ = _create_session_with_students(db_session, num_graded=2, num_error=1)
        resp = test_client.get(f"/session/{session.id}/results")
        assert resp.status_code == 200


# ── Ingestion Report Tests ──────────────────────────────────────

class TestIngestionReport:
    def test_ingestion_report_with_data(self, test_client, db_session):
        session, subs = _create_session_with_students(db_session)
        # Add ingestion report to first student
        sub = subs[0]
        sub.ingestion_report = json.dumps({
            "files_received": 2,
            "files_parsed": 2,
            "files_failed": 0,
            "warnings": [],
            "errors": [],
        })
        db_session.commit()

        resp = test_client.get(f"/session/{session.id}/student/{sub.id}/ingestion-report")
        assert resp.status_code == 200

    def test_ingestion_report_no_data(self, test_client, db_session):
        session, subs = _create_session_with_students(db_session)
        resp = test_client.get(f"/session/{session.id}/student/{subs[0].id}/ingestion-report")
        assert resp.status_code in (200, 404)

    def test_ingestion_report_nonexistent_student(self, test_client, db_session):
        session, _ = _create_session_with_students(db_session)
        resp = test_client.get(f"/session/{session.id}/student/99999/ingestion-report")
        assert resp.status_code == 404


# ── Student Files Tests ─────────────────────────────────────────

class TestStudentFiles:
    def test_list_student_files(self, test_client, db_session):
        session, subs = _create_session_with_students(db_session)
        resp = test_client.get(f"/session/{session.id}/student/{subs[0].id}/files")
        assert resp.status_code == 200

    def test_list_files_nonexistent_student(self, test_client, db_session):
        session, _ = _create_session_with_students(db_session)
        resp = test_client.get(f"/session/{session.id}/student/99999/files")
        assert resp.status_code == 404


# ── Status API Tests ────────────────────────────────────────────

class TestStatusAPI:
    def test_status_complete(self, test_client, db_session):
        session, _ = _create_session_with_students(db_session, status="complete")
        resp = test_client.get(f"/session/{session.id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "complete"

    def test_status_grading(self, test_client, db_session):
        session, _ = _create_session_with_students(db_session, status="grading")
        resp = test_client.get(f"/session/{session.id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "grading"


# ── Health Check Tests ──────────────────────────────────────────

class TestHealthCheck:
    def test_health_returns_200(self, test_client):
        resp = test_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "healthy" or "status" in data


# ── Start Grading Tests ────────────────────────────────────────

class TestStartGrading:
    def test_start_grading_no_submissions(self, test_client, db_session):
        session = GradingSession(
            title="Empty Session",
            description="No students",
            rubric="Code (100): Quality",
            max_score=100,
            status="pending",
            total_students=0,
            graded_count=0,
            error_count=0,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)

        resp = test_client.post(f"/session/{session.id}/grade")
        # Should either start or reject with no students
        assert resp.status_code in (200, 302, 303, 400, 422)

    def test_start_grading_nonexistent(self, test_client):
        resp = test_client.post("/session/99999/grade")
        assert resp.status_code in (404, 302)


# ── Stop Grading Tests ─────────────────────────────────────────

class TestStopGrading:
    def test_stop_grading_nonexistent(self, test_client):
        resp = test_client.post("/session/99999/stop-grading")
        assert resp.status_code in (404, 302, 400)

    def test_stop_grading_not_active(self, test_client, db_session):
        session, _ = _create_session_with_students(db_session, status="complete")
        resp = test_client.post(f"/session/{session.id}/stop-grading")
        # Not actively grading
        assert resp.status_code in (200, 302, 400)


# ── Regrade Tests ───────────────────────────────────────────────

class TestRegradeAll:
    def test_regrade_all_nonexistent(self, test_client):
        resp = test_client.post("/session/99999/regrade-all")
        assert resp.status_code in (404, 302)


class TestRegradeStudent:
    def test_regrade_student_nonexistent(self, test_client):
        resp = test_client.post("/session/99999/student/99999/regrade")
        assert resp.status_code in (404, 302)


# ── Delete Session Tests ────────────────────────────────────────

class TestDeleteSession:
    def test_delete_existing_session(self, test_client, db_session):
        session, _ = _create_session_with_students(db_session)
        resp = test_client.post(f"/session/{session.id}/delete")
        assert resp.status_code in (200, 302, 303)

    def test_delete_nonexistent_session(self, test_client):
        resp = test_client.post("/session/99999/delete")
        assert resp.status_code in (404, 302)
