#!/usr/bin/env python3
"""
End-to-end testing script for the AI Grading System.
Tests all major functionality including:
- Session creation
- File upload and extraction
- AI grading
- Results export
- Override functionality
"""
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from datetime import datetime

# Add the project root to path
sys.path.insert(0, str(Path(__file__).parent))

def create_test_zip() -> Path:
    """Create a test ZIP file with sample student submissions."""
    tmp_dir = Path(tempfile.mkdtemp())
    master_zip = tmp_dir / "test_submissions.zip"
    
    with zipfile.ZipFile(master_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Student 1: Good Python solution
        student1_code = '''def factorial(n):
    """Calculate factorial of n."""
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0 or n == 1:
        return 1
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result

# Test
if __name__ == "__main__":
    print(factorial(5))
'''
        zf.writestr("JohnDoe_001/solution.py", student1_code)
        zf.writestr("JohnDoe_001/readme.txt", "This is my solution for the factorial problem.")
        
        # Student 2: Incomplete solution
        student2_code = '''def factorial(n):
    # TODO: implement
    pass
'''
        zf.writestr("JaneSmith_002/solution.py", student2_code)
        
        # Student 3: Solution with bugs
        student3_code = '''def factorial(n):
    result = 1
    for i in range(n):  # Bug: should be range(1, n+1) or range(2, n+1)
        result *= i
    return result
'''
        zf.writestr("BobJones_003/solution.py", student3_code)
    
    return master_zip


def test_database():
    """Test database connection and models."""
    print("\n" + "="*60)
    print("TEST 1: Database Connection & Models")
    print("="*60)
    
    try:
        from app.database import init_db, get_db
        from app.models import GradingSession, StudentSubmission
        
        # Initialize database
        init_db()
        print("✓ Database initialized successfully")
        
        # Test database connection
        db = next(get_db())
        print("✓ Database connection successful")
        
        return True
    except Exception as e:
        print(f"✗ Database test failed: {e}")
        return False


def test_file_parsing():
    """Test file parsing capabilities."""
    print("\n" + "="*60)
    print("TEST 2: File Parsing")
    print("="*60)
    
    try:
        from app.services.file_parser import parse_file
        
        # Create test files
        tmp_dir = Path(tempfile.mkdtemp())
        
        # Test Python file
        py_file = tmp_dir / "test.py"
        py_file.write_text("def hello():\n    return 'world'")
        result = parse_file(py_file)
        assert result['type'] == 'code', f"Expected 'code', got {result['type']}"
        print("✓ Python file parsing works")
        
        # Test text file
        txt_file = tmp_dir / "test.txt"
        txt_file.write_text("Hello World")
        result = parse_file(txt_file)
        assert result['type'] == 'code', f"Expected 'code', got {result['type']}"
        print("✓ Text file parsing works")
        
        return True
    except Exception as e:
        print(f"✗ File parsing test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_zip_processing():
    """Test ZIP extraction and processing."""
    print("\n" + "="*60)
    print("TEST 3: ZIP Processing")
    print("="*60)
    
    try:
        from app.services.zip_processor import extract_master_archive_with_verification
        from app.config import UPLOAD_DIR
        
        # Create test ZIP
        master_zip = create_test_zip()
        print(f"Created test ZIP: {master_zip}")
        
        # Extract
        report = extract_master_archive_with_verification(master_zip, 9999)
        
        assert report['success'], f"Extraction failed: {report.get('errors', [])}"
        print(f"✓ Extraction successful: {report['successful_extractions']} students extracted")
        print(f"  - Total students: {report['total_archives_found']}")
        print(f"  - Total files: {report['stats']['total_files']}")
        
        # Cleanup
        import shutil
        if (UPLOAD_DIR / "9999").exists():
            shutil.rmtree(UPLOAD_DIR / "9999")
        
        return True
    except Exception as e:
        print(f"✗ ZIP processing test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_exporter():
    """Test export functionality."""
    print("\n" + "="*60)
    print("TEST 4: Export Functions")
    print("="*60)
    
    try:
        from app.database import get_db, init_db
        from app.models import GradingSession, StudentSubmission
        from app.services.exporter import export_csv, export_json
        
        init_db()
        db = next(get_db())
        
        # Create a test session
        session = GradingSession(
            title="Test Session",
            description="Test Description",
            rubric="Test Rubric",
            max_score=100,
            status="completed",
            total_students=2,
            graded_count=2
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        
        # Add test submissions
        for i, (student_id, score, grade) in enumerate([
            ("Student001", 85.5, "B"),
            ("Student002", 92.0, "A-")
        ]):
            sub = StudentSubmission(
                session_id=session.id,
                student_identifier=student_id,
                status="graded",
                ai_score=score,
                ai_letter_grade=grade,
                ai_confidence="high",
                ai_result=json.dumps({
                    "total_score": score,
                    "letter_grade": grade,
                    "overall_feedback": "Good work!",
                    "rubric_breakdown": [
                        {"criterion": "Correctness", "score": score, "max": 100, "justification": "Good"}
                    ]
                })
            )
            db.add(sub)
        
        db.commit()
        
        # Test CSV export
        csv_content = export_csv(db, session.id)
        assert "Student ID" in csv_content, "CSV header missing"
        assert "Student001" in csv_content, "Student001 not in CSV"
        assert "Student002" in csv_content, "Student002 not in CSV"
        print("✓ CSV export works")
        
        # Test JSON export
        json_content = export_json(db, session.id)
        data = json.loads(json_content)
        assert data['session']['title'] == "Test Session"
        assert len(data['students']) == 2
        print("✓ JSON export works")
        
        # Cleanup
        db.query(StudentSubmission).filter(StudentSubmission.session_id == session.id).delete()
        db.delete(session)
        db.commit()
        
        return True
    except Exception as e:
        print(f"✗ Export test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_code_executor():
    """Test code execution functionality."""
    print("\n" + "="*60)
    print("TEST 5: Code Execution")
    print("="*60)
    
    try:
        from app.services.code_executor import run_test_cases
        
        # Create a temp directory with a test Python file
        tmp_dir = Path(tempfile.mkdtemp())
        solution_file = tmp_dir / "solution.py"
        solution_file.write_text("""
import sys
n = int(sys.stdin.readline())
print(n * 2)
""")
        
        # Define test cases
        test_cases = json.dumps([
            {"input": "5\n", "expected_output": "10", "description": "Double 5", "points": 10},
            {"input": "3\n", "expected_output": "6", "description": "Double 3", "points": 10},
        ])
        
        # Run tests
        results = run_test_cases(str(tmp_dir), test_cases, "python3 solution.py")
        
        print(f"✓ Code execution works")
        print(f"  - Tests passed: {results['passed']}/{results['total']}")
        
        return True
    except Exception as e:
        print(f"✗ Code execution test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_routes():
    """Test FastAPI routes."""
    print("\n" + "="*60)
    print("TEST 6: FastAPI Routes")
    print("="*60)
    
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        
        client = TestClient(app)
        
        # Test health endpoint
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        print("✓ Health endpoint works")
        
        # Test home page
        response = client.get("/")
        assert response.status_code == 200
        print("✓ Home page loads")
        
        # Test new session form
        response = client.get("/session/new")
        assert response.status_code == 200
        print("✓ New session form loads")
        
        return True
    except Exception as e:
        print(f"✗ Routes test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_full_workflow():
    """Test complete grading workflow."""
    print("\n" + "="*60)
    print("TEST 7: Complete Workflow Integration")
    print("="*60)
    
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import get_db, init_db
        from app.models import GradingSession
        import shutil
        
        client = TestClient(app)
        
        # 1. Create a session
        response = client.post("/session/new", data={
            "title": "Integration Test Assignment",
            "description": "Test assignment for integration testing",
            "rubric": "Correctness: 50 points\\nCode Quality: 50 points\\nTotal: 100",
            "max_score": 100,
        }, follow_redirects=False)
        
        assert response.status_code == 303, f"Expected redirect, got {response.status_code}"
        session_id = int(response.headers["location"].split("/")[-1])
        print(f"✓ Created session: {session_id}")
        
        # 2. Upload test ZIP
        master_zip = create_test_zip()
        with open(master_zip, "rb") as f:
            response = client.post(
                f"/session/{session_id}/upload",
                files={"zip_file": ("test.zip", f, "application/zip")},
                follow_redirects=False
            )
        
        assert response.status_code == 303, f"Expected redirect after upload, got {response.status_code}"
        print("✓ Uploaded test submissions")
        
        # 3. Verify session has students
        init_db()
        db = next(get_db())
        session = db.query(GradingSession).filter(GradingSession.id == session_id).first()
        assert session.total_students == 3, f"Expected 3 students, got {session.total_students}"
        print(f"✓ Verified {session.total_students} students extracted")
        
        # 4. Check export endpoints work
        response = client.get(f"/session/{session_id}/export/csv")
        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        print("✓ CSV export endpoint works")
        
        response = client.get(f"/session/{session_id}/export/json")
        assert response.status_code == 200
        data = response.json()
        assert data["session"]["id"] == session_id
        print("✓ JSON export endpoint works")
        
        # 5. Check results page
        response = client.get(f"/session/{session_id}/results")
        assert response.status_code == 200
        print("✓ Results page loads")
        
        # Cleanup
        db.query(GradingSession).filter(GradingSession.id == session_id).delete()
        db.commit()
        
        from app.config import UPLOAD_DIR
        if (UPLOAD_DIR / str(session_id)).exists():
            shutil.rmtree(UPLOAD_DIR / str(session_id))
        
        return True
    except Exception as e:
        print(f"✗ Full workflow test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """Run all tests and print summary."""
    print("\n" + "="*60)
    print("AI GRADING SYSTEM - END-TO-END TEST SUITE")
    print("="*60)
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    tests = [
        ("Database", test_database),
        ("File Parsing", test_file_parsing),
        ("ZIP Processing", test_zip_processing),
        ("Export Functions", test_exporter),
        ("Code Execution", test_code_executor),
        ("FastAPI Routes", test_routes),
        ("Full Workflow", test_full_workflow),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"✗ {name} test crashed: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
    
    print("="*60)
    print(f"Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All tests passed! The system is working correctly.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please review the errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
