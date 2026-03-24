"""Tests for app.services.file_parser_enhanced module."""
import base64
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.file_parser_enhanced import (
    ExtractedContent,
    IngestionReport,
    parse_file_with_report,
    process_student_submission,
    _parse_code_file,
    _parse_text_file,
)


# ── ZIP extraction ──────────────────────────────────────────────────


class TestZipExtraction:
    """Tests for ZIP-based extraction via parse_file_with_report."""

    def test_valid_zip_is_treated_as_archive(self, temp_dir):
        """A .zip file is recognized as an archive and skipped (not extracted inline)."""
        zpath = temp_dir / "student.zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("hello.py", "print('hello')")
        zpath.write_bytes(buf.getvalue())

        content, report = parse_file_with_report(zpath)
        assert content.file_type == "archive"
        assert report["status"] == "skipped"

    def test_empty_zip(self, temp_dir):
        """An empty ZIP is still recognized as an archive."""
        zpath = temp_dir / "empty.zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w"):
            pass
        zpath.write_bytes(buf.getvalue())

        content, report = parse_file_with_report(zpath)
        assert content.file_type == "archive"

    def test_unicode_filenames_in_zip(self, temp_dir):
        """ZIP entries with unicode names can be created (extraction tested at higher level)."""
        zpath = temp_dir / "unicode.zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("rapport_\u00e9tudiant.py", "x = 1")
            zf.writestr("\u4f5c\u696d.txt", "hello")
        zpath.write_bytes(buf.getvalue())

        content, report = parse_file_with_report(zpath)
        # It is still an archive at file-level parsing
        assert content.file_type == "archive"


# ── PDF parsing ─────────────────────────────────────────────────────


class TestPdfParsing:
    """Tests for PDF parsing via _parse_pdf_mixed."""

    def test_normal_pdf(self, temp_dir):
        """A valid PDF with text and renderable pages produces text and images."""
        pdf_path = temp_dir / "report.pdf"
        pdf_path.write_bytes(b"dummy")  # placeholder on disk

        # Mock fitz (PyMuPDF) -- imported inside _parse_pdf_mixed
        mock_page = MagicMock()
        mock_page.get_text.return_value = "This is page one content."
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"\x89PNG fake image bytes"
        mock_page.get_pixmap.return_value = mock_pix

        mock_doc = MagicMock()
        mock_doc.__len__ = lambda self: 1
        mock_doc.__getitem__ = lambda self, idx: mock_page

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        import sys
        sys.modules["fitz"] = mock_fitz
        try:
            content, report = parse_file_with_report(pdf_path)
        finally:
            sys.modules.pop("fitz", None)

        assert content.file_type in ("pdf", "mixed")
        assert content.text_content is not None
        assert "page one content" in content.text_content
        assert len(content.images) >= 1
        assert report["status"] == "parsed"

    def test_corrupted_pdf(self, temp_dir):
        """A corrupted PDF triggers an error result."""
        pdf_path = temp_dir / "bad.pdf"
        pdf_path.write_bytes(b"not a real pdf at all")

        mock_fitz = MagicMock()
        mock_fitz.open.side_effect = RuntimeError("cannot open broken file")

        import sys
        sys.modules["fitz"] = mock_fitz
        try:
            content, report = parse_file_with_report(pdf_path)
        finally:
            sys.modules.pop("fitz", None)

        assert content.file_type == "error"
        assert content.error is not None
        assert report["status"] == "failed"


# ── Image handling ──────────────────────────────────────────────────


class TestImageHandling:
    """Tests for image file parsing with real tiny images."""

    def _make_image(self, temp_dir, name, fmt, size=(4, 4)):
        from PIL import Image

        img = Image.new("RGB", size, color=(255, 0, 0))
        path = temp_dir / name
        img.save(str(path), format=fmt)
        return path

    def test_jpeg_image(self, temp_dir):
        path = self._make_image(temp_dir, "photo.jpg", "JPEG")
        content, report = parse_file_with_report(path)

        assert content.file_type == "image"
        assert len(content.images) == 1
        img_data = content.images[0]
        assert img_data["media_type"] == "image/jpeg"
        # base64 should decode without error
        raw = base64.b64decode(img_data["base64"])
        assert len(raw) > 0
        assert report["status"] == "parsed"

    def test_png_image(self, temp_dir):
        path = self._make_image(temp_dir, "diagram.png", "PNG")
        content, report = parse_file_with_report(path)

        assert content.file_type == "image"
        assert len(content.images) == 1
        assert content.images[0]["media_type"] == "image/png"
        assert report["status"] == "parsed"


# ── Notebook parsing ────────────────────────────────────────────────


class TestNotebookParsing:
    """Tests for Jupyter notebook parsing."""

    def _write_notebook(self, path, cells):
        """Helper to write a minimal .ipynb file."""
        nb = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
            "cells": cells,
        }
        path.write_text(json.dumps(nb))

    def test_normal_notebook(self, temp_dir):
        nb_path = temp_dir / "analysis.ipynb"
        self._write_notebook(nb_path, [
            {"cell_type": "code", "source": "import pandas as pd", "metadata": {}, "outputs": []},
            {"cell_type": "markdown", "source": "# Results", "metadata": {}},
        ])

        # nbformat is imported inside _parse_notebook_file
        mock_cell_code = MagicMock()
        mock_cell_code.cell_type = "code"
        mock_cell_code.source = "import pandas as pd"
        mock_cell_code.get.return_value = []

        mock_cell_md = MagicMock()
        mock_cell_md.cell_type = "markdown"
        mock_cell_md.source = "# Results"

        mock_notebook = MagicMock()
        mock_notebook.cells = [mock_cell_code, mock_cell_md]

        mock_nbformat = MagicMock()
        mock_nbformat.read.return_value = mock_notebook

        import sys
        sys.modules["nbformat"] = mock_nbformat
        try:
            content, report = parse_file_with_report(nb_path)
        finally:
            sys.modules.pop("nbformat", None)

        assert content.file_type == "notebook"
        assert content.text_content is not None
        assert "pandas" in content.text_content
        assert content.metadata.get("cells") == 2

    def test_empty_notebook(self, temp_dir):
        nb_path = temp_dir / "blank.ipynb"
        self._write_notebook(nb_path, [])

        mock_notebook = MagicMock()
        mock_notebook.cells = []

        mock_nbformat = MagicMock()
        mock_nbformat.read.return_value = mock_notebook

        import sys
        sys.modules["nbformat"] = mock_nbformat
        try:
            content, report = parse_file_with_report(nb_path)
        finally:
            sys.modules.pop("nbformat", None)

        assert content.file_type == "notebook"
        # No cells means empty or minimal text
        assert content.metadata.get("cells") == 0


# ── Text encoding ───────────────────────────────────────────────────


class TestTextEncoding:
    """Tests for text file parsing with different encodings."""

    def test_utf8_file(self, temp_dir):
        path = temp_dir / "readme.txt"
        path.write_text("Hello, world! \u2603 snowman", encoding="utf-8")

        content, report = parse_file_with_report(path)
        assert content.file_type == "text"
        assert "\u2603" in content.text_content
        assert report["status"] == "parsed"

    def test_latin1_file(self, temp_dir):
        """Latin-1 encoded files are read with errors='replace' so they don't crash."""
        path = temp_dir / "notes.txt"
        path.write_bytes("caf\xe9 na\xefve".encode("latin-1"))

        content, report = parse_file_with_report(path)
        assert content.file_type == "text"
        # The text should still be present (possibly with replacement chars)
        assert content.text_content is not None
        assert len(content.text_content) > 0
        assert report["status"] == "parsed"


# ── Code file parsing ───────────────────────────────────────────────


class TestCodeFileParsing:
    """Tests for code file parsing and truncation."""

    def test_python_file(self, temp_dir):
        path = temp_dir / "solution.py"
        path.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        content = _parse_code_file(path, max_chars=50000)
        assert content.file_type == "code"
        assert "def add" in content.text_content
        assert content.metadata["language"] == "python"
        assert content.metadata["truncated"] is False

    def test_truncation_at_limit(self, temp_dir):
        path = temp_dir / "big.py"
        long_code = "x = 1\n" * 10000  # ~60k chars
        path.write_text(long_code, encoding="utf-8")

        content = _parse_code_file(path, max_chars=100)
        assert content.file_type == "code"
        # Smart chunking may overshoot max_chars to respect code boundaries,
        # but the result must be significantly shorter than the original
        assert len(content.text_content) < len(long_code)
        assert content.metadata["truncated"] is True
        assert content.metadata["original_length"] == len(long_code)


# ── process_student_submission ──────────────────────────────────────


class TestProcessStudentSubmission:
    """Tests for processing a whole student directory with mixed files."""

    def test_mixed_directory(self, temp_dir):
        student_dir = temp_dir / "alice"
        student_dir.mkdir()

        # Create mixed files
        (student_dir / "main.py").write_text("print('hello')", encoding="utf-8")
        (student_dir / "README.md").write_text("# My Project", encoding="utf-8")
        (student_dir / "data.txt").write_text("some data here", encoding="utf-8")

        contents, report = process_student_submission(student_dir, "alice", session_id=1)

        assert isinstance(contents, list)
        assert len(contents) == 3
        assert isinstance(report, IngestionReport)
        assert report.student_id == "alice"
        assert report.session_id == 1
        assert len(report.files_received) == 3
        assert len(report.files_parsed) == 3
        assert len(report.files_failed) == 0
        assert report.total_text_chars > 0

    def test_hidden_files_skipped(self, temp_dir):
        student_dir = temp_dir / "bob"
        student_dir.mkdir()

        (student_dir / "main.py").write_text("x = 1", encoding="utf-8")
        (student_dir / ".DS_Store").write_bytes(b"\x00\x00")
        (student_dir / ".gitignore").write_text("*.pyc", encoding="utf-8")

        contents, report = process_student_submission(student_dir, "bob", session_id=2)

        filenames = [c.filename for c in contents]
        assert "main.py" in filenames
        assert ".DS_Store" not in filenames
        assert ".gitignore" not in filenames

    def test_empty_directory(self, temp_dir):
        student_dir = temp_dir / "empty_student"
        student_dir.mkdir()

        contents, report = process_student_submission(student_dir, "empty", session_id=3)

        assert contents == []
        assert len(report.files_received) == 0

    def test_report_to_dict(self, temp_dir):
        student_dir = temp_dir / "carol"
        student_dir.mkdir()
        (student_dir / "code.py").write_text("pass", encoding="utf-8")

        _contents, report = process_student_submission(student_dir, "carol", session_id=4)
        d = report.to_dict()

        assert d["student_id"] == "carol"
        assert d["session_id"] == 4
        assert "summary" in d
        assert d["summary"]["received"] >= 1


# ── ExtractedContent dataclass ──────────────────────────────────────


class TestExtractedContent:
    def test_to_dict_short_text(self):
        ec = ExtractedContent(filename="a.py", file_type="code", text_content="short")
        d = ec.to_dict()
        assert d["filename"] == "a.py"
        assert d["text_content_preview"] == "short"
        assert d["text_length"] == 5

    def test_to_dict_long_text_truncated(self):
        ec = ExtractedContent(filename="big.py", file_type="code", text_content="x" * 300)
        d = ec.to_dict()
        assert d["text_content_preview"].endswith("...")
        assert d["text_length"] == 300

    def test_to_dict_no_text(self):
        ec = ExtractedContent(filename="img.png", file_type="image")
        d = ec.to_dict()
        assert d["text_content_preview"] is None
        assert d["text_length"] == 0
