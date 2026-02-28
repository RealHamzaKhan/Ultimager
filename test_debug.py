#!/usr/bin/env python3
"""Debug test to verify PDF processing and ingestion reports."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.services.file_parser_fixed import process_student_submission, parse_file_with_report
from app.services.ai_grader_enhanced import parse_rubric, grade_student
import asyncio

def test_pdf_processing():
    """Test PDF processing with an actual PDF file."""
    print("\n=== Testing PDF Processing ===")
    
    # Create a test directory structure
    with tempfile.TemporaryDirectory() as tmpdir:
        student_dir = Path(tmpdir) / "test_student"
        student_dir.mkdir()
        
        # Create a simple test file
        test_file = student_dir / "test.py"
        test_file.write_text("print('Hello World')")
        
        # Process the submission
        extracted, report = process_student_submission(student_dir, "test_student", 1)
        
        print(f"Files received: {len(report.files_received)}")
        print(f"Files parsed: {len(report.files_parsed)}")
        print(f"Total text chars: {report.total_text_chars}")
        print(f"Total images: {report.total_images}")
        
        # Save report
        import json
        report_dict = report.to_dict()
        print(f"\nIngestion report keys: {list(report_dict.keys())}")
        print(f"Summary: {report_dict.get('summary')}")
        
        return extracted, report

def test_rubric_parsing():
    """Test rubric parsing with various formats."""
    print("\n=== Testing Rubric Parsing ===")
    
    test_cases = [
        ("Problem format", """Problem 1: Campus Navigation System - 4 points
Problem 2: Ride-Sharing Route Optimization - 3 points
Problem 3: 8-Puzzle Solver - 3 points
Total: 10"""),
        
        ("Simple format", """Correctness: 40
Code Quality: 30
Documentation: 30"""),
    ]
    
    for name, rubric in test_cases:
        criteria = parse_rubric(rubric)
        total = sum(c['max'] for c in criteria)
        print(f"\n{name}:")
        print(f"  Criteria: {len(criteria)} (Total: {total})")
        for c in criteria:
            print(f"    - {c['criterion']}: {c['max']}")
        
        if total == 0 or len(criteria) == 0:
            print("  ❌ FAILED - No criteria parsed!")
        else:
            print("  ✅ PASSED")

def test_full_grading_flow():
    """Test the full grading flow with a mock submission."""
    print("\n=== Testing Full Grading Flow ===")
    
    import tempfile
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmpdir:
        student_dir = Path(tmpdir) / "test_student"
        student_dir.mkdir()
        
        # Create multiple files
        (student_dir / "solution.py").write_text("def solve():\n    return 42")
        (student_dir / "README.md").write_text("# Solution\nThis is my solution")
        
        # Process
        extracted, report = process_student_submission(student_dir, "test_student", 1)
        
        print(f"Extracted {len(extracted)} files:")
        for content in extracted:
            print(f"  - {content.filename}: {content.file_type}")
            if content.text_content:
                print(f"    Text: {len(content.text_content)} chars")
            if content.images:
                print(f"    Images: {len(content.images)}")
        
        # Check ingestion report
        import json
        report_json = json.dumps(report.to_dict())
        report_loaded = json.loads(report_json)
        
        print(f"\nIngestion report saved successfully: {len(report_json)} bytes")
        print(f"Can be loaded back: {'summary' in report_loaded}")

if __name__ == "__main__":
    test_rubric_parsing()
    test_pdf_processing()
    test_full_grading_flow()
    print("\n✅ All debug tests completed!")
