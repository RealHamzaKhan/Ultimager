"""Application configuration — loads .env and defines constants."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ── NVIDIA NIM API ────────────────────────────────────────────────
NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL: str = "moonshotai/kimi-k2-instruct"
RATE_LIMIT_RPM: int = 40  # requests per minute — hard limit

# ── Paths ─────────────────────────────────────────────────────────
UPLOAD_DIR: Path = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'grading.db'}"

# ── Code execution limits ─────────────────────────────────────────
EXEC_TIMEOUT_SECONDS: int = 10
EXEC_MEMORY_LIMIT_MB: int = 256

# ── Supported file extensions ─────────────────────────────────────
CODE_EXTENSIONS: set[str] = {
    ".py", ".java", ".cpp", ".c", ".js", ".ts", ".cs", ".go", ".rb",
    ".php", ".swift", ".kt", ".scala", ".rs", ".sh", ".sql", ".html",
    ".css", ".r", ".m",
}
IMAGE_EXTENSIONS: set[str] = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
IGNORED_NAMES: set[str] = {"__MACOSX", ".DS_Store", "__pycache__", ".git"}
