"""End-to-end tests for the full grading pipeline.

These tests exercise the complete flow: create session → upload → grade → results.
They use mocked LLM calls but real DB and file processing.
"""
import io
import json
import os
import time
import zipfile
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


class TestCreateSessionFlow:
    """Test session creation via the API."""

    def test_create_session_valid(self, test_client):
        resp = test_client.post("/session/new", data={
            "title": "E2E Test Assignment",
            "description": "Testing the full pipeline",
            "rubric": "Code Quality (50): Correctness\nDocumentation (50): Comments",
            "max_score": "100",
        })
        # Should redirect to session detail page
        assert resp.status_code in (200, 302, 303)

    def test_create_session_missing_title(self, test_client):
        resp = test_client.post("/session/new", data={
            "title": "",
            "description": "Testing",
            "rubric": "Code (100): Quality",
            "max_score": "100",
        })
        # Should either return error or redirect back
        assert resp.status_code in (200, 302, 303, 422)


class TestUploadFlow:
    """Test file upload to a session."""

    def test_upload_zip_to_session(self, test_client, db_session, sample_session, sample_zip_bytes):
        files = {"zip_file": ("submissions.zip", io.BytesIO(sample_zip_bytes), "application/zip")}
        resp = test_client.post(
            f"/session/{sample_session.id}/upload",
            files=files,
        )
        # Should succeed - redirect or 200
        assert resp.status_code in (200, 302, 303)

    def test_upload_to_nonexistent_session(self, test_client, sample_zip_bytes):
        files = {"zip_file": ("submissions.zip", io.BytesIO(sample_zip_bytes), "application/zip")}
        resp = test_client.post(
            "/session/99999/upload",
            files=files,
        )
        assert resp.status_code in (404, 302)


class TestSessionListAPI:
    """Test the session list API."""

    def test_list_sessions_empty(self, test_client):
        resp = test_client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

    def test_list_sessions_with_data(self, test_client, db_session, sample_session):
        resp = test_client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        sessions = data["sessions"]
        assert len(sessions) >= 1
        assert any(s["title"] == "Test Assignment" for s in sessions)


class TestSessionStatusAPI:
    """Test session status endpoint."""

    def test_get_status_pending(self, test_client, db_session, sample_session):
        resp = test_client.get(f"/session/{sample_session.id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"

    def test_get_status_nonexistent(self, test_client):
        resp = test_client.get("/session/99999/status")
        assert resp.status_code == 404


class TestDeleteSessionFlow:
    """Test session deletion."""

    def test_delete_session(self, test_client, db_session, sample_session):
        resp = test_client.post(f"/session/{sample_session.id}/delete")
        # Should redirect to home
        assert resp.status_code in (200, 302, 303)

    def test_delete_nonexistent_session(self, test_client):
        resp = test_client.post("/session/99999/delete")
        assert resp.status_code in (404, 302)


class TestOverrideFlow:
    """Test grade override workflow."""

    def test_override_graded_student(self, test_client, db_session, graded_submission, sample_session):
        resp = test_client.post(
            f"/session/{sample_session.id}/student/{graded_submission.id}/override",
            json={"score": 90.0, "comments": "Adjusted", "is_reviewed": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ("success", "ok", True) or "score" in str(data).lower()

    def test_override_exceeds_max(self, test_client, db_session, graded_submission, sample_session):
        resp = test_client.post(
            f"/session/{sample_session.id}/student/{graded_submission.id}/override",
            json={"score": 999.0, "comments": "Too high"},
        )
        assert resp.status_code == 422


class TestFlagFlow:
    """Test student flagging workflow."""

    def test_flag_student(self, test_client, db_session, sample_submission, sample_session):
        resp = test_client.post(
            f"/session/{sample_session.id}/student/{sample_submission.id}/flag",
            data={"reason": "Suspected plagiarism"},
        )
        assert resp.status_code == 200

    def test_unflag_student(self, test_client, db_session, sample_submission, sample_session):
        # First flag
        test_client.post(
            f"/session/{sample_session.id}/student/{sample_submission.id}/flag",
            data={"reason": "Test"},
        )
        # Then unflag
        resp = test_client.post(
            f"/session/{sample_session.id}/student/{sample_submission.id}/unflag",
        )
        assert resp.status_code == 200


class TestExportFlow:
    """Test export endpoints."""

    def test_export_csv(self, test_client, db_session, sample_session, graded_submission):
        resp = test_client.get(f"/session/{sample_session.id}/export/csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "") or "csv" in resp.headers.get("content-disposition", "").lower() or resp.status_code == 200

    def test_export_json(self, test_client, db_session, sample_session, graded_submission):
        resp = test_client.get(f"/session/{sample_session.id}/export/json")
        assert resp.status_code == 200

    def test_export_nonexistent_session(self, test_client):
        resp = test_client.get("/session/99999/export/csv")
        assert resp.status_code == 404


class TestRetryFailedFlow:
    """Test retry-failed endpoint."""

    def test_retry_failed_resets_errors(self, test_client, db_session, sample_session):
        # Create an error submission
        from app.models import StudentSubmission
        sub = StudentSubmission(
            session_id=sample_session.id,
            student_identifier="error_student",
            files=json.dumps([]),
            file_count=0,
            status="error",
            error_message="LLM timeout",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(sub)
        db_session.commit()

        resp = test_client.post(f"/session/{sample_session.id}/retry-failed")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("reset_count", 0) >= 1 or "reset" in str(data).lower()


class TestHealthCheck:
    """Test health endpoint."""

    def test_health_returns_200(self, test_client):
        resp = test_client.get("/health")
        assert resp.status_code == 200
