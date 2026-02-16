"""
Comprehensive Test Dataset Generator for AI Grading System

This script generates multiple test datasets covering:
1. Different file structures (flat, nested, mixed)
2. Different file formats (Python, Java, C++, PDF, Word, Images)
3. Different question types (coding, essay, mixed)
4. Edge cases (empty files, special characters, large files)
5. Error conditions (syntax errors, missing files, corrupted files)
6. Various assignment types (labs, quizzes, projects, exams)
"""

import zipfile
import json
import os
from pathlib import Path
from datetime import datetime

# Test datasets directory
TEST_DATA_DIR = Path("test_datasets")
TEST_DATA_DIR.mkdir(exist_ok=True)


def create_test_dataset_1_basic_python():
    """Dataset 1: Basic Python assignments with different quality levels"""
    print("Creating Dataset 1: Basic Python Assignments...")
    
    zip_path = TEST_DATA_DIR / "dataset_01_basic_python.zip"
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Excellent student
        zf.writestr("student_001_excellent/factorial.py", '''
def factorial(n):
    """
    Calculate factorial of n.
    Args:
        n: Non-negative integer
    Returns:
        Factorial of n
    Raises:
        ValueError: If n is negative
        TypeError: If n is not an integer
    """
    if not isinstance(n, int):
        raise TypeError("n must be an integer")
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0 or n == 1:
        return 1
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result

# Test cases
if __name__ == "__main__":
    print(f"5! = {factorial(5)}")  # Should be 120
    print(f"0! = {factorial(0)}")  # Should be 1
    try:
        factorial(-1)
    except ValueError as e:
        print(f"Error caught: {e}")
''')
        zf.writestr("student_001_excellent/README.md", "# Factorial Implementation\n\nComplete implementation with error handling and documentation.")
        
        # Good student
        zf.writestr("student_002_good/factorial.py", '''
def factorial(n):
    if n < 0:
        return None
    if n == 0 or n == 1:
        return 1
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result

print(factorial(5))
''')
        
        # Average student
        zf.writestr("student_003_average/factorial.py", '''
def factorial(n):
    result = 1
    for i in range(1, n + 1):
        result = result * i
    return result

# Works for positive numbers
print(factorial(5))
''')
        
        # Below average (no error handling)
        zf.writestr("student_004_below/factorial.py", '''
# TODO: Implement factorial

def fact(n):
    pass
''')
        
        # Failing (syntax error)
        zf.writestr("student_005_failing/factorial.py", '''
def factorial(n)
    result = 1
    for i in range(n)
        result *= i
    return result
''')
    
    return zip_path


def create_test_dataset_2_multiple_questions():
    """Dataset 2: Multiple questions with different file naming patterns"""
    print("Creating Dataset 2: Multiple Questions...")
    
    zip_path = TEST_DATA_DIR / "dataset_02_multiple_questions.zip"
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Student with Q1, Q2 format
        zf.writestr("student_001/Q1_reverse_string.py", '''
def reverse_string(s):
    return s[::-1]

# Test
print(reverse_string("hello"))
''')
        zf.writestr("student_001/Q2_palindrome.py", '''
def is_palindrome(s):
    s = s.lower().replace(" ", "")
    return s == s[::-1]

print(is_palindrome("racecar"))
''')
        zf.writestr("student_001/Q3_fibonacci.py", '''
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

print(fibonacci(10))
''')
        
        # Student with question1, question2 format
        zf.writestr("student_002/question1_reverse.py", '''
# Implementation of string reversal
def reverse(s):
    reversed_str = ""
    for char in s:
        reversed_str = char + reversed_str
    return reversed_str
''')
        zf.writestr("student_002/question2_palindrome_check.py", '''
# Check if string is palindrome
def check_palindrome(text):
    clean_text = "".join(char.lower() for char in text if char.isalnum())
    return clean_text == clean_text[::-1]
''')
        
        # Student with TaskA, TaskB format
        zf.writestr("student_003/TaskA.py", '''
# Task A: String operations
def reverse_words(sentence):
    words = sentence.split()
    return " ".join(reversed(words))
''')
        zf.writestr("student_003/TaskB.py", '''
# Task B: Number operations
def sum_digits(number):
    return sum(int(digit) for digit in str(abs(number)))
''')
    
    return zip_path


def create_test_dataset_3_mixed_formats():
    """Dataset 3: Mixed file formats (code + documents + images)"""
    print("Creating Dataset 3: Mixed File Formats...")
    
    zip_path = TEST_DATA_DIR / "dataset_03_mixed_formats.zip"
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Student with Python + README
        zf.writestr("student_001_code_readme/solution.py", '''
def calculate_area(radius):
    import math
    return math.pi * radius ** 2

print(calculate_area(5))
''')
        zf.writestr("student_001_code_readme/README.txt", "Circle Area Calculator\n\nThis program calculates the area of a circle given its radius.")
        
        # Student with Java
        zf.writestr("student_002_java/Main.java", '''
public class Main {
    public static void main(String[] args) {
        Circle circle = new Circle(5.0);
        System.out.println("Area: " + circle.getArea());
    }
}

class Circle {
    private double radius;
    
    public Circle(double radius) {
        this.radius = radius;
    }
    
    public double getArea() {
        return Math.PI * radius * radius;
    }
}
''')
        
        # Student with C++
        zf.writestr("student_003_cpp/main.cpp", '''
#include <iostream>
#include <cmath>

class Circle {
private:
    double radius;
public:
    Circle(double r) : radius(r) {}
    double getArea() {
        return M_PI * radius * radius;
    }
};

int main() {
    Circle c(5.0);
    std::cout << "Area: " << c.getArea() << std::endl;
    return 0;
}
''')
        
        # Student with multiple file types
        zf.writestr("student_004_mixed/solution.py", '''
# Main solution
def solve():
    return "Solution implemented"
''')
        zf.writestr("student_004_mixed/data.csv", "name,age,score\nAlice,20,95\nBob,21,87")
        zf.writestr("student_004_mixed/notes.md", "# Notes\n\n- Implemented core functionality\n- Tested with sample data")
        zf.writestr("student_004_mixed/config.json", '{"timeout": 30, "max_iterations": 1000}')
    
    return zip_path


def create_test_dataset_4_nested_structure():
    """Dataset 4: Deeply nested folder structures"""
    print("Creating Dataset 4: Nested Folder Structures...")
    
    zip_path = TEST_DATA_DIR / "dataset_04_nested_structure.zip"
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Deep nesting
        zf.writestr("student_001/src/main/python/app.py", '''
from utils import helper

def main():
    return helper.process()

if __name__ == "__main__":
    main()
''')
        zf.writestr("student_001/src/main/python/utils/helper.py", '''
def process():
    return "Processing complete"
''')
        zf.writestr("student_001/src/test/python/test_app.py", '''
import unittest

class TestApp(unittest.TestCase):
    def test_process(self):
        self.assertTrue(True)
''')
        zf.writestr("student_001/docs/readme.md", "# Project Documentation")
        zf.writestr("student_001/data/input/sample.csv", "id,value\n1,100\n2,200")
        
        # Mixed nesting
        zf.writestr("student_002/code/main.py", '''
print("Hello World")
''')
        zf.writestr("student_002/code/lib/__init__.py", "")
        zf.writestr("student_002/code/lib/utils.py", '''
def utility():
    pass
''')
        zf.writestr("student_002/assets/data.json", '{"key": "value"}')
    
    return zip_path


def create_test_dataset_5_edge_cases():
    """Dataset 5: Edge cases and error conditions"""
    print("Creating Dataset 5: Edge Cases...")
    
    zip_path = TEST_DATA_DIR / "dataset_05_edge_cases.zip"
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Empty file
        zf.writestr("student_001_empty/empty.py", "")
        
        # Very long file
        long_code = "# " + "x" * 10000 + "\n\nprint('done')\n"
        zf.writestr("student_002_long/long_file.py", long_code)
        
        # Special characters in filename
        zf.writestr("student_003_special/solution_final_v2.0.py", '''
print("Solution with version in filename")
''')
        
        # Unicode content
        zf.writestr("student_004_unicode/unicode.py", '''
# UTF-8 content: αβγδ ε
# 中文测试
# مرحبا
print("Unicode test: café, naïve, résumé")
''')
        
        # Binary file (should be skipped gracefully)
        zf.writestr("student_005_binary/compiled.pyc", b'\x00\x01\x02\x03\x04\x05')
        
        # File with no extension
        zf.writestr("student_006_no_ext/README", "This is a readme file without extension")
        
        # Multiple dots in filename
        zf.writestr("student_007_dots/solution.test.final.py", '''
# File with multiple dots
print("Multiple dots in filename")
''')
        
        # Hidden files (should be ignored)
        zf.writestr("student_008_hidden/.hidden_file.py", "# Hidden file")
        zf.writestr("student_008_hidden/__MACOSX/ignore_this", "macOS file")
        zf.writestr("student_008_visible/visible.py", "# Visible file")
    
    return zip_path


def create_test_dataset_6_large_class():
    """Dataset 6: Large class simulation (50+ students)"""
    print("Creating Dataset 6: Large Class (50 students)...")
    
    zip_path = TEST_DATA_DIR / "dataset_06_large_class.zip"
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i in range(1, 51):
            student_id = f"student_{i:03d}"
            # Vary the quality
            if i <= 10:
                code = f'''# Excellent solution by {student_id}
def factorial(n):
    if n < 0:
        raise ValueError("Negative input")
    if n in (0, 1):
        return 1
    return n * factorial(n - 1)
'''
            elif i <= 25:
                code = f'''# Good solution by {student_id}
def factorial(n):
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result
'''
            elif i <= 40:
                code = f'''# Average solution by {student_id}
def fact(n):
    # Works but no error handling
    r = 1
    for i in range(n):
        r *= (i + 1)
    return r
'''
            else:
                code = f'''# Incomplete by {student_id}
# TODO: implement factorial
def factorial(n):
    pass
'''
            zf.writestr(f"{student_id}/solution.py", code)
    
    return zip_path


def create_test_dataset_7_different_subjects():
    """Dataset 7: Different assignment types and subjects"""
    print("Creating Dataset 7: Different Assignment Types...")
    
    zip_path = TEST_DATA_DIR / "dataset_07_assignment_types.zip"
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Data Science assignment
        zf.writestr("student_001_datascience/analysis.py", '''
import pandas as pd
import matplotlib.pyplot as plt

# Data analysis
data = {'A': [1, 2, 3], 'B': [4, 5, 6]}
df = pd.DataFrame(data)
print(df.describe())
''')
        zf.writestr("student_001_datascience/report.md", "# Data Analysis Report\n\n## Summary\nThe data shows interesting patterns.")
        
        # Web Development assignment
        zf.writestr("student_002_webdev/index.html", '''
<!DOCTYPE html>
<html>
<head><title>My Page</title></head>
<body>
    <h1>Hello World</h1>
    <script src="script.js"></script>
</body>
</html>
''')
        zf.writestr("student_002_webdev/script.js", '''
document.addEventListener('DOMContentLoaded', function() {
    console.log('Page loaded');
});
''')
        zf.writestr("student_002_webdev/style.css", '''
body {
    font-family: Arial, sans-serif;
    margin: 0;
    padding: 20px;
}
''')
        
        # Algorithms assignment
        zf.writestr("student_003_algorithms/sort.py", '''
def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + middle + quicksort(right)

print(quicksort([3, 6, 8, 10, 1, 2, 1]))
''')
        
        # Database assignment
        zf.writestr("student_004_database/queries.sql", '''
-- Create table
CREATE TABLE students (
    id INT PRIMARY KEY,
    name VARCHAR(100),
    grade FLOAT
);

-- Insert data
INSERT INTO students VALUES (1, 'Alice', 95.5);

-- Query
SELECT * FROM students WHERE grade > 90;
''')
    
    return zip_path


def create_test_dataset_8_partial_submissions():
    """Dataset 8: Partial/incomplete submissions"""
    print("Creating Dataset 8: Partial Submissions...")
    
    zip_path = TEST_DATA_DIR / "dataset_08_partial.zip"
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Missing required file
        zf.writestr("student_001_partial/Q1_answer.py", "# Only Q1 submitted")
        # Missing Q2 and Q3
        
        # Wrong file format (submitted .txt instead of .py)
        zf.writestr("student_002_wrong_format/solution.txt", '''
This is my solution in text format.

def solution():
    return 42
''')
        
        # Corrupted file
        zf.writestr("student_003_corrupted/solution.py", b'\x89PNG\r\n\x1a\n')
        
        # Very large file (stress test)
        large_content = "print('line')\n" * 1000
        zf.writestr("student_004_large/large_file.py", large_content)
        
        # Empty submission (just a folder)
        zf.writestr("student_005_empty/.keep", "")
    
    return zip_path


def create_all_datasets():
    """Create all test datasets"""
    print("=" * 60)
    print("GENERATING COMPREHENSIVE TEST DATASETS")
    print("=" * 60)
    print()
    
    datasets = []
    
    datasets.append(create_test_dataset_1_basic_python())
    datasets.append(create_test_dataset_2_multiple_questions())
    datasets.append(create_test_dataset_3_mixed_formats())
    datasets.append(create_test_dataset_4_nested_structure())
    datasets.append(create_test_dataset_5_edge_cases())
    datasets.append(create_test_dataset_6_large_class())
    datasets.append(create_test_dataset_7_different_subjects())
    datasets.append(create_test_dataset_8_partial_submissions())
    
    print()
    print("=" * 60)
    print("ALL DATASETS CREATED SUCCESSFULLY!")
    print("=" * 60)
    print()
    print("Created datasets:")
    for i, path in enumerate(datasets, 1):
        size = path.stat().st_size / 1024  # KB
        print(f"  {i}. {path.name} ({size:.1f} KB)")
    
    print()
    print("Dataset descriptions:")
    print("  1. Basic Python - Different quality levels")
    print("  2. Multiple Questions - Various naming patterns")
    print("  3. Mixed Formats - Code, documents, data files")
    print("  4. Nested Structure - Deep folder hierarchies")
    print("  5. Edge Cases - Empty, unicode, special chars")
    print("  6. Large Class - 50 students")
    print("  7. Assignment Types - Different subjects")
    print("  8. Partial Submissions - Missing/corrupted files")
    print()
    print(f"All datasets saved to: {TEST_DATA_DIR.absolute()}")
    
    return datasets


if __name__ == "__main__":
    create_all_datasets()
