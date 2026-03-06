"""Regression tests for parser guardrails and vision routing."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.services.file_parser import parse_file as legacy_parse_file
from app.services.file_parser_enhanced import parse_file_with_report, process_student_submission
from app.services.zip_processor import _should_ignore


ONE_PIXEL_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+b5RkAAAAASUVORK5CYII="
)


def _write_one_pixel_png(path: Path) -> None:
    path.write_bytes(base64.b64decode(ONE_PIXEL_PNG_BASE64))


def test_office_lock_docx_is_skipped_across_parsers(tmp_path: Path) -> None:
    lock_file = tmp_path / "~$AI (1).docx"
    lock_file.write_bytes(b"not-a-real-docx")

    legacy = legacy_parse_file(lock_file)
    assert legacy["type"] == "skipped"
    assert "skipped" in legacy.get("note", "").lower()

    content, report = parse_file_with_report(lock_file)
    assert report["status"] == "skipped"
    assert content.file_type == "skipped"
    assert content.metadata.get("reason") == "transient_system_file"


def test_process_student_submission_ignores_office_lock_files(tmp_path: Path) -> None:
    student_dir = tmp_path / "student"
    student_dir.mkdir()

    (student_dir / "solution.py").write_text("print('ok')", encoding="utf-8")
    (student_dir / "~$draft.docx").write_bytes(b"lock-file")

    extracted, report = process_student_submission(student_dir, "student", 1)

    names = {item.filename for item in extracted}
    assert names == {"solution.py"}
    assert all("~$" not in item["filename"] for item in report.files_received)
    assert not report.errors


def test_vision_extraction_is_limited_to_visual_file_types(tmp_path: Path) -> None:
    fitz = pytest.importorskip("fitz")
    docx = pytest.importorskip("docx")

    code_path = tmp_path / "solution.py"
    code_path.write_text("def solve():\n    return 42\n", encoding="utf-8")

    text_path = tmp_path / "notes.txt"
    text_path.write_text("plain notes", encoding="utf-8")

    image_path = tmp_path / "diagram.png"
    _write_one_pixel_png(image_path)

    pdf_path = tmp_path / "submission.pdf"
    pdf_doc = fitz.open()
    pdf_page = pdf_doc.new_page()
    pdf_page.insert_text((72, 72), "PDF visual/text content")
    pdf_doc.save(str(pdf_path))
    pdf_doc.close()

    docx_path = tmp_path / "report.docx"
    document = docx.Document()
    document.add_paragraph("DOCX with embedded image")
    document.add_picture(str(image_path))
    document.save(str(docx_path))

    parsed = {}
    for path in (code_path, text_path, image_path, pdf_path, docx_path):
        content, report = parse_file_with_report(path)
        assert report["status"] == "parsed"
        parsed[path.name] = content

    assert len(parsed["solution.py"].images) == 0
    assert len(parsed["notes.txt"].images) == 0
    assert len(parsed["diagram.png"].images) == 1
    assert len(parsed["submission.pdf"].images) >= 1
    assert len(parsed["report.docx"].images) >= 1

    files_with_images = {name for name, content in parsed.items() if content.images}
    assert files_with_images.issubset({"diagram.png", "submission.pdf", "report.docx"})


def test_zip_processor_ignores_transient_system_names() -> None:
    assert _should_ignore("~$AI (1).docx")
    assert _should_ignore("._diagram.png")
    assert not _should_ignore("solution.py")


def test_invalid_excel_and_powerpoint_are_marked_failed(tmp_path: Path) -> None:
    bad_excel = tmp_path / "broken.xlsx"
    bad_excel.write_text("not-an-excel-file", encoding="utf-8")
    excel_content, excel_report = parse_file_with_report(bad_excel)
    assert excel_content.file_type == "error"
    assert excel_report["status"] == "failed"
    assert excel_report.get("error")

    bad_ppt = tmp_path / "broken.pptx"
    bad_ppt.write_text("not-a-powerpoint-file", encoding="utf-8")
    ppt_content, ppt_report = parse_file_with_report(bad_ppt)
    assert ppt_content.file_type == "error"
    assert ppt_report["status"] == "failed"
    assert ppt_report.get("error")


def test_valid_excel_and_powerpoint_are_parsed(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    pptx = pytest.importorskip("pptx")

    excel_path = tmp_path / "valid.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Scores"
    sheet.append(["Student", "Score"])
    sheet.append(["Ayesha", 92])
    workbook.save(excel_path)
    workbook.close()

    ppt_path = tmp_path / "valid.pptx"
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    if slide.shapes.title:
        slide.shapes.title.text = "Demo Slide"
    if len(slide.placeholders) > 1:
        slide.placeholders[1].text = "PowerPoint parsing check"
    presentation.save(str(ppt_path))

    excel_content, excel_report = parse_file_with_report(excel_path)
    assert excel_report["status"] == "parsed"
    assert excel_content.file_type == "excel"
    assert "Ayesha" in (excel_content.text_content or "")

    ppt_content, ppt_report = parse_file_with_report(ppt_path)
    assert ppt_report["status"] == "parsed"
    assert ppt_content.file_type == "powerpoint"
    assert "Demo Slide" in (ppt_content.text_content or "")
