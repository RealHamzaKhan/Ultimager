"""Root conftest: fixtures for test client, test DB, mock LLM, sample files."""
import io
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure test env
os.environ.setdefault("NVIDIA_API_KEY", "test-key-not-real")
os.environ.setdefault("NVIDIA_MODEL", "test-model")

from app.database import get_db
from app.main import app
from app.models import Base, GradingSession, StudentSubmission


# ── Database fixtures ─────────────────────────────────────────────

@pytest.fixture()
def db_engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Enable foreign keys for SQLite
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """Provide a transactional DB session for tests."""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def test_client(db_engine):
    """FastAPI TestClient with test DB injected."""
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ── Sample data fixtures ──────────────────────────────────────────

@pytest.fixture()
def sample_session(db_session):
    """Create a sample grading session in the test DB."""
    session = GradingSession(
        title="Test Assignment",
        description="A test assignment for unit tests",
        rubric="Code Quality (40): correctness, style\nDocumentation (30): comments, README\nTesting (30): test coverage",
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
    return session


@pytest.fixture()
def sample_submission(db_session, sample_session):
    """Create a sample student submission."""
    sub = StudentSubmission(
        session_id=sample_session.id,
        student_identifier="alice_perfect",
        files=json.dumps([{"filename": "solution.py", "type": "code"}]),
        file_count=1,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(sub)
    db_session.commit()
    db_session.refresh(sub)
    return sub


@pytest.fixture()
def graded_submission(db_session, sample_session):
    """Create a graded student submission with AI result."""
    ai_result = {
        "rubric_breakdown": [
            {"criterion": "Code Quality", "score": 35, "max": 40, "justification": "Good code"},
            {"criterion": "Documentation", "score": 25, "max": 30, "justification": "Decent docs"},
            {"criterion": "Testing", "score": 20, "max": 30, "justification": "Some tests"},
        ],
        "total_score": 80,
        "overall_feedback": "Good work overall.",
        "strengths": ["Clean code", "Good naming"],
        "weaknesses": ["Missing edge case tests"],
        "suggestions_for_improvement": "Add more tests.",
        "confidence": "high",
        "confidence_reasoning": "Clear submission with good structure.",
        "grading_hash": "abc123def456",
        "transparency": {
            "llm_call": {
                "model": "test-model",
                "provider": "nvidia",
                "usage": {"prompt_tokens": 500, "completion_tokens": 200, "total_tokens": 700},
                "fallback_used": False,
                "consistency_alert": False,
            },
            "text_chars_sent": 1200,
            "images_sent": 0,
        },
    }
    sub = StudentSubmission(
        session_id=sample_session.id,
        student_identifier="bob_java",
        files=json.dumps([{"filename": "Main.java", "type": "code"}]),
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
    return sub


@pytest.fixture()
def sample_zip_bytes():
    """Create an in-memory ZIP with test student files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("alice_perfect/solution.py", 'def add(a, b):\n    return a + b\n')
        zf.writestr("alice_perfect/README.md", "# Alice's Solution\nThis is my solution.")
        zf.writestr("bob_java/Main.java", 'public class Main {\n    public static void main(String[] args) {\n        System.out.println("Hello");\n    }\n}')
        zf.writestr("carol_empty/.gitkeep", "")
    buf.seek(0)
    return buf.getvalue()


@pytest.fixture()
def sample_rubric():
    """A structured rubric dict for testing."""
    return {
        "criteria": [
            {"name": "Code Quality", "max_score": 40, "description": "Correctness and style"},
            {"name": "Documentation", "max_score": 30, "description": "Comments and README"},
            {"name": "Testing", "max_score": 30, "description": "Test coverage and quality"},
        ],
        "max_score": 100,
    }


@pytest.fixture()
def sample_rubric_text():
    """Rubric as plain text (how it's stored in DB)."""
    return "Code Quality (40): Correctness and style\nDocumentation (30): Comments and README\nTesting (30): Test coverage and quality"


# ── Mock LLM fixtures ────────────────────────────────────────────

@pytest.fixture()
def mock_llm_response():
    """Factory for configurable mock LLM responses."""
    def _make(
        score=80,
        grade="B",
        confidence="high",
        error=None,
        malformed=False,
        timeout=False,
        rate_limit=False,
    ):
        if timeout:
            raise TimeoutError("LLM request timed out")
        if rate_limit:
            from openai import RateLimitError
            raise RateLimitError("Rate limit exceeded", response=MagicMock(status_code=429), body=None)
        if malformed:
            return "This is not valid JSON at all {broken"
        if error:
            return json.dumps({"error": error})

        return json.dumps({
            "rubric_breakdown": [
                {"criterion": "Code Quality", "score": int(score * 0.4), "max": 40, "justification": "Test"},
                {"criterion": "Documentation", "score": int(score * 0.3), "max": 30, "justification": "Test"},
                {"criterion": "Testing", "score": int(score * 0.3), "max": 30, "justification": "Test"},
            ],
            "total_score": score,
            "overall_feedback": "Test feedback",
            "strengths": ["Good"],
            "weaknesses": ["Could improve"],
            "suggestions_for_improvement": "Keep going",
            "confidence": confidence,
            "confidence_reasoning": "Test reasoning",
        })

    return _make


@pytest.fixture()
def temp_dir():
    """Provide a temporary directory for file operations."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)
