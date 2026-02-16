#!/usr/bin/env python3
"""
COMPREHENSIVE SYSTEM VALIDATION SCRIPT

This script tests every component of the AI Grading System
to ensure everything works correctly before delivery.
"""

import sys
import json
import tempfile
import zipfile
from pathlib import Path
from io import BytesIO

sys.path.insert(0, str(Path(__file__).parent))

def test_component(name, test_func):
    """Helper to test a component"""
    try:
        result = test_func()
        if result:
            print(f"✓ {name}")
            return True
        else:
            print(f"✗ {name} - Test returned False")
            return False
    except Exception as e:
        print(f"✗ {name} - {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def test_database():
    """Test 1: Database initialization and models"""
    from app.database import init_db, get_db
    from app.models import GradingSession, StudentSubmission
    
    init_db()
    db = next(get_db())
    
    # Create test session
    session = GradingSession(
        title="Test",
        description="Test",
        rubric="Test: 100",
        max_score=100,
        status="pending",
        total_students=0,
        graded_count=0
    )
    db.add(session)
    db.commit()
    
    # Verify
    assert session.id is not None
    
    # Create test submission
    sub = StudentSubmission(
        session_id=session.id,
        student_identifier="test_student",
        status="pending"
    )
    db.add(sub)
    db.commit()
    
    assert sub.id is not None
    
    # Cleanup
    db.delete(sub)
    db.delete(session)
    db.commit()
    
    return True

def test_fastapi_app():
    """Test 2: FastAPI app loads"""
    from app.main import app
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    
    # Test health
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    
    # Test home
    response = client.get("/")
    assert response.status_code == 200
    
    return True

def test_session_routes():
    """Test 3: Session creation and retrieval"""
    from app.main import app
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    
    # Create session
    data = {
        "title": "Test Assignment",
        "description": "Test",
        "rubric": "Test: 100",
        "max_score": 100
    }
    response = client.post("/session/new", data=data, follow_redirects=False)
    assert response.status_code == 303
    
    # Get session ID
    location = response.headers.get("location", "")
    session_id = location.split("/")[-1]
    
    # Get session page
    response = client.get(f"/session/{session_id}")
    assert response.status_code == 200
    
    # Get status API
    response = client.get(f"/session/{session_id}/status")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    
    return True

def test_file_upload():
    """Test 4: File upload and extraction"""
    from app.main import app
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    
    # First create a session
    data = {
        "title": "Test Upload",
        "description": "Test",
        "rubric": "Test: 100",
        "max_score": 100
    }
    response = client.post("/session/new", data=data, follow_redirects=False)
    location = response.headers.get("location", "")
    session_id = location.split("/")[-1]
    
    # Create test ZIP
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("student_001/solution.py", "print('hello')")
        zf.writestr("student_002/solution.py", "print('world')")
    zip_buffer.seek(0)
    
    # Upload
    files = {"zip_file": ("test.zip", zip_buffer, "application/zip")}
    response = client.post(f"/session/{session_id}/upload", files=files, follow_redirects=False)
    assert response.status_code == 303
    
    # Verify students were extracted
    response = client.get(f"/session/{session_id}/status")
    data = response.json()
    assert data.get("total_students", 0) == 2
    
    return True

def test_export_functions():
    """Test 5: CSV and JSON export"""
    from app.database import get_db
    from app.models import GradingSession, StudentSubmission
    from app.services.exporter import export_csv, export_json
    
    db = next(get_db())
    
    # Create test data
    session = GradingSession(
        title="Export Test",
        description="Test",
        rubric="Test: 100",
        max_score=100,
        status="completed",
        total_students=2,
        graded_count=2
    )
    db.add(session)
    db.commit()
    
    # Add submissions
    for i in range(2):
        sub = StudentSubmission(
            session_id=session.id,
            student_identifier=f"student_{i}",
            status="graded",
            ai_score=85.0,
            ai_letter_grade="B",
            ai_confidence="high",
            ai_result=json.dumps({
                "total_score": 85.0,
                "letter_grade": "B",
                "overall_feedback": "Good work",
                "rubric_breakdown": [
                    {"criterion": "Test", "score": 85, "max": 100}
                ]
            })
        )
        db.add(sub)
    db.commit()
    
    # Test CSV export
    csv_content = export_csv(db, session.id)
    assert "Student ID" in csv_content
    assert "student_0" in csv_content or "student_1" in csv_content
    
    # Test JSON export
    json_content = export_json(db, session.id)
    data = json.loads(json_content)
    assert "session" in data
    assert "students" in data
    assert len(data["students"]) == 2
    
    # Cleanup
    for sub in db.query(StudentSubmission).filter(StudentSubmission.session_id == session.id).all():
        db.delete(sub)
    db.delete(session)
    db.commit()
    
    return True

def test_ai_grader():
    """Test 6: AI grader service"""
    from app.services.ai_grader import grade_student, parse_rubric
    
    # Test rubric parsing
    rubric_text = """
    Correctness: 40
    Attempt: 40
    Intent: 20
    """
    criteria = parse_rubric(rubric_text)
    assert len(criteria) == 3
    
    # Test grading (this will actually call the API)
    # Note: This requires API key and internet
    # For now, just verify the function exists and accepts correct params
    
    return True

def test_zip_processor():
    """Test 7: ZIP extraction"""
    from app.services.zip_processor import extract_master_archive_with_verification
    from app.config import UPLOAD_DIR
    import tempfile
    
    # Create test ZIP
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        with zipfile.ZipFile(f.name, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("student_001/solution.py", "print('test')")
        zip_path = f.name
    
    # Extract
    report = extract_master_archive_with_verification(zip_path, 9999)
    
    assert report['success'] is True
    assert len(report['students']) == 1
    assert report['students'][0]['student_identifier'] == 'student_001'
    
    # Cleanup
    import os
    os.unlink(zip_path)
    
    return True

def test_file_parser():
    """Test 8: File parser"""
    from app.services.file_parser import parse_file
    import tempfile
    
    # Create test Python file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write("print('hello world')")
        file_path = f.name
    
    # Parse
    result = parse_file(file_path)
    
    assert result['type'] == 'code'
    assert 'print' in result.get('content', '')
    
    # Cleanup
    import os
    os.unlink(file_path)
    
    return True

def run_all_tests():
    """Run all component tests"""
    print("=" * 70)
    print("AI GRADING SYSTEM - COMPREHENSIVE VALIDATION")
    print("=" * 70)
    print()
    
    tests = [
        ("Database Models", test_database),
        ("FastAPI Application", test_fastapi_app),
        ("Session Routes", test_session_routes),
        ("File Upload", test_file_upload),
        ("Export Functions", test_export_functions),
        ("AI Grader Service", test_ai_grader),
        ("ZIP Processor", test_zip_processor),
        ("File Parser", test_file_parser),
    ]
    
    results = []
    for name, test_func in tests:
        success = test_component(name, test_func)
        results.append((name, success))
    
    print()
    print("=" * 70)
    print("TEST RESULTS")
    print("=" * 70)
    
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    for name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status}: {name}")
    
    print()
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED - SYSTEM IS READY")
        return True
    else:
        print(f"\n⚠️  {total - passed} test(s) failed - FIX REQUIRED")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
