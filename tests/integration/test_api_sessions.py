"""Integration tests for session CRUD endpoints."""
import json

import pytest


class TestCreateSession:
    """POST /session/new"""

    def test_valid_form_data_redirects(self, test_client):
        resp = test_client.post(
            "/session/new",
            data={"title": "Midterm Exam", "description": "CS101 midterm", "rubric": "Quality (50)\nStyle (50)", "max_score": "100"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/session/" in resp.headers["location"]

    def test_missing_title_returns_422(self, test_client):
        """title is a required Form(...) field; omitting it triggers a 422."""
        resp = test_client.post(
            "/session/new",
            data={"description": "no title here", "rubric": "r", "max_score": "100"},
            follow_redirects=False,
        )
        assert resp.status_code == 422

    def test_created_session_appears_in_api_list(self, test_client):
        test_client.post(
            "/session/new",
            data={"title": "Listed Session", "description": "", "rubric": "", "max_score": "100"},
            follow_redirects=False,
        )
        resp = test_client.get("/api/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] >= 1
        titles = [s["title"] for s in body["sessions"]]
        assert "Listed Session" in titles


class TestListSessions:
    """GET /api/sessions"""

    def test_empty_db_returns_zero_count(self, test_client):
        resp = test_client.get("/api/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["sessions"] == []

    def test_returns_sessions_array(self, test_client):
        # Create two sessions
        for title in ("Alpha", "Beta"):
            test_client.post(
                "/session/new",
                data={"title": title, "description": "", "rubric": "", "max_score": "100"},
                follow_redirects=False,
            )
        resp = test_client.get("/api/sessions")
        body = resp.json()
        assert body["count"] == 2
        assert isinstance(body["sessions"], list)

    def test_session_fields_present(self, test_client):
        test_client.post(
            "/session/new",
            data={"title": "Fields Check", "description": "d", "rubric": "r", "max_score": "50"},
            follow_redirects=False,
        )
        resp = test_client.get("/api/sessions")
        session = resp.json()["sessions"][0]
        for key in ("id", "title", "status", "total_students", "graded_count", "error_count", "created_at", "max_score"):
            assert key in session
        assert session["max_score"] == 50


class TestSessionDetail:
    """GET /session/{id}"""

    def test_existing_session_returns_200(self, test_client):
        create_resp = test_client.post(
            "/session/new",
            data={"title": "Detail Session", "description": "", "rubric": "", "max_score": "100"},
            follow_redirects=False,
        )
        location = create_resp.headers["location"]
        session_id = location.rstrip("/").split("/")[-1]
        resp = test_client.get(f"/session/{session_id}")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_nonexistent_session_returns_404(self, test_client):
        resp = test_client.get("/session/99999")
        assert resp.status_code == 404


class TestDeleteSession:
    """POST /session/{id}/delete"""

    def test_delete_existing_session_redirects(self, test_client):
        create_resp = test_client.post(
            "/session/new",
            data={"title": "To Delete", "description": "", "rubric": "", "max_score": "100"},
            follow_redirects=False,
        )
        session_id = create_resp.headers["location"].rstrip("/").split("/")[-1]

        resp = test_client.post(f"/session/{session_id}/delete", follow_redirects=False)
        assert resp.status_code == 303

        # Verify it's gone
        assert test_client.get(f"/session/{session_id}").status_code == 404

    def test_delete_nonexistent_session_returns_404(self, test_client):
        resp = test_client.post("/session/99999/delete", follow_redirects=False)
        assert resp.status_code == 404
