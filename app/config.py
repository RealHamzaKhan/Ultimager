"""Application configuration — loads .env and defines constants.

All LLM provider settings are configurable via environment variables so the
system works with any OpenAI-compatible API (NVIDIA NIM, OpenAI, Together AI,
Groq, Ollama, etc.).  Just set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL in
your .env file.

Backward-compat: NVIDIA_API_KEY / NVIDIA_MODEL / NVIDIA_BASE_URL are still
accepted as aliases so existing .env files continue to work without changes.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ── LLM Provider (any OpenAI-compatible endpoint) ────────────────────────────
# Primary model — used for grading, routing, relevance checks, vision
LLM_API_KEY: str = (
    os.getenv("LLM_API_KEY")
    or os.getenv("NVIDIA_API_KEY")
    or ""
)
LLM_BASE_URL: str = (
    os.getenv("LLM_BASE_URL")
    or os.getenv("NVIDIA_BASE_URL")
    or "https://integrate.api.nvidia.com/v1"
)
LLM_MODEL: str = (
    os.getenv("LLM_MODEL")
    or os.getenv("NVIDIA_MODEL")
    or "meta/llama-4-maverick-17b-128e-instruct"
)
# Secondary model — used for text-only tasks like rubric generation.
# Defaults to LLM_MODEL when not set (fine for single-model providers).
LLM_RUBRIC_MODEL: str = (
    os.getenv("LLM_RUBRIC_MODEL")
    or os.getenv("GLM_TEXT_MODEL")
    or os.getenv("LLM_MODEL")
    or os.getenv("NVIDIA_MODEL")
    or "meta/llama-4-maverick-17b-128e-instruct"
)

# Backward-compat aliases — code that still imports the old names keeps working
NVIDIA_API_KEY: str = LLM_API_KEY
NVIDIA_BASE_URL: str = LLM_BASE_URL
NVIDIA_MODEL: str = LLM_MODEL
GLM_TEXT_MODEL: str = LLM_RUBRIC_MODEL

# ── Request / timeout settings ───────────────────────────────────────────────
# HTTP timeout for a single LLM API call (seconds)
LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "120.0"))
# Requests per minute — lower this if your provider has a stricter rate limit
RATE_LIMIT_RPM: int = int(os.getenv("RATE_LIMIT_RPM", "40"))

# ── Max tokens per call type ─────────────────────────────────────────────────
# Judge output: short JSON verdict per checkpoint → 800 is plenty
MAX_TOKENS_JUDGE: int = int(os.getenv("MAX_TOKENS_JUDGE", "800"))
# Full grading pass: needs room for all criteria at once
MAX_TOKENS_GRADING: int = int(os.getenv("MAX_TOKENS_GRADING", "4000"))
# Retry grading pass: re-evaluates only failed checkpoints
MAX_TOKENS_RETRY: int = int(os.getenv("MAX_TOKENS_RETRY", "2000"))
# File-to-criterion routing: returns a small JSON mapping
MAX_TOKENS_ROUTING: int = int(os.getenv("MAX_TOKENS_ROUTING", "2000"))
# Checkpoint generation from rubric
MAX_TOKENS_CHECKPOINT_GEN: int = int(os.getenv("MAX_TOKENS_CHECKPOINT_GEN", "4000"))
# Rubric generation (Phase 1 extraction + Phase 2 criteria)
MAX_TOKENS_RUBRIC: int = int(os.getenv("MAX_TOKENS_RUBRIC", "5000"))
# Relevance / sanity check: brief yes/no answer
MAX_TOKENS_RELEVANCE: int = int(os.getenv("MAX_TOKENS_RELEVANCE", "500"))
# Vision pre-analysis transcription
MAX_TOKENS_VISION: int = int(os.getenv("MAX_TOKENS_VISION", "1200"))

# ── Judge context window ─────────────────────────────────────────────────────
# Maximum chars of student content sent to the domain judge per checkpoint.
# With per-criterion file routing the actual content is already scoped to one
# file, so 60 K covers even large Jupyter notebooks with full cell outputs.
# Llama 4 Maverick's 128 K-token window allows up to ~370 K chars total.
JUDGE_CONTENT_LIMIT: int = int(os.getenv("JUDGE_CONTENT_LIMIT", "60000"))

# ── Vision capabilities ───────────────────────────────────────────────────────
NVIDIA_MAX_IMAGES_PER_REQUEST: int = int(os.getenv("NVIDIA_MAX_IMAGES_PER_REQUEST", "8"))
# Images sent per file during routing — low detail for efficiency
MAX_IMAGES_PER_FILE_FOR_ROUTING: int = int(os.getenv("MAX_IMAGES_PER_FILE_FOR_ROUTING", "2"))

# ── Provider / scoring config ────────────────────────────────────────────────
LLM_PROVIDER_ORDER: str = os.getenv("LLM_PROVIDER_ORDER", "nvidia")
SCORING_PRIMARY_PROVIDER: str = os.getenv("SCORING_PRIMARY_PROVIDER", "nvidia").strip().lower()
SCORING_ALLOW_FALLBACK: bool = False
SCORING_CONSISTENCY_ALERT_DELTA: float = float(os.getenv("SCORING_CONSISTENCY_ALERT_DELTA", "1.5"))

ENABLE_VISION_PREANALYSIS: bool = os.getenv("ENABLE_VISION_PREANALYSIS", "1").strip().lower() in {"1", "true", "yes", "on"}
MAX_IMAGES_SELECTION_POOL: int = int(os.getenv("MAX_IMAGES_SELECTION_POOL", "220"))
MAX_IMAGES_FOR_PREANALYSIS: int = int(os.getenv("MAX_IMAGES_FOR_PREANALYSIS", "200"))
VISION_PREANALYSIS_CHUNK_SIZE: int = int(os.getenv("VISION_PREANALYSIS_CHUNK_SIZE", "6"))
MAX_IMAGES_FOR_FINAL_GRADE: int = int(os.getenv("MAX_IMAGES_FOR_FINAL_GRADE", "8"))
MAX_FINAL_IMAGE_BYTES: int = int(os.getenv("MAX_FINAL_IMAGE_BYTES", "8000000"))

# ── Vision Transcript Injection ───────────────────────────────────────────────
VISION_TRANSCRIPT_MAX_CHARS: int = int(os.getenv("VISION_TRANSCRIPT_MAX_CHARS", "80000"))
VISION_ENTRY_TRANSCRIPTION_LIMIT: int = int(os.getenv("VISION_ENTRY_TRANSCRIPTION_LIMIT", "1200"))
VISION_ENTRY_SUMMARY_LIMIT: int = int(os.getenv("VISION_ENTRY_SUMMARY_LIMIT", "800"))

# ── Score Verification ────────────────────────────────────────────────────────
SCORE_VERIFICATION_ENABLED: bool = os.getenv("SCORE_VERIFICATION_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}

# ── ACMAG (Anchor-Calibrated Multi-Agent Grading) ────────────────────────────
ACMAG_ENABLED: bool = os.getenv("ACMAG_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
ACMAG_CALIBRATION_RATIO: float = float(os.getenv("ACMAG_CALIBRATION_RATIO", "0.10"))
ACMAG_MIN_CALIBRATION: int = int(os.getenv("ACMAG_MIN_CALIBRATION", "3"))
ACMAG_MAX_CALIBRATION: int = int(os.getenv("ACMAG_MAX_CALIBRATION", "12"))
ACMAG_BLIND_REVIEW_RATIO: float = float(os.getenv("ACMAG_BLIND_REVIEW_RATIO", "0.30"))
ACMAG_KAPPA_THRESHOLD: float = float(os.getenv("ACMAG_KAPPA_THRESHOLD", "0.60"))
ACMAG_MODERATION_SCORE_DELTA: float = float(os.getenv("ACMAG_MODERATION_SCORE_DELTA", "1.0"))
ACMAG_MAX_ANCHORS: int = int(os.getenv("ACMAG_MAX_ANCHORS", "8"))

# ── Parallel Grading ──────────────────────────────────────────────────────────
PARALLEL_GRADING_ENABLED: bool = os.getenv("PARALLEL_GRADING_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
PARALLEL_GRADING_WORKERS: int = int(os.getenv("PARALLEL_GRADING_WORKERS", "8"))

# ── Paths ─────────────────────────────────────────────────────────────────────
UPLOAD_DIR: Path = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'grading.db'}"

# ── Multi-Pass Grading ────────────────────────────────────────────────────────
# Splits content into windows when it exceeds the model's effective context.
# Llama 4 Maverick has 128 K tokens (~400 K chars); set threshold high so
# multi-pass only triggers for truly massive submissions.
MODEL_CONTEXT_TOKENS: int = int(os.getenv("MODEL_CONTEXT_TOKENS", "128000"))
MODEL_RESERVED_TOKENS: int = int(os.getenv("MODEL_RESERVED_TOKENS", "12000"))
CHARS_PER_TOKEN_ESTIMATE: float = float(os.getenv("CHARS_PER_TOKEN_ESTIMATE", "3.2"))
MULTI_PASS_TEXT_THRESHOLD: int = int(os.getenv(
    "MULTI_PASS_TEXT_THRESHOLD",
    str(int((MODEL_CONTEXT_TOKENS - MODEL_RESERVED_TOKENS) * CHARS_PER_TOKEN_ESTIMATE))
))
MULTI_PASS_WINDOW_SIZE: int = int(os.getenv("MULTI_PASS_WINDOW_SIZE", "100000"))
MULTI_PASS_OVERLAP: int = int(os.getenv("MULTI_PASS_OVERLAP", "4000"))
FINAL_IMAGE_CAP: int = int(os.getenv("FINAL_IMAGE_CAP", "6"))

# ── Code execution limits ─────────────────────────────────────────────────────
EXEC_TIMEOUT_SECONDS: int = int(os.getenv("EXEC_TIMEOUT_SECONDS", "10"))
EXEC_MEMORY_LIMIT_MB: int = int(os.getenv("EXEC_MEMORY_LIMIT_MB", "256"))

# ── Supported file extensions ─────────────────────────────────────────────────
CODE_EXTENSIONS: set[str] = {
    ".py", ".java", ".cpp", ".c", ".js", ".ts", ".cs", ".go", ".rb",
    ".php", ".swift", ".kt", ".scala", ".rs", ".sh", ".sql", ".html",
    ".css", ".r", ".m",
}
IMAGE_EXTENSIONS: set[str] = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

IGNORED_NAMES: set[str] = {
    "__MACOSX", ".DS_Store", "__pycache__", ".git", ".gitignore",
    "test_datasets",
    "Thumbs.db", "desktop.ini",
}
