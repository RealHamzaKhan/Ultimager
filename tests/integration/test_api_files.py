"""Integration tests for file-serving and export endpoints."""
import csv
import io
import json
from datetime import datetime, timezone

import pytest

from app.models import GradingSession, StudentSubmission


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_completed_session(db_session):
    """Create a completed session with one graded submission that has ai_result."""
    ai_result = {
        "rubric_breakdown": [
            {"criterion": "Code Quality", "score": 35, "max": 40, "justification": "Good"},
            {"criterion": "Documentation", "score": 25, "max": 30, "justification": "Ok"},
            {"criterion": "Testing", "score": 20, "max": 30, "justification": "Needs work"},
        ],
        "total_score": 80,
        "overall_feedback": "Solid submission.",
        "strengths": ["Clean code"],
        "weaknesses": ["Few tests"],
        "suggestions_for_improvement": "Add more tests.",
        "confidence": "high",
        "confidence_reasoning": "Clear.",
        "grading_hash": "hash123",
    }

    session = GradingSession(
        title="Export Test",
        description="For export testing",
        rubric="Code Quality (40)\nDocumentation (30)\nTesting (30)",
        max_score=100,
        status="completed",
        total_students=1,
        graded_count=1,
        error_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    sub = StudentSubmission(
        session_id=session.id,
        student_identifier="export_student",
        files=json.dumps([{"filename": "main.py", "type": "code"}]),
        file_count=1,
        status="graded",
        ai_result=json.dumps(ai_result),
        ai_score=80.0,
        ai_letter_grade="B",
        ai_confidence="high",
        graded_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return session.id, sub.id


# ---------------------------------------------------------------------------
# Tests: CSV export
# ---------------------------------------------------------------------------

class TestExportCSV:
    """GET /session/{id}/export/csv"""

    def test_csv_export_returns_csv(self, test_client, db_session):
        sid, _ = _seed_completed_session(db_session)
        resp = test_client.get(f"/session/{sid}/export/csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        content = resp.text
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        # Header + at least 1 data row
        assert len(rows) >= 2
        header = rows[0]
        assert "Student ID" in header
        assert "AI Score" in header

    def test_csv_contains_student_data(self, test_client, db_session):
        sid, _ = _seed_completed_session(db_session)
        resp = test_client.get(f"/session/{sid}/export/csv")
        assert "export_student" in resp.text

    def test_csv_nonexistent_session_returns_404(self, test_client):
        """Exporter raises ValueError for missing session, route returns 404."""
        resp = test_client.get("/session/99999/export/csv")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: JSON export
# ---------------------------------------------------------------------------

class TestExportJSON:
    """GET /session/{id}/export/json"""

    def test_json_export_returns_valid_json(self, test_client, db_session):
        sid, _ = _seed_completed_session(db_session)
        resp = test_client.get(f"/session/{sid}/export/json")
        assert resp.status_code == 200
        body = resp.json()
        assert "session" in body
        assert "students" in body

    def test_json_export_includes_student_data(self, test_client, db_session):
        sid, _ = _seed_completed_session(db_session)
        resp = test_client.get(f"/session/{sid}/export/json")
        body = resp.json()
        assert len(body["students"]) >= 1
        student = body["students"][0]
        assert student["student_identifier"] == "export_student"
        assert student["ai_score"] == 80.0
        assert "ai_result" in student

    def test_json_export_session_metadata(self, test_client, db_session):
        sid, _ = _seed_completed_session(db_session)
        resp = test_client.get(f"/session/{sid}/export/json")
        meta = resp.json()["session"]
        assert meta["title"] == "Export Test"
        assert meta["max_score"] == 100

    def test_json_nonexistent_session_returns_404(self, test_client):
        resp = test_client.get("/session/99999/export/json")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: results page
# ---------------------------------------------------------------------------

class TestResultsPage:
    """GET /session/{id}/results"""

    def test_results_page_renders_html(self, test_client, db_session):
        sid, _ = _seed_completed_session(db_session)
        resp = test_client.get(f"/session/{sid}/results")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_results_nonexistent_session_returns_404(self, test_client):
        resp = test_client.get("/session/99999/results")
        assert resp.status_code == 404
