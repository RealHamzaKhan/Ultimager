#!/usr/bin/env python3
"""
Comprehensive end-to-end test suite for the AI Grading System.
Tests all major features and verifies correct behavior.
"""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.file_parser_enhanced import (
    parse_file_with_report, 
    process_student_submission,
    ExtractedContent,
    IngestionReport
)
from app.services.ai_grader_enhanced import (
    grade_student,
    generate_rubric_from_description,
    validate_submission_relevance,
    compute_grading_hash,
    parse_rubric
)

def test_rubric_parsing():
    """Test rubric parsing functionality."""
    print("\n=== Test: Rubric Parsing ===")
    
    rubric_text = """
Correctness: 40 points
Code Quality: 20 points
Documentation: 15 points
Total: 75
"""
    
    criteria = parse_rubric(rubric_text)
    assert len(criteria) == 3, f"Expected 3 criteria, got {len(criteria)}"
    assert criteria[0]["criterion"] == "Correctness"
    assert criteria[0]["max"] == 40
    print("✓ Rubric parsing works correctly")


def test_file_parsing():
    """Test file parsing for various formats."""
    print("\n=== Test: File Parsing ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Test code file
        code_file = tmpdir / "solution.py"
        code_file.write_text("def hello():\n    return 'world'")
        content, report = parse_file_with_report(code_file)
        assert content.file_type == "code"
        assert "hello" in content.text_content
        print("✓ Code file parsing works")
        
        # Test text file
        text_file = tmpdir / "readme.txt"
        text_file.write_text("This is a test document")
        content, report = parse_file_with_report(text_file)
        assert content.file_type == "text"
        print("✓ Text file parsing works")
        
        # Test image file (create a simple dummy)
        try:
            from PIL import Image
            img_file = tmpdir / "diagram.png"
            img = Image.new('RGB', (100, 100), color='red')
            img.save(img_file)
            content, report = parse_file_with_report(img_file)
            assert content.file_type == "image"
            assert len(content.images) == 1
            print("✓ Image file parsing works")
        except ImportError:
            print("⚠ PIL not available, skipping image test")


def test_process_student_submission():
    """Test processing a full student submission."""
    print("\n=== Test: Process Student Submission ===")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        student_dir = Path(tmpdir) / "test_student"
        student_dir.mkdir()
        
        # Create multiple files
        (student_dir / "main.py").write_text("print('Hello World')")
        (student_dir / "README.md").write_text("# My Solution\nThis is my submission")
        
        extracted, report = process_student_submission(student_dir, "test_student", 1)
        
        assert len(extracted) == 2
        assert report.student_id == "test_student"
        assert len(report.files_received) == 2
        assert len(report.files_parsed) == 2
        print(f"✓ Processed {len(extracted)} files")
        print(f"✓ Total text chars: {report.total_text_chars}")
        print("✓ Ingestion report generated successfully")


def test_grading_hash():
    """Test grading hash computation for consistency."""
    print("\n=== Test: Grading Hash Consistency ===")
    
    files1 = [
        ExtractedContent(filename="test.py", file_type="code", text_content="print('hello')"),
    ]
    files2 = [
        ExtractedContent(filename="test.py", file_type="code", text_content="print('hello')"),
    ]
    files3 = [
        ExtractedContent(filename="test.py", file_type="code", text_content="print('world')"),
    ]
    
    hash1 = compute_grading_hash(files1, "Rubric: 100 pts", 100)
    hash2 = compute_grading_hash(files2, "Rubric: 100 pts", 100)
    hash3 = compute_grading_hash(files3, "Rubric: 100 pts", 100)
    
    assert hash1 == hash2, "Same inputs should produce same hash"
    assert hash1 != hash3, "Different inputs should produce different hash"
    print("✓ Grading hash consistency verified")


def test_ingestion_report_structure():
    """Test ingestion report data structure."""
    print("\n=== Test: Ingestion Report Structure ===")
    
    report = IngestionReport(
        student_id="student_001",
        session_id=1,
        timestamp="2024-01-01T00:00:00"
    )
    
    report.files_received = [
        {"filename": "test.py", "size_bytes": 100}
    ]
    report.files_parsed = [
        {"filename": "test.py", "status": "parsed", "text_length": 50}
    ]
    report.total_text_chars = 50
    report.total_images = 0
    
    data = report.to_dict()
    
    assert data["student_id"] == "student_001"
    assert data["summary"]["received"] == 1
    assert data["summary"]["parsed"] == 1
    print("✓ Ingestion report structure is valid")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("AI GRADING SYSTEM - COMPREHENSIVE TEST SUITE")
    print("=" * 60)
    
    try:
        test_rubric_parsing()
        test_file_parsing()
        test_process_student_submission()
        test_grading_hash()
        test_ingestion_report_structure()
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        return True
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return False
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
