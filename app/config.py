"""Application configuration — loads .env and defines constants."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ── NVIDIA NIM API ────────────────────────────────────────────────
# Primary and only provider for this grading system
NVIDIA_API_KEY: str = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
# Llama 4 Maverick (17B MoE) - faster and more efficient than Qwen
NVIDIA_MODEL: str = os.getenv("NVIDIA_MODEL", "meta/llama-4-maverick-17b-128e-instruct")

# Vision capabilities for the model
NVIDIA_MAX_IMAGES_PER_REQUEST: int = int(os.getenv("NVIDIA_MAX_IMAGES_PER_REQUEST", "8"))

# Only use NVIDIA as the single provider
LLM_PROVIDER_ORDER: str = os.getenv("LLM_PROVIDER_ORDER", "nvidia")

# ── Scoring Configuration ─────────────────────────────────────────
SCORING_PRIMARY_PROVIDER: str = os.getenv("SCORING_PRIMARY_PROVIDER", "nvidia").strip().lower()
SCORING_ALLOW_FALLBACK: bool = False  # Single provider - no fallback
SCORING_CONSISTENCY_ALERT_DELTA: float = float(os.getenv("SCORING_CONSISTENCY_ALERT_DELTA", "1.5"))

ENABLE_VISION_PREANALYSIS: bool = os.getenv("ENABLE_VISION_PREANALYSIS", "1").strip().lower() in {"1", "true", "yes", "on"}
# Global pool for image-aware grading. Keep this high enough to avoid dropping later pages/files.
MAX_IMAGES_SELECTION_POOL: int = int(os.getenv("MAX_IMAGES_SELECTION_POOL", "220"))
# 0 means "use full selection pool"; positive values apply a cap with diversity sampling.
MAX_IMAGES_FOR_PREANALYSIS: int = int(os.getenv("MAX_IMAGES_FOR_PREANALYSIS", "200"))
VISION_PREANALYSIS_CHUNK_SIZE: int = int(os.getenv("VISION_PREANALYSIS_CHUNK_SIZE", "6"))
MAX_IMAGES_FOR_FINAL_GRADE: int = int(os.getenv("MAX_IMAGES_FOR_FINAL_GRADE", "8"))
MAX_FINAL_IMAGE_BYTES: int = int(os.getenv("MAX_FINAL_IMAGE_BYTES", "8000000"))
RATE_LIMIT_RPM: int = 40  # requests per minute — hard limit

# ── Vision Transcript Injection ──────────────────────────────────
# Full per-image transcriptions replace the old lossy consolidation summary.
VISION_TRANSCRIPT_MAX_CHARS: int = int(os.getenv("VISION_TRANSCRIPT_MAX_CHARS", "80000"))
VISION_ENTRY_TRANSCRIPTION_LIMIT: int = int(os.getenv("VISION_ENTRY_TRANSCRIPTION_LIMIT", "1200"))
VISION_ENTRY_SUMMARY_LIMIT: int = int(os.getenv("VISION_ENTRY_SUMMARY_LIMIT", "800"))

# ── Score Verification ───────────────────────────────────────────
# DISABLED: The verification LLM was incorrectly zeroing scores where
# the grading LLM correctly awarded partial credit (e.g. typos in function
# names treated as "no evidence").  The primary grading pass has strong
# enough prompt guardrails; a second pass with less context does more harm.
SCORE_VERIFICATION_ENABLED: bool = os.getenv("SCORE_VERIFICATION_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}

# ── ACMAG (Anchor-Calibrated Multi-Agent Grading) ────────────────
# ACMAG can be enabled explicitly via env when needed. Keep default off for
# stable baseline grading throughput.
ACMAG_ENABLED: bool = os.getenv("ACMAG_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
ACMAG_CALIBRATION_RATIO: float = float(os.getenv("ACMAG_CALIBRATION_RATIO", "0.10"))
ACMAG_MIN_CALIBRATION: int = int(os.getenv("ACMAG_MIN_CALIBRATION", "3"))
ACMAG_MAX_CALIBRATION: int = int(os.getenv("ACMAG_MAX_CALIBRATION", "12"))
ACMAG_BLIND_REVIEW_RATIO: float = float(os.getenv("ACMAG_BLIND_REVIEW_RATIO", "0.30"))
ACMAG_KAPPA_THRESHOLD: float = float(os.getenv("ACMAG_KAPPA_THRESHOLD", "0.60"))
ACMAG_MODERATION_SCORE_DELTA: float = float(os.getenv("ACMAG_MODERATION_SCORE_DELTA", "1.0"))

# ── Parallel Grading Configuration ──────────────────────────────────────
PARALLEL_GRADING_ENABLED: bool = os.getenv("PARALLEL_GRADING_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
PARALLEL_GRADING_WORKERS: int = int(os.getenv("PARALLEL_GRADING_WORKERS", "8"))  # Concurrent students

ACMAG_MAX_ANCHORS: int = int(os.getenv("ACMAG_MAX_ANCHORS", "8"))

# ── Paths ─────────────────────────────────────────────────────────
UPLOAD_DIR: Path = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'grading.db'}"

# ── Multi-Pass Grading ────────────────────────────────────────────
# Multi-pass splits content into windows when it exceeds the model's
# effective context.  Llama 4 Maverick has 128K tokens (~400K chars),
# so we set the threshold high — multi-pass should only trigger for
# truly massive submissions, not normal 3-4 file assignments.
MODEL_CONTEXT_TOKENS: int = int(os.getenv("MODEL_CONTEXT_TOKENS", "128000"))
# Reserve tokens for system prompt + output — the rest is available for student content
MODEL_RESERVED_TOKENS: int = int(os.getenv("MODEL_RESERVED_TOKENS", "12000"))
# Chars-per-token estimate (conservative for Llama tokenizer)
CHARS_PER_TOKEN_ESTIMATE: float = float(os.getenv("CHARS_PER_TOKEN_ESTIMATE", "3.2"))
# Effective char limit = (128000 - 12000) * 3.2 ≈ 371,200 chars
MULTI_PASS_TEXT_THRESHOLD: int = int(os.getenv(
    "MULTI_PASS_TEXT_THRESHOLD",
    str(int((MODEL_CONTEXT_TOKENS - MODEL_RESERVED_TOKENS) * CHARS_PER_TOKEN_ESTIMATE))
))
MULTI_PASS_WINDOW_SIZE: int = int(os.getenv("MULTI_PASS_WINDOW_SIZE", "100000"))
MULTI_PASS_OVERLAP: int = int(os.getenv("MULTI_PASS_OVERLAP", "4000"))  # overlap chars between windows
FINAL_IMAGE_CAP: int = int(os.getenv("FINAL_IMAGE_CAP", "6"))  # images per LLM request

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
IGNORED_NAMES: set[str] = {
    "__MACOSX", ".DS_Store", "__pycache__", ".git", ".gitignore",
    "test_datasets", "tests", "test", "testing",
    "__test__carol_cpp", "__test__eve_pdf_text", "__test__nick_mixed", "__test__jake_flat",
    "__test__grace_notebook", "__test__bob_java", "__test__frank_pdf_scanned", "__test__dan_docx",
    "__test__karen_macos_junk", "__test__leo_empty", "__test__iris_nested", "__test__olivia_unsupported",
    "__test__mia_unicode", "__test__alice_perfect", "__test__henry_images"
}
