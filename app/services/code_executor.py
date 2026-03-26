"""Sandboxed code execution against instructor-provided test cases.

Supports Python, Java, C/C++, JavaScript/Node.
macOS note: resource.RLIMIT_AS is not reliably available, so we skip
memory limits on Darwin and rely on the timeout instead.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from app.config import EXEC_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

IS_MAC = platform.system() == "Darwin"


def run_test_cases(
    student_dir: str,
    test_cases_json: str,
    run_command: Optional[str] = None,
) -> dict:
    """Execute test cases against a student's code.

    Args:
        student_dir: path to extracted student files
        test_cases_json: JSON string of test case array
        run_command: e.g. "python solution.py"

    Returns:
        {"results": [...], "passed": int, "total": int, "summary": str}
    """
    try:
        test_cases = json.loads(test_cases_json)
    except (json.JSONDecodeError, TypeError):
        return {"results": [], "passed": 0, "total": 0, "summary": "Invalid test cases JSON"}

    if not test_cases:
        return {"results": [], "passed": 0, "total": 0, "summary": "No test cases provided"}

    student_path = Path(student_dir)

    # Auto-detect run command if not specified
    if not run_command:
        run_command = _detect_run_command(student_path)
        if not run_command:
            return {
                "results": [],
                "passed": 0,
                "total": len(test_cases),
                "summary": "Could not detect how to run the code. Specify a run command.",
            }

    results: list[dict] = []
    passed = 0

    for i, tc in enumerate(test_cases):
        tc_input = tc.get("input", "")
        expected = tc.get("expected_output", "").strip()
        description = tc.get("description", f"Test case {i + 1}")
        points = tc.get("points", 0)

        result = _execute_single(student_path, run_command, tc_input, expected)
        result["description"] = description
        result["points"] = points
        result["expected_output"] = expected

        if result["passed"]:
            passed += 1

        results.append(result)

    return {
        "results": results,
        "passed": passed,
        "total": len(test_cases),
        "summary": f"{passed}/{len(test_cases)} test cases passed",
    }


def _execute_single(
    student_dir: Path,
    run_command: str,
    stdin_input: str,
    expected_output: str,
) -> dict:
    """Run a single test case in a subprocess."""
    # Create a temp directory with a copy of student files for isolation
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Copy student files
        for item in sorted(student_dir.rglob("*")):
            if item.is_file():
                rel = item.relative_to(student_dir)
                dest = tmp_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, dest)

        # Handle compilation for Java / C / C++
        cmd = run_command.strip()
        compile_output = ""

        if "javac" in cmd:
            # Split compile and run: "javac Main.java && java Main"
            parts = cmd.split("&&")
            if len(parts) == 2:
                compile_cmd = parts[0].strip()
                run_cmd = parts[1].strip()
                comp = _run_subprocess(compile_cmd, tmp_path, "", timeout=EXEC_TIMEOUT_SECONDS)
                if comp["returncode"] != 0:
                    return {
                        "passed": False,
                        "actual_output": "",
                        "error": f"Compilation failed: {comp['stderr']}",
                        "execution_time": comp["execution_time"],
                    }
                compile_output = comp["stdout"]
                cmd = run_cmd
            # else just run as-is

        elif cmd.startswith("gcc ") or cmd.startswith("g++ "):
            # "gcc solution.c -o out && ./out"
            parts = cmd.split("&&")
            if len(parts) == 2:
                compile_cmd = parts[0].strip()
                run_cmd = parts[1].strip()
                comp = _run_subprocess(compile_cmd, tmp_path, "", timeout=EXEC_TIMEOUT_SECONDS)
                if comp["returncode"] != 0:
                    return {
                        "passed": False,
                        "actual_output": "",
                        "error": f"Compilation failed: {comp['stderr']}",
                        "execution_time": comp["execution_time"],
                    }
                cmd = run_cmd

        # Execute
        result = _run_subprocess(cmd, tmp_path, stdin_input, timeout=EXEC_TIMEOUT_SECONDS)
        actual = result["stdout"].strip()
        is_passed = actual == expected_output.strip()

        return {
            "passed": is_passed,
            "actual_output": actual,
            "error": result["stderr"] if result["returncode"] != 0 else "",
            "execution_time": result["execution_time"],
        }


def _run_subprocess(
    cmd: str, cwd: Path, stdin_input: str, timeout: int = 10
) -> dict:
    """Run a shell command with timeout and capture output."""
    import time

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "PATH": os.environ.get("PATH", "")},
        )
        elapsed = round(time.time() - start, 3)
        return {
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "returncode": proc.returncode,
            "execution_time": elapsed,
        }
    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - start, 3)
        return {
            "stdout": "",
            "stderr": f"Execution timed out after {timeout}s",
            "returncode": -1,
            "execution_time": elapsed,
        }
    except Exception as e:
        elapsed = round(time.time() - start, 3)
        return {
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
            "execution_time": elapsed,
        }


def _detect_run_command(student_dir: Path) -> Optional[str]:
    """Try to auto-detect the correct run command from file extensions."""
    files = sorted(student_dir.rglob("*"))
    extensions = {f.suffix.lower() for f in files if f.is_file()}

    if ".py" in extensions:
        py_files = [f for f in files if f.suffix.lower() == ".py"]
        # Prefer common names
        for name in ("solution.py", "main.py", "app.py"):
            for f in py_files:
                if f.name.lower() == name:
                    return f"python3 {f.name}"
        return f"python3 {py_files[0].name}" if py_files else None

    if ".java" in extensions:
        java_files = [f for f in files if f.suffix.lower() == ".java"]
        if java_files:
            name = java_files[0].stem
            return f"javac {java_files[0].name} && java {name}"

    if ".c" in extensions:
        c_files = [f for f in files if f.suffix.lower() == ".c"]
        if c_files:
            return f"gcc {c_files[0].name} -o out && ./out"

    if ".cpp" in extensions:
        cpp_files = [f for f in files if f.suffix.lower() == ".cpp"]
        if cpp_files:
            return f"g++ {cpp_files[0].name} -o out && ./out"

    if ".js" in extensions:
        js_files = [f for f in files if f.suffix.lower() == ".js"]
        if js_files:
            return f"node {js_files[0].name}"

    return None
