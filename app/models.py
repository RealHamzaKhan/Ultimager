"""Database models for AI Grading System with persistent state."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, JSON
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class GradingSession(Base):
    """A grading session for an assignment."""
    __tablename__ = "grading_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    rubric = Column(Text, nullable=True)
    max_score = Column(Integer, default=100)
    
    # Test configuration
    run_command = Column(String(255), nullable=True)
    test_cases = Column(Text, nullable=True)  # JSON string
    questions = Column(Text, nullable=True)  # JSON string
    expected_files = Column(Text, nullable=True)  # JSON string
    
    # Progress tracking
    status = Column(String(50), default="pending")  # pending, grading, paused, completed, completed_with_errors, failed
    total_students = Column(Integer, default=0)
    graded_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    
    # Background task tracking
    task_id = Column(String(100), nullable=True, unique=True)  # Unique task identifier
    is_background_task = Column(Boolean, default=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_updated = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Resume capability
    current_student_index = Column(Integer, default=0)
    grading_progress = Column(JSON, default=dict)  # Detailed progress tracking
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    submissions = relationship("StudentSubmission", back_populates="session", cascade="all, delete-orphan")
    progress_logs = relationship("GradingProgress", back_populates="session", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<GradingSession(id={self.id}, title='{self.title}', status='{self.status}')>"


class StudentSubmission(Base):
    """A single student's submission within a grading session."""
    __tablename__ = "student_submissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("grading_sessions.id", ondelete="CASCADE"), nullable=False)
    student_identifier = Column(String(255), nullable=False)
    
    # File tracking
    files = Column(Text, nullable=True)  # JSON string of file metadata
    file_count = Column(Integer, default=0)
    
    # Grading status
    status = Column(String(50), default="pending")  # pending, grading, graded, error, skipped
    graded_at = Column(DateTime, nullable=True)
    
    # AI Results
    ai_result = Column(Text, nullable=True)  # JSON string
    ai_score = Column(Float, nullable=True)
    ai_letter_grade = Column(String(10), nullable=True)
    ai_confidence = Column(String(20), nullable=True)
    
    # Test results
    test_results = Column(Text, nullable=True)  # JSON string
    tests_passed = Column(Integer, default=0)
    tests_total = Column(Integer, default=0)
    
    # Override tracking
    is_overridden = Column(Boolean, default=False)
    override_score = Column(Float, nullable=True)
    override_comments = Column(Text, nullable=True)
    is_reviewed = Column(Boolean, default=False)
    
    # Error tracking
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    
    # Processing order
    processing_order = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    session = relationship("GradingSession", back_populates="submissions")
    progress_logs = relationship("GradingProgress", back_populates="submission", cascade="all, delete-orphan")
    
    @property
    def final_score(self) -> Optional[float]:
        """Return the final score (override if set, otherwise AI score)."""
        if self.is_overridden and self.override_score is not None:
            return self.override_score
        return self.ai_score
    
    def to_dict(self) -> dict:
        """Convert submission to dictionary for API responses."""
        import json
        return {
            "id": self.id,
            "session_id": self.session_id,
            "student_identifier": self.student_identifier,
            "status": self.status,
            "file_count": self.file_count,
            "ai_score": self.ai_score,
            "ai_letter_grade": self.ai_letter_grade,
            "ai_confidence": self.ai_confidence,
            "final_score": self.final_score,
            "is_overridden": self.is_overridden,
            "override_score": self.override_score,
            "is_reviewed": self.is_reviewed,
            "tests_passed": self.tests_passed,
            "tests_total": self.tests_total,
            "graded_at": self.graded_at.isoformat() if self.graded_at else None,
            "error_message": self.error_message,
            "files": json.loads(self.files) if self.files else [],
            "ai_result": json.loads(self.ai_result) if self.ai_result else None,
        }

    def __repr__(self):
        return f"<StudentSubmission(id={self.id}, student='{self.student_identifier}', status='{self.status}')>"


class GradingProgress(Base):
    """Detailed progress log for grading operations."""
    __tablename__ = "grading_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, ForeignKey("grading_sessions.id", ondelete="CASCADE"), nullable=False)
    submission_id = Column(Integer, ForeignKey("student_submissions.id", ondelete="CASCADE"), nullable=True)
    
    # Progress details
    event_type = Column(String(50), nullable=False)  # start, progress, complete, error, resume
    message = Column(Text, nullable=True)
    details = Column(JSON, default=dict)
    
    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    session = relationship("GradingSession", back_populates="progress_logs")
    submission = relationship("StudentSubmission", back_populates="progress_logs")

    def __repr__(self):
        return f"<GradingProgress(session={self.session_id}, type='{self.event_type}')>"


class BackgroundTask(Base):
    """Track background grading tasks for persistence across restarts."""
    __tablename__ = "background_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(100), nullable=False, unique=True)
    session_id = Column(Integer, ForeignKey("grading_sessions.id", ondelete="CASCADE"), nullable=False)
    
    # Task status
    status = Column(String(50), default="pending")  # pending, running, paused, completed, failed
    
    # Progress
    total_items = Column(Integer, default=0)
    completed_items = Column(Integer, default=0)
    error_items = Column(Integer, default=0)
    
    # Timing
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    last_activity = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    # Error tracking
    error_message = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<BackgroundTask(task_id='{self.task_id}', status='{self.status}')>"
