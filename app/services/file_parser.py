"""Parse individual files into content the AI can consume.

Supports: code, PDF, DOCX, images, Jupyter notebooks.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Extension categories ──────────────────────────────────────────
CODE_EXTENSIONS: set[str] = {
    ".py", ".java", ".cpp", ".c", ".js", ".ts", ".cs", ".go", ".rb",
    ".php", ".swift", ".kt", ".scala", ".rs", ".sh", ".sql", ".html",
    ".css", ".r", ".m",
}
IMAGE_EXTENSIONS: set[str] = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def parse_file(file_path: Path) -> dict:
    """Return a dict describing the file content for the AI.

    Keys:
        filename, type, content (text or base64), size, error (if any)
    """
    # Convert to Path if string
    if isinstance(file_path, str):
        file_path = Path(file_path)
    
    ext = file_path.suffix.lower()
    result: dict = {
        "filename": file_path.name,
        "extension": ext,
        "size": file_path.stat().st_size if file_path.exists() else 0,
    }

    try:
        if ext in CODE_EXTENSIONS:
            result.update(_parse_code(file_path))
        elif ext == ".pdf":
            result.update(_parse_pdf(file_path))
        elif ext == ".docx":
            result.update(_parse_docx(file_path))
        elif ext in IMAGE_EXTENSIONS:
            result.update(_parse_image(file_path))
        elif ext == ".ipynb":
            result.update(_parse_notebook(file_path))
        elif ext == ".md" or ext == ".txt":
            parsed = _parse_code(file_path)
            parsed["type"] = "text"  # Override type for plain text files
            result.update(parsed)
        else:
            result["type"] = "unsupported"
            result["content"] = None
            logger.info("Unsupported file skipped: %s", file_path.name)
    except Exception as exc:
        logger.exception("Error parsing %s", file_path.name)
        result["type"] = "error"
        result["content"] = None
        result["error"] = str(exc)

    return result


# ── Private helpers ───────────────────────────────────────────────

def _parse_code(file_path: Path) -> dict:
    text = file_path.read_text(encoding="utf-8", errors="replace")
    return {"type": "code", "content": text}


def _parse_pdf(file_path: Path) -> dict:
    """Extract text with PyMuPDF; fall back to image conversion for scanned PDFs."""
    import fitz  # PyMuPDF

    doc = fitz.open(str(file_path))
    text_parts: list[str] = []
    for page in doc:
        text_parts.append(page.get_text())
    full_text = "\n".join(text_parts).strip()
    doc.close()

    # If very little text extracted, treat as scanned — send page images
    if len(full_text) < 50:
        return _pdf_to_images(file_path)

    return {"type": "pdf_text", "content": full_text}


def _pdf_to_images(file_path: Path) -> dict:
    """Convert PDF pages to base64 PNG images for vision input."""
    import fitz

    doc = fitz.open(str(file_path))
    images: list[str] = []
    for page_num, page in enumerate(doc):
        pix = page.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("png")
        b64 = base64.b64encode(img_bytes).decode()
        images.append(b64)
        if page_num >= 4:  # cap at 5 pages to avoid huge payloads
            break
    doc.close()
    return {"type": "pdf_images", "content": images}


def _parse_docx(file_path: Path) -> dict:
    from docx import Document

    doc = Document(str(file_path))
    parts: list[str] = []

    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)

    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells)
            parts.append(row_text)

    return {"type": "docx", "content": "\n".join(parts)}


def _parse_image(file_path: Path) -> dict:
    """Base64-encode an image for AI vision input."""
    raw = file_path.read_bytes()
    b64 = base64.b64encode(raw).decode()
    ext = file_path.suffix.lower().lstrip(".")
    if ext == "jpg":
        ext = "jpeg"
    return {"type": "image", "content": b64, "media_type": f"image/{ext}"}


def _parse_notebook(file_path: Path) -> dict:
    """Convert .ipynb to structured text with code + markdown cells."""
    import nbformat

    nb = nbformat.read(str(file_path), as_version=4)
    parts: list[str] = []

    for i, cell in enumerate(nb.cells, 1):
        if cell.cell_type == "code":
            parts.append(f"# --- Code Cell {i} ---\n{cell.source}")
        elif cell.cell_type == "markdown":
            parts.append(f"# --- Markdown Cell {i} ---\n{cell.source}")

    return {"type": "notebook", "content": "\n\n".join(parts)}
