"""Backward-compatible file parser interface.

Historically, parts of the codebase and tests imported symbols from
``app.services.file_parser_fixed``. The enhanced parser now lives in
``file_parser_enhanced``. This module re-exports the enhanced interface so
legacy imports continue to work without duplication.
"""

from app.services.file_parser_enhanced import (
    ExtractedContent,
    IngestionReport,
    _parse_pdf_mixed,
    detect_file_dependencies,
    detect_image_sequences,
    get_file_type_summary,
    parse_file_with_report,
    prepare_content_for_llm,
    process_student_submission,
)

__all__ = [
    "ExtractedContent",
    "IngestionReport",
    "_parse_pdf_mixed",
    "detect_file_dependencies",
    "detect_image_sequences",
    "get_file_type_summary",
    "parse_file_with_report",
    "prepare_content_for_llm",
    "process_student_submission",
]
