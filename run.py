"""Entry point for the AI Grading System."""
import os

import uvicorn


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

if __name__ == "__main__":
    # Autoreload can interrupt grading jobs because uploads are written during processing.
    reload_enabled = _env_bool("UVICORN_RELOAD", "0")
    reload_dirs = ["app"] if reload_enabled else None

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=reload_enabled,
        reload_dirs=reload_dirs,
        reload_excludes=[
            "uploads/**",
            "*.db",
            "*.db-*",
            "*.zip",
            "*.tar.gz",
            "*.rar",
            "*.log",
        ],
    )
