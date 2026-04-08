"""SQLAlchemy database setup — single-file SQLite."""
import logging
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

from app.config import DATABASE_URL

logger = logging.getLogger(__name__)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session and closes it after use."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _run_migrations():
    """Add missing columns to existing tables (safe, idempotent)."""
    inspector = inspect(engine)

    # Migration: add 'checkpoints' column to grading_sessions
    if "grading_sessions" in inspector.get_table_names():
        columns = {col["name"] for col in inspector.get_columns("grading_sessions")}
        if "checkpoints" not in columns:
            logger.info("Migration: adding 'checkpoints' column to grading_sessions")
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE grading_sessions ADD COLUMN checkpoints TEXT"))


def init_db():
    """Create all tables if they don't exist, then run migrations."""
    # Import models to ensure all tables are registered with Base
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    _run_migrations()
