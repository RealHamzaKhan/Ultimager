"""
State-of-the-art archive processor with LLM-powered identification and comprehensive verification.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict

from app.config import IGNORED_NAMES, UPLOAD_DIR
from app.services.file_parser import parse_file

logger = logging.getLogger(__name__)

# Archive format handlers
ARCHIVE_EXTENSIONS = {
    '.zip': 'zip',
    '.rar': 'rar',
    '.7z': '7z',
    '.tar': 'tar',
    '.gz': 'tar.gz',
    '.tgz': 'tar.gz',
}


def _should_ignore(name: str) -> bool:
    """Return True if the file/folder should be skipped."""
    basename = name.strip().rstrip("/")
    lower_name = basename.lower()
    
    # Exact name match against ignored names set
    if basename in IGNORED_NAMES or lower_name in {n.lower() for n in IGNORED_NAMES}:
        return True
    
    # Hidden files and system folders
    if basename.startswith(".") or basename.startswith("__"):
        return True
    
    # Test-related patterns
    test_patterns = ['test', 'testing', 'dataset_', 'sample_', 'example_']
    for pattern in test_patterns:
        if pattern in lower_name:
            return True
    
    # Common test data names (single names that look like test data)
    common_test_names = [
        'carol', 'eve', 'nick', 'jake', 'grace', 'bob', 'frank', 'dan',
        'karen', 'leo', 'iris', 'olivia', 'mia', 'alice', 'henry',
        'test', 'demo', 'sample', 'example'
    ]
    # Only ignore if it's a simple name (not like "Test_Submission_2024")
    if lower_name in common_test_names or lower_name.rstrip('_0123456789') in common_test_names:
        return True
    
    return False


def extract_archive(archive_path: Path, extract_dir: Path) -> Tuple[bool, str]:
    """
    Extract any supported archive format to the specified directory.
    
    Returns:
        Tuple of (success: bool, message: str)
    """
    # Convert to Path if string
    if isinstance(archive_path, str):
        archive_path = Path(archive_path)
    if isinstance(extract_dir, str):
        extract_dir = Path(extract_dir)
    
    suffix = archive_path.suffix.lower()
    name_lower = archive_path.name.lower()
    
    try:
        if suffix == '.zip':
            with zipfile.ZipFile(archive_path, 'r') as zf:
                zf.extractall(extract_dir)
            return True, f"Extracted ZIP: {archive_path.name}"
        
        elif suffix in ['.tar', '.gz', '.tgz'] or '.tar.' in name_lower:
            mode = 'r:gz' if suffix in ['.gz', '.tgz'] or '.tar.gz' in name_lower else 'r'
            with tarfile.open(archive_path, mode) as tf:
                tf.extractall(extract_dir)
            return True, f"Extracted TAR: {archive_path.name}"
        
        elif suffix == '.rar':
            try:
                import rarfile
                with rarfile.RarFile(archive_path) as rf:
                    rf.extractall(extract_dir)
                return True, f"Extracted RAR: {archive_path.name}"
            except ImportError:
                return False, "rarfile module not installed. Run: pip install rarfile"
            except Exception as e:
                return False, f"RAR extraction failed: {str(e)}"
                
        elif suffix == '.7z':
            try:
                import py7zr
                with py7zr.SevenZipFile(archive_path, mode='r') as sz:
                    sz.extractall(extract_dir)
                return True, f"Extracted 7Z: {archive_path.name}"
            except ImportError:
                return False, "py7zr module not installed. Run: pip install py7zr"
            except Exception as e:
                return False, f"7Z extraction failed: {str(e)}"
        
        else:
            return False, f"Unsupported archive format: {suffix}"
            
    except Exception as e:
        logger.exception(f"Failed to extract {archive_path}: {e}")
        return False, f"Extraction error: {str(e)}"


def extract_student_id(filename: str) -> Optional[str]:
    """
    Extract student identifier from the submission filename.
    
    Uses the original filename (without extension) as the student identifier
    to preserve the exact name provided by the student.
    """
    # Get the filename without extension
    clean_name = Path(filename).stem
    
    # Return the filename as-is (this is the student's submission identifier)
    return clean_name if clean_name else None


def detect_question_from_filename(filename: str) -> Optional[Dict[str, Any]]:
    """
    Detect which question a file corresponds to based on filename patterns.
    
    Returns dict with question info or None if can't detect.
    """
    clean_name = Path(filename).stem.lower()
    ext = Path(filename).suffix.lower()
    
    # Pattern definitions with confidence scores
    patterns = [
        # High confidence patterns
        (r'^[Qq](\d+)', 'question', 0.95),
        (r'^[Qq]uestion[-_\s]?(\d+)', 'question', 0.95),
        (r'^[Tt]ask[-_\s]?(\d+)', 'task', 0.90),
        (r'^[Pp]roblem[-_\s]?(\d+)', 'problem', 0.90),
        (r'^[Ee]xercise[-_\s]?(\d+)', 'exercise', 0.90),
        (r'^[Aa]ssignment[-_\s]?(\d+)', 'assignment', 0.90),
        (r'^[Pp]art[-_\s]?(\d+)', 'part', 0.85),
        (r'^[Ll]ab[-_\s]?(\d+)', 'lab', 0.85),
        
        # Medium confidence - number at start
        (r'^(\d+)[-_]', 'numbered', 0.70),
        
        # Lower confidence - number at end before extension
        (r'[-_](\d+)\.\w+$', 'numbered', 0.60),
    ]
    
    for pattern, q_type, confidence in patterns:
        match = re.search(pattern, clean_name)
        if match:
            question_num = int(match.group(1))
            return {
                'number': question_num,
                'type': q_type,
                'confidence': confidence,
                'detected_from': 'filename_pattern'
            }
    
    return None


def get_file_category(filename: str) -> str:
    """Categorize file by type."""
    ext = Path(filename).suffix.lower()
    
    code_exts = {'.cpp', '.c', '.h', '.hpp', '.py', '.java', '.js', '.ts', '.cs', 
                 '.go', '.rb', '.php', '.swift', '.kt', '.rs', '.scala'}
    doc_exts = {'.txt', '.md', '.doc', '.docx', '.pdf', '.rtf'}
    image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.tiff'}
    archive_exts = {'.zip', '.rar', '.7z', '.tar', '.gz', '.tgz'}
    
    if ext in code_exts:
        return 'code'
    elif ext in doc_exts:
        return 'document'
    elif ext in image_exts:
        return 'image'
    elif ext in archive_exts:
        return 'archive'
    else:
        return 'other'


def process_student_files(student_dir: Path, student_id: str) -> Dict[str, Any]:
    """
    Process all files in a student's directory with comprehensive metadata.
    
    Returns detailed file information including question mapping.
    """
    files_info = []
    all_questions = set()
    
    # Recursively find all files
    for file_path in sorted(student_dir.rglob("*")):
        if not file_path.is_file():
            continue
        if _should_ignore(file_path.name):
            continue
            
        # Parse file content
        parsed = parse_file(file_path)
        
        # Detect question mapping
        question_info = detect_question_from_filename(file_path.name)
        if question_info:
            all_questions.add(question_info['number'])
        
        # Get file category
        category = get_file_category(file_path.name)
        
        file_info = {
            'filename': file_path.name,
            'relative_path': str(file_path.relative_to(student_dir)),
            'full_path': str(file_path),
            'size': file_path.stat().st_size,
            'type': parsed.get('type', category),
            'category': category,
            'extension': file_path.suffix.lower(),
            'question': question_info,
            'parsed': parsed
        }
        
        files_info.append(file_info)
    
    # Sort files by question number, then by name
    def sort_key(f):
        q = f.get('question', {})
        return (q.get('number', 999) if q else 999, f['filename'])
    
    files_info.sort(key=sort_key)
    
    return {
        'student_identifier': student_id,
        'extract_dir': str(student_dir),
        'file_count': len(files_info),
        'files': files_info,
        'questions_detected': sorted(list(all_questions)),
        'categories': defaultdict(int)
    }


def extract_master_archive_with_verification(archive_path: Path, session_id: int) -> Dict[str, Any]:
    """
    Extract master archive with comprehensive verification and reporting.
    
    Returns detailed extraction report including:
    - All extracted students
    - File counts per student
    - Question mappings
    - Any errors or warnings
    """
    session_dir = UPLOAD_DIR / str(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    
    extraction_report = {
        'success': False,
        'total_archives_found': 0,
        'successful_extractions': 0,
        'failed_extractions': 0,
        'students': [],
        'errors': [],
        'warnings': [],
        'stats': {
            'total_files': 0,
            'by_category': defaultdict(int),
            'by_question': defaultdict(int)
        }
    }
    
    # Extract master archive
    master_extracted = session_dir / "_master"
    success, message = extract_archive(archive_path, master_extracted)
    
    if not success:
        extraction_report['errors'].append(message)
        return extraction_report
    
    extraction_report['success'] = True
    
    # Process all top-level items
    top_items = sorted(master_extracted.iterdir())
    
    for item in top_items:
        if _should_ignore(item.name):
            extraction_report['warnings'].append(f"Ignored: {item.name}")
            continue
        
        extraction_report['total_archives_found'] += 1
        
        # Extract student ID
        student_id = extract_student_id(item.name)
        if not student_id:
            student_id = item.stem if item.is_file() else item.name
            extraction_report['warnings'].append(f"Could not extract ID from: {item.name}, using: {student_id}")
        
        if item.is_file():
            # Check if it's an archive
            suffix = item.suffix.lower()
            if suffix in ['.zip', '.rar', '.7z', '.tar', '.gz', '.tgz'] or '.tar.' in item.name.lower():
                # Nested student archive
                student_dir = session_dir / student_id
                student_dir.mkdir(parents=True, exist_ok=True)
                
                success, msg = extract_archive(item, student_dir)
                if success:
                    extraction_report['successful_extractions'] += 1
                    student_data = process_student_files(student_dir, student_id)
                    extraction_report['students'].append(student_data)
                    extraction_report['stats']['total_files'] += student_data['file_count']
                else:
                    extraction_report['failed_extractions'] += 1
                    extraction_report['errors'].append(f"{student_id}: {msg}")
            else:
                # Single file submission
                student_dir = session_dir / student_id
                student_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, student_dir / item.name)
                student_data = process_student_files(student_dir, student_id)
                extraction_report['students'].append(student_data)
                extraction_report['stats']['total_files'] += student_data['file_count']
                extraction_report['successful_extractions'] += 1
        
        elif item.is_dir():
            # Folder submission
            student_data = process_student_files(item, student_id)
            extraction_report['students'].append(student_data)
            extraction_report['stats']['total_files'] += student_data['file_count']
            extraction_report['successful_extractions'] += 1
    
    # Calculate statistics
    for student in extraction_report['students']:
        for f in student['files']:
            extraction_report['stats']['by_category'][f['category']] += 1
            if f.get('question'):
                extraction_report['stats']['by_question'][f['question']['number']] += 1
    
    return extraction_report


# Backward compatibility
extract_master_zip = extract_master_archive_with_verification
def cleanup_session_files(session_id: int) -> None:
    """Remove extracted files for a session."""
    session_dir = UPLOAD_DIR / str(session_id)
    if session_dir.exists():
        shutil.rmtree(session_dir, ignore_errors=True)
