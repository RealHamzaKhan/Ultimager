"""Integration tests for grading control endpoints.

All external LLM calls are mocked so these tests never reach a real provider.
"""
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from app.main import _active_grading, _stop_grading_flags, _sse_queues
from app.models import GradingSession, StudentSubmission


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_grading_globals():
    """Ensure module-level grading state is clean before and after each test."""
    _active_grading.clear()
    _stop_grading_flags.clear()
    _sse_queues.clear()
    yield
    _active_grading.clear()
    _stop_grading_flags.clear()
    _sse_queues.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_session_with_submission(db_session, *, status="pending"):
    """Create a session with one pending submission."""
    session = GradingSession(
        title="Grading Test",
        description="",
        rubric="Quality (100)",
        max_score=100,
        status="pending",
        total_students=1,
        graded_count=0,
        error_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    sub = StudentSubmission(
        session_id=session.id,
        student_identifier="student_a",
        files=json.dumps([{"filename": "hw.py", "type": "code"}]),
        file_count=1,
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return session.id, sub.id


# ---------------------------------------------------------------------------
# Tests: start grading
# ---------------------------------------------------------------------------

class TestStartGrading:
    """POST /session/{id}/grade"""

    @patch("app.main.asyncio")
    def test_starts_grading_returns_json(self, mock_asyncio, test_client, db_session):
        """Grading should kick off and return a JSON message."""
        mock_loop = MagicMock()
        mock_asyncio.get_event_loop.return_value = mock_loop
        mock_loop.run_in_executor.return_value = None

        sid, _ = _seed_session_with_submission(db_session)
        resp = test_client.post(f"/session/{sid}/grade")
        assert resp.status_code == 200
        body = resp.json()
        assert "message" in body
        assert body["session_id"] == sid

    def test_nonexistent_session_returns_404(self, test_client):
        resp = test_client.post("/session/99999/grade")
        assert resp.status_code == 404

    @patch("app.main.asyncio")
    def test_already_grading_returns_already_message(self, mock_asyncio, test_client, db_session):
        mock_loop = MagicMock()
        mock_asyncio.get_event_loop.return_value = mock_loop
        mock_loop.run_in_executor.return_value = None

        sid, _ = _seed_session_with_submission(db_session)
        # First call starts grading
        test_client.post(f"/session/{sid}/grade")
        # Second call should say already grading
        resp = test_client.post(f"/session/{sid}/grade")
        assert resp.status_code == 200
        assert "Already grading" in resp.json()["message"]


# ---------------------------------------------------------------------------
# Tests: stop grading
# ---------------------------------------------------------------------------

class TestStopGrading:
    """POST /session/{id}/stop-grading"""

    def test_not_grading_returns_not_currently(self, test_client, db_session):
        sid, _ = _seed_session_with_submission(db_session)
        resp = test_client.post(f"/session/{sid}/stop-grading")
        assert resp.status_code == 200
        assert "Not currently grading" in resp.json()["message"]

    @patch("app.main.asyncio")
    def test_stop_while_grading(self, mock_asyncio, test_client, db_session):
        mock_loop = MagicMock()
        mock_asyncio.get_event_loop.return_value = mock_loop
        mock_loop.run_in_executor.return_value = None

        sid, _ = _seed_session_with_submission(db_session)
        test_client.post(f"/session/{sid}/grade")
        resp = test_client.post(f"/session/{sid}/stop-grading")
        assert resp.status_code == 200
        assert "stopped" in resp.json()["message"].lower()

    def test_nonexistent_session_returns_404(self, test_client):
        resp = test_client.post("/session/99999/stop-grading")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: regrade all
# ---------------------------------------------------------------------------

class TestRegradeAll:
    """POST /session/{id}/regrade-all"""

    @patch("app.main.asyncio")
    def test_regrade_all_resets_and_starts(self, mock_asyncio, test_client, db_session):
        mock_loop = MagicMock()
        mock_asyncio.get_event_loop.return_value = mock_loop
        mock_loop.run_in_executor.return_value = None

        sid, _ = _seed_session_with_submission(db_session, status="graded")
        resp = test_client.post(f"/session/{sid}/regrade-all")
        assert resp.status_code == 200
        body = resp.json()
        assert "message" in body

    def test_nonexistent_session_returns_404(self, test_client):
        resp = test_client.post("/session/99999/regrade-all")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: regrade single student
# ---------------------------------------------------------------------------

class TestRegradeStudent:
    """POST /session/{id}/student/{sid}/regrade"""

    @patch("app.main.asyncio")
    def test_regrade_single_student(self, mock_asyncio, test_client, db_session):
        mock_loop = MagicMock()
        mock_asyncio.get_event_loop.return_value = mock_loop
        mock_loop.run_in_executor.return_value = None

        sid, subid = _seed_session_with_submission(db_session, status="graded")
        resp = test_client.post(f"/session/{sid}/student/{subid}/regrade")
        assert resp.status_code == 200
        body = resp.json()
        assert body["student_id"] == subid

    def test_nonexistent_session_returns_404(self, test_client):
        resp = test_client.post("/session/99999/student/1/regrade")
        assert resp.status_code == 404

    def test_nonexistent_student_returns_404(self, test_client, db_session):
        sid, _ = _seed_session_with_submission(db_session)
        resp = test_client.post(f"/session/{sid}/student/99999/regrade")
        assert resp.status_code == 404

    @patch("app.main.asyncio")
    def test_regrade_blocked_while_grading(self, mock_asyncio, test_client, db_session):
        """A single-student regrade should be rejected while a full grading run is active."""
        mock_loop = MagicMock()
        mock_asyncio.get_event_loop.return_value = mock_loop
        mock_loop.run_in_executor.return_value = None

        sid, subid = _seed_session_with_submission(db_session)
        # Start a full grading run
        test_client.post(f"/session/{sid}/grade")
        # Attempt single regrade
        resp = test_client.post(f"/session/{sid}/student/{subid}/regrade")
        assert resp.status_code == 409
