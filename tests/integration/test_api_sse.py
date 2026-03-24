"""Integration tests for SSE and status endpoints."""
import json
import threading
import time
from datetime import datetime, timezone
from queue import Queue

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

def _seed_session(db_session, *, status="pending", total=1, graded=0):
    session = GradingSession(
        title="SSE Test",
        description="",
        rubric="Quality (100)",
        max_score=100,
        status=status,
        total_students=total,
        graded_count=graded,
        error_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session.id


# ---------------------------------------------------------------------------
# Tests: SSE stream
# ---------------------------------------------------------------------------

class TestGradeStream:
    """GET /session/{id}/grade-stream

    The SSE endpoint uses an internal async generator with asyncio.sleep
    loops, making it difficult to test with the synchronous TestClient.
    We verify the response content-type and ensure a "complete" event is
    delivered by pushing it onto the handler's client queue from a background
    thread shortly after the connection is established.
    """

    def test_returns_event_stream_content_type(self, test_client, db_session):
        sid = _seed_session(db_session, status="completed", graded=1)

        # Pre-populate active grading so the SSE handler has something to
        # report and doesn't fall through to the DB path (which uses
        # SessionLocal and can't see the test DB).
        _active_grading[sid] = {
            "status": "completed",
            "current_student": "",
            "graded_count": 1,
            "failed_count": 0,
            "total": 1,
            "stage": "done",
        }

        def _push_complete_event():
            """Wait for the handler to register its client queue, then push a
            complete event so the generator terminates."""
            for _ in range(50):  # up to 5 seconds
                time.sleep(0.1)
                queues = _sse_queues.get(sid, [])
                if queues:
                    for q in queues:
                        q.put({
                            "type": "complete",
                            "graded_count": 1,
                            "failed_count": 0,
                            "total": 1,
                            "message": "done",
                        })
                    return

        pusher = threading.Thread(target=_push_complete_event, daemon=True)
        pusher.start()

        with test_client.stream("GET", f"/session/{sid}/grade-stream") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            # Read lines until we see the complete event or give up
            lines = []
            for line in resp.iter_lines():
                lines.append(line)
                if any("complete" in l for l in lines):
                    break
            assert any("complete" in l for l in lines)

        pusher.join(timeout=2)


# ---------------------------------------------------------------------------
# Tests: status endpoint
# ---------------------------------------------------------------------------

class TestSessionStatus:
    """GET /session/{id}/status"""

    def test_returns_progress_fields(self, test_client, db_session):
        sid = _seed_session(db_session, total=5, graded=3)
        resp = test_client.get(f"/session/{sid}/status")
        assert resp.status_code == 200
        body = resp.json()
        for key in ("session_id", "status", "total_students", "graded_count", "failed_count", "progress_percentage"):
            assert key in body, f"Missing key: {key}"
        assert body["session_id"] == sid
        assert body["total_students"] == 5

    def test_nonexistent_session_returns_404(self, test_client):
        resp = test_client.get("/session/99999/status")
        assert resp.status_code == 404

    def test_progress_percentage_zero_when_no_students(self, test_client, db_session):
        sid = _seed_session(db_session, total=0, graded=0)
        resp = test_client.get(f"/session/{sid}/status")
        body = resp.json()
        assert body["progress_percentage"] == 0
