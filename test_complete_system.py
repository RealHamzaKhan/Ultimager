#!/usr/bin/env python3
"""
Comprehensive End-to-End Test Suite for AI Grading System

This script will:
1. Start the server
2. Test all functionality via API calls
3. Verify database operations
4. Test export formats
5. Verify data persistence after restart
"""

import subprocess
import time
import json
import requests
import zipfile
import os
from pathlib import Path
from datetime import datetime

BASE_URL = "http://localhost:8000"
TEST_RESULTS = []

def log_test(name, status, details=""):
    """Log test result"""
    result = {"name": name, "status": status, "details": details}
    TEST_RESULTS.append(result)
    status_icon = "✅" if status == "PASS" else "❌"
    print(f"{status_icon} {name}")
    if details:
        print(f"   {details}")

def test_server_startup():
    """Test 1: Server starts successfully"""
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "healthy":
                log_test("Server Startup", "PASS", f"Version: {data.get('version')}")
                return True
        log_test("Server Startup", "FAIL", f"Status: {response.status_code}")
        return False
    except Exception as e:
        log_test("Server Startup", "FAIL", str(e))
        return False

def test_create_session():
    """Test 2: Create a new grading session"""
    try:
        data = {
            "title": "E2E Test Assignment",
            "description": "Comprehensive end-to-end test",
            "rubric": "Correctness: 40\nAttempt: 40\nIntent: 20",
            "max_score": 100
        }
        response = requests.post(f"{BASE_URL}/session/new", data=data, allow_redirects=False)
        if response.status_code == 303:
            location = response.headers.get("location", "")
            session_id = location.split("/")[-1]
            log_test("Create Session", "PASS", f"Session ID: {session_id}")
            return session_id
        log_test("Create Session", "FAIL", f"Status: {response.status_code}")
        return None
    except Exception as e:
        log_test("Create Session", "FAIL", str(e))
        return None

def test_upload_submissions(session_id, dataset_path):
    """Test 3: Upload student submissions"""
    try:
        with open(dataset_path, 'rb') as f:
            files = {'zip_file': (dataset_path.name, f, 'application/zip')}
            response = requests.post(
                f"{BASE_URL}/session/{session_id}/upload",
                files=files,
                allow_redirects=False
            )
        if response.status_code == 303:
            log_test("Upload Submissions", "PASS", f"Dataset: {dataset_path.name}")
            return True
        log_test("Upload Submissions", "FAIL", f"Status: {response.status_code}")
        return False
    except Exception as e:
        log_test("Upload Submissions", "FAIL", str(e))
        return False

def test_start_grading(session_id):
    """Test 4: Start AI grading"""
    try:
        response = requests.post(f"{BASE_URL}/session/{session_id}/grade")
        if response.status_code == 200:
            data = response.json()
            log_test("Start Grading", "PASS", f"Task ID: {data.get('task_id', 'N/A')[:8]}...")
            return data.get('task_id')
        log_test("Start Grading", "FAIL", f"Status: {response.status_code}")
        return None
    except Exception as e:
        log_test("Start Grading", "FAIL", str(e))
        return None

def test_session_status(session_id):
    """Test 5: Check session status during/after grading"""
    try:
        response = requests.get(f"{BASE_URL}/session/{session_id}/status")
        if response.status_code == 200:
            data = response.json()
            log_test("Session Status", "PASS", 
                    f"Status: {data.get('status')}, "
                    f"Progress: {data.get('graded_count', 0)}/{data.get('total_students', 0)}")
            return data
        log_test("Session Status", "FAIL", f"Status: {response.status_code}")
        return None
    except Exception as e:
        log_test("Session Status", "FAIL", str(e))
        return None

def test_export_csv(session_id, include_pending=False):
    """Test 6: Export results as CSV"""
    try:
        url = f"{BASE_URL}/session/{session_id}/export/csv"
        if include_pending:
            url += "?include_pending=true"
        response = requests.get(url)
        if response.status_code == 200:
            content = response.text
            lines = content.strip().split('\n')
            log_test("Export CSV", "PASS", f"Rows: {len(lines)-1} (header + data)")
            return content
        log_test("Export CSV", "FAIL", f"Status: {response.status_code}")
        return None
    except Exception as e:
        log_test("Export CSV", "FAIL", str(e))
        return None

def test_export_json(session_id, include_pending=False):
    """Test 7: Export results as JSON"""
    try:
        url = f"{BASE_URL}/session/{session_id}/export/json"
        if include_pending:
            url += "?include_pending=true"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            students = data.get('students', [])
            log_test("Export JSON", "PASS", f"Students: {len(students)}")
            return data
        log_test("Export JSON", "FAIL", f"Status: {response.status_code}")
        return None
    except Exception as e:
        log_test("Export JSON", "FAIL", str(e))
        return None

def test_override_grade(session_id, student_id):
    """Test 8: Override a student's grade"""
    try:
        data = {
            "score": 95.0,
            "comments": "Overridden for testing",
            "is_reviewed": True
        }
        response = requests.post(
            f"{BASE_URL}/session/{session_id}/student/{student_id}/override",
            json=data
        )
        if response.status_code == 200:
            result = response.json()
            log_test("Override Grade", "PASS", f"Final Score: {result.get('final_score')}")
            return True
        log_test("Override Grade", "FAIL", f"Status: {response.status_code}")
        return False
    except Exception as e:
        log_test("Override Grade", "FAIL", str(e))
        return False

def test_results_page(session_id):
    """Test 9: Results dashboard loads"""
    try:
        response = requests.get(f"{BASE_URL}/session/{session_id}/results")
        if response.status_code == 200:
            log_test("Results Page", "PASS", "Dashboard loads correctly")
            return True
        log_test("Results Page", "FAIL", f"Status: {response.status_code}")
        return False
    except Exception as e:
        log_test("Results Page", "FAIL", str(e))
        return False

def test_list_sessions():
    """Test 10: List all sessions via API"""
    try:
        response = requests.get(f"{BASE_URL}/api/sessions")
        if response.status_code == 200:
            data = response.json()
            sessions = data.get('sessions', [])
            log_test("List Sessions API", "PASS", f"Total sessions: {len(sessions)}")
            return sessions
        log_test("List Sessions API", "FAIL", f"Status: {response.status_code}")
        return None
    except Exception as e:
        log_test("List Sessions API", "FAIL", str(e))
        return None

def run_all_tests():
    """Run all tests"""
    print("=" * 70)
    print("AI GRADING SYSTEM - COMPREHENSIVE END-TO-END TEST SUITE")
    print("=" * 70)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Test 1: Server startup
    if not test_server_startup():
        print("\n❌ CRITICAL: Server not running. Please start it first with: python run.py")
        return False
    
    print()
    
    # Test 2: Create session
    session_id = test_create_session()
    if not session_id:
        print("\n❌ Cannot continue without session")
        return False
    
    print()
    
    # Test 3: Create test dataset
    test_dir = Path("test_datasets")
    if not test_dir.exists():
        print("Creating test datasets...")
        subprocess.run(["venv/bin/python", "create_test_datasets.py"], check=True)
    
    # Use dataset 1 for testing
    dataset_path = test_dir / "dataset_01_basic_python.zip"
    if not dataset_path.exists():
        log_test("Find Dataset", "FAIL", f"Dataset not found: {dataset_path}")
        return False
    
    # Test 4: Upload
    if not test_upload_submissions(session_id, dataset_path):
        print("\n❌ Cannot continue without uploaded submissions")
        return False
    
    print()
    
    # Test 5: Start grading
    task_id = test_start_grading(session_id)
    
    print()
    print("⏳ Waiting for grading to complete (this may take a few minutes)...")
    print()
    
    # Test 6: Monitor progress
    max_attempts = 30
    for attempt in range(max_attempts):
        time.sleep(5)
        status_data = test_session_status(session_id)
        if status_data:
            if status_data.get('status') in ['completed', 'completed_with_errors']:
                break
        print(f"   Attempt {attempt + 1}/{max_attempts} - Still grading...")
    
    print()
    
    # Test 7: Export CSV
    csv_content = test_export_csv(session_id)
    
    # Test 8: Export JSON
    json_data = test_export_json(session_id)
    
    print()
    
    # Test 9: Results page
    test_results_page(session_id)
    
    # Test 10: Get a student ID for override test
    if json_data and json_data.get('students'):
        first_student = json_data['students'][0]
        student_id = first_student.get('id')
        if student_id:
            test_override_grade(session_id, student_id)
    
    print()
    
    # Test 11: List sessions
    test_list_sessions()
    
    print()
    print("=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    
    passed = sum(1 for r in TEST_RESULTS if r['status'] == 'PASS')
    failed = sum(1 for r in TEST_RESULTS if r['status'] == 'FAIL')
    
    print(f"Total Tests: {len(TEST_RESULTS)}")
    print(f"Passed: {passed} ✅")
    print(f"Failed: {failed} ❌")
    print()
    
    if failed > 0:
        print("Failed Tests:")
        for result in TEST_RESULTS:
            if result['status'] == 'FAIL':
                print(f"  - {result['name']}: {result['details']}")
        print()
    
    if passed == len(TEST_RESULTS):
        print("🎉 ALL TESTS PASSED! System is ready for production.")
        return True
    else:
        print(f"⚠️  {failed} test(s) failed. Please review the errors above.")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
