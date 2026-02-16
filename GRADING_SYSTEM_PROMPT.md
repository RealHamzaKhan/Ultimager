# AI-Powered Student Submission Grading System — Build Specification

## What You Are Building

Build a **complete, production-ready, local web application** for a university CS instructor to automatically grade student submissions using AI. The instructor uploads a single master ZIP file, the system extracts each student's submission, analyzes every file with AI, optionally executes code against test cases, and produces detailed, consistent grades with full justification — so the instructor never needs to cross-check.

---

## Tech Stack (Non-Negotiable)

- **Backend:** Python 3.11+ with FastAPI
- **Frontend:** Jinja2 templates with Tailwind CSS (CDN) — served by FastAPI directly, NO separate frontend framework
- **Database:** SQLite via SQLAlchemy (single file `grading.db`)
- **AI Provider:** Kimi K2.5 via NVIDIA NIM API (OpenAI-compatible endpoint)
- **File Processing:** python-docx, PyMuPDF (fitz), Pillow, nbconvert, base64
- **Code Execution:** subprocess with sandboxed execution (timeout + resource limits)
- **No authentication required** — local use only

---

## NVIDIA NIM API Configuration

```python
BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL = "moonshotai/kimi-k2-instruct"
RATE_LIMIT = 40  # requests per minute — MUST be respected

# OpenAI-compatible client setup
from openai import OpenAI
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key="<INSTRUCTOR_PROVIDED_API_KEY>"
)
```

- The API key should be configurable via a `.env` file (`NVIDIA_API_KEY=...`) loaded with `python-dotenv`.
- Implement a **rate limiter** (token bucket or simple sleep-based) that ensures no more than 40 requests/minute across all concurrent grading operations. This is critical — exceeding the rate limit will cause failures.

---

## Input Format

The instructor uploads a **master ZIP file** with this structure:

```
master.zip
├── StudentName_RollNo.zip      (or any identifier as zip filename)
│   ├── solution.py
│   ├── report.pdf
│   └── screenshot.png
├── AnotherStudent_ID.zip
│   ├── Main.java
│   ├── README.md
│   └── output.docx
└── ...
```

**Rules for student identification:**
- Each student's submission is a **ZIP file inside the master ZIP**
- The **ZIP filename** (without `.zip` extension) is the student identifier (e.g., `AhmedKhan_22F-BSE-001`)
- Ignore `__MACOSX/`, `.DS_Store`, `__pycache__/`, `.git/` and other OS/IDE artifacts
- If someone submits a folder instead of a zip inside the master zip, still handle it gracefully

---

## Supported File Types & Processing

| File Type | Extensions | Processing Method |
|---|---|---|
| Code files | `.py`, `.java`, `.cpp`, `.c`, `.js`, `.ts`, `.cs`, `.go`, `.rb`, `.php`, `.swift`, `.kt`, `.scala`, `.rs`, `.sh`, `.sql`, `.html`, `.css`, `.r`, `.m` | Read as UTF-8 text, send full source code to AI |
| PDF | `.pdf` | Extract text using PyMuPDF (fitz). If text extraction yields <50 chars, treat as scanned/handwritten — convert pages to images (PNG) and send to AI as vision input |
| Word docs | `.docx` | Extract text using python-docx (paragraphs + tables) |
| Images | `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp` | Convert to base64, send to AI as vision input for analysis |
| Jupyter Notebooks | `.ipynb` | Convert to Python script using nbconvert, extract both code cells and markdown cells, send as structured text |
| Other files | `.*` | Log as "unsupported file skipped" — don't crash |

---

## Core Features to Implement

### 1. Assignment/Task Configuration (Per Grading Session)

Before grading, the instructor provides:

- **Assignment Title** (text field)
- **Assignment Description** (rich textarea) — what the task was, what was expected
- **Grading Rubric** (textarea) — point breakdown, e.g.:
  ```
  Correctness: 40 points
  Code Quality & Style: 20 points
  Edge Cases & Error Handling: 15 points
  Documentation/Comments: 15 points
  Output/Report Quality: 10 points
  Total: 100
  ```
- **Test Cases (optional)** — for auto-execution:
  - Input/expected output pairs as JSON or a test script file upload
  - Specify the language/command to run (e.g., `python solution.py`, `javac Main.java && java Main`)
- **Reference Solution (optional)** — upload an ideal solution file that AI can compare against for consistency
- **Max Score** (default: 100)

Store these as part of a "GradingSession" in the database.

### 2. AI Grading Engine

This is the heart of the system. Build it carefully.

**Grading prompt strategy for consistency:**

```
You are an expert Computer Science instructor grading student submissions.
You MUST grade consistently — the same quality of work must always receive the same grade regardless of the order you evaluate it.

ASSIGNMENT: {title}
DESCRIPTION: {description}
RUBRIC: {rubric}
MAX SCORE: {max_score}
{if reference_solution: "REFERENCE SOLUTION: {reference_solution}"}
{if test_results: "AUTOMATED TEST RESULTS: {test_results}"}

STUDENT SUBMISSION FILES:
{for each file: filename, type, and content}

INSTRUCTIONS:
1. Evaluate EVERY submitted file against the rubric.
2. For code: check correctness, logic, efficiency, style, comments, error handling.
3. For reports/documents: check completeness, clarity, correctness of explanations.
4. For images/screenshots: verify they show expected output or diagrams.
5. Grade STRICTLY according to the rubric — do not inflate grades.
6. If test case results are provided, weight them heavily for correctness.

Respond in this exact JSON format:
{
  "total_score": <number>,
  "max_score": <number>,
  "letter_grade": "<A+/A/A-/B+/B/B-/C+/C/C-/D+/D/F>",
  "rubric_breakdown": [
    {"criterion": "<rubric item>", "score": <number>, "max": <number>, "justification": "<detailed explanation>"}
  ],
  "file_analysis": [
    {"filename": "<name>", "assessment": "<detailed analysis of this specific file>", "issues_found": ["<issue1>", "<issue2>"]}
  ],
  "overall_feedback": "<comprehensive 4-6 sentence assessment explaining the grade>",
  "strengths": ["<specific strength with evidence>"],
  "weaknesses": ["<specific weakness with evidence>"],
  "critical_errors": ["<any major errors like compilation failures, completely wrong logic>"],
  "suggestions_for_improvement": "<actionable advice for the student>",
  "confidence": "<high/medium/low — how confident are you in this grade>"
}
```

**Important implementation details:**
- Send the rubric and assignment description with EVERY student's grading request so the AI is always calibrated the same way.
- If a reference solution is provided, include it in every request for comparison.
- If the AI returns `"confidence": "low"`, flag that student for manual review in the UI.
- Parse the AI JSON response safely — if parsing fails, retry once. If it fails again, mark as "GRADING ERROR" and move on.

### 3. Automated Code Execution & Testing

When the instructor provides test cases:

- **Sandbox execution:** Run student code in a subprocess with:
  - `timeout` of 10 seconds (configurable)
  - Memory limit via `resource` module (256MB max)
  - No network access (if possible)
  - Temporary directory per student (cleaned up after)
- **Support these runners:**
  - Python: `python3 {file}`
  - Java: `javac {file} && java {classname}`
  - C/C++: `gcc/g++ {file} -o out && ./out`
  - JavaScript: `node {file}`
- **Test case format:** JSON array:
  ```json
  [
    {"input": "5\n3\n", "expected_output": "8", "description": "Basic addition", "points": 10},
    {"input": "0\n0\n", "expected_output": "0", "description": "Zero case", "points": 5}
  ]
  ```
- Feed stdin, capture stdout, compare with expected output (trim whitespace).
- Record: passed/failed, actual output, error messages, execution time.
- Send these results to the AI along with the code so it can factor test outcomes into the grade.

### 4. Instructor Grade Override

After AI grading completes:

- The instructor can click on any student's result and:
  - **Edit the total score** directly
  - **Edit individual rubric criterion scores**
  - **Add/edit comments**
  - **Mark as "Reviewed"** (checkbox)
- Edited grades should be visually distinguished (e.g., badge saying "Manually Adjusted")
- Store both the original AI grade and the overridden grade in the database

### 5. Results Dashboard & Export

**Dashboard page showing:**

- **Summary statistics:** Average score, median, std deviation, min, max, grade distribution histogram
- **Sortable student table:** Student ID, Score, Letter Grade, Confidence, Status (Graded/Error/Reviewed), expand for details
- **Expandable detail view per student:** Full rubric breakdown with per-criterion scores and justifications, file-by-file analysis, test case results (if applicable), strengths/weaknesses, AI's suggestions, override controls
- **Filtering:** Filter by grade range, confidence level, status
- **Search:** Search by student ID

**Export options:**
- **CSV:** Student ID, Score, Letter Grade, Rubric Breakdown, Feedback Summary
- **Detailed JSON:** Full grading data for all students
- **PDF Report (bonus):** Per-student grade report suitable for printing/sharing

---

## Database Schema (SQLAlchemy + SQLite)

```python
class GradingSession(Base):
    id: int (PK, auto)
    title: str
    description: text
    rubric: text
    max_score: int (default 100)
    reference_solution: text (nullable)
    test_cases: text (nullable, JSON string)
    created_at: datetime
    status: str  # "pending", "grading", "completed"
    total_students: int
    graded_count: int

class StudentSubmission(Base):
    id: int (PK, auto)
    session_id: int (FK -> GradingSession.id)
    student_identifier: str  # extracted from zip filename
    files: text  # JSON list of {filename, type, size}
    status: str  # "pending", "grading", "graded", "error"
    
    # AI grading results (JSON)
    ai_result: text (nullable)
    ai_score: float (nullable)
    ai_letter_grade: str (nullable)
    ai_confidence: str (nullable)
    
    # Test execution results (JSON)
    test_results: text (nullable)
    tests_passed: int (nullable)
    tests_total: int (nullable)
    
    # Override fields
    override_score: float (nullable)
    override_comments: text (nullable)
    is_reviewed: bool (default False)
    is_overridden: bool (default False)
    
    # Final computed
    final_score: float (property — returns override_score if overridden, else ai_score)
    
    graded_at: datetime (nullable)
```

---

## Application Routes

```
GET  /                          → Home page: list all grading sessions + "New Session" button
POST /session/new               → Create new grading session (form with title, description, rubric, test cases, reference solution)
GET  /session/{id}              → Session detail: upload ZIP or view results
POST /session/{id}/upload       → Upload master ZIP, extract students, store in DB
POST /session/{id}/grade        → Start AI grading (background task with progress tracking)
GET  /session/{id}/status       → JSON endpoint for polling grading progress
GET  /session/{id}/results      → Full results dashboard
POST /session/{id}/student/{sid}/override → Save instructor override
GET  /session/{id}/export/csv   → Download CSV
GET  /session/{id}/export/json  → Download JSON
```

---

## UI/UX Requirements

- **Clean, professional dashboard** — this is an academic tool, not a toy
- Use **Tailwind CSS via CDN** for styling
- **Dark/light mode toggle** (default: light for academic setting)
- **Progress indicator** during grading: show which student is being graded, how many done, estimated time remaining
- **Real-time updates** during grading: use polling (every 2 seconds to `/status` endpoint) or SSE to update the UI as each student is graded
- **Grade color coding:** A=green, B=blue, C=yellow, D=orange, F=red
- **Expandable cards** for each student — click to see full breakdown without navigating away
- **Toast notifications** for success/error states
- **Responsive design** — should work on tablet too (instructor might use iPad)
- **Grade distribution chart** — use Chart.js via CDN for a histogram

---

## Rate Limiting Implementation (Critical)

```python
import asyncio
import time

class RateLimiter:
    def __init__(self, max_requests: int = 40, per_seconds: int = 60):
        self.max_requests = max_requests
        self.per_seconds = per_seconds
        self.timestamps = []
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        async with self.lock:
            now = time.time()
            # Remove timestamps older than the window
            self.timestamps = [t for t in self.timestamps if now - t < self.per_seconds]
            if len(self.timestamps) >= self.max_requests:
                sleep_time = self.per_seconds - (now - self.timestamps[0]) + 0.1
                await asyncio.sleep(sleep_time)
            self.timestamps.append(time.time())
```

Every AI API call MUST go through this rate limiter. No exceptions.

---

## Project Structure

```
grading-system/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, routes, startup
│   ├── config.py             # Settings, env vars, API config
│   ├── database.py           # SQLAlchemy setup, engine, session
│   ├── models.py             # SQLAlchemy models
│   ├── schemas.py            # Pydantic schemas
│   ├── services/
│   │   ├── __init__.py
│   │   ├── zip_processor.py  # Extract master ZIP, identify students, parse files
│   │   ├── file_parser.py    # Parse each file type (code, PDF, docx, ipynb, images)
│   │   ├── code_executor.py  # Sandbox code execution against test cases
│   │   ├── ai_grader.py      # AI grading logic, prompt construction, rate limiting
│   │   └── exporter.py       # CSV/JSON export
│   ├── templates/
│   │   ├── base.html         # Base template with nav, Tailwind, Chart.js CDN
│   │   ├── index.html        # Home — list sessions
│   │   ├── new_session.html  # Create session form
│   │   ├── session.html      # Session detail — upload + results
│   │   └── results.html      # Full results dashboard
│   └── static/               # Any custom CSS/JS if needed
├── uploads/                   # Temporary upload storage (gitignored)
├── .env                       # NVIDIA_API_KEY=...
├── .env.example               # Template
├── requirements.txt
├── README.md                  # Setup & usage instructions
└── run.py                     # Entry point: uvicorn app.main:app
```

---

## requirements.txt

```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-dotenv>=1.0.0
sqlalchemy>=2.0.0
python-multipart>=0.0.6
jinja2>=3.1.0
openai>=1.0.0
python-docx>=1.0.0
PyMuPDF>=1.23.0
Pillow>=10.0.0
nbconvert>=7.0.0
nbformat>=5.9.0
aiofiles>=23.0.0
```

---

## Critical Implementation Rules

1. **NEVER skip error handling.** Every file parse, every API call, every subprocess must be wrapped in try/except. One student's broken submission must never crash the entire batch.

2. **Grading must be async and non-blocking.** Use FastAPI's background tasks or asyncio. The instructor should see real-time progress in the browser while grading runs.

3. **AI consistency is paramount.** The exact same rubric text and assignment description must be sent with every single student's grading request. Never modify the system prompt between students.

4. **Store EVERYTHING.** Raw AI responses, test execution outputs, timestamps — all stored in the DB. The instructor should be able to audit exactly what the AI saw and said.

5. **Graceful degradation:** If PDF text extraction fails → try image conversion. If code execution fails → still do AI-only grading. If AI API fails → retry once, then mark as error. Never leave a student in "pending" limbo.

6. **The UI must work fully offline after initial page load** (except for the AI API calls). All JS/CSS via CDN with fallbacks.

7. **Make it runnable with a single command:** `pip install -r requirements.txt && python run.py` — that's it. No Docker required. No complex setup.

---

## README.md Content

Include in the README:
- One-paragraph description of what this is
- Prerequisites (Python 3.11+)
- Setup: clone, create `.env` with API key, `pip install -r requirements.txt`, `python run.py`
- Usage walkthrough with screenshots descriptions
- ZIP structure explanation with examples
- Test case format documentation
- Troubleshooting section (common errors like rate limiting, file parse failures)

---

## Build Order (Suggested)

Build and test in this order:
1. Database models + FastAPI skeleton with home page
2. Session creation form + storage
3. ZIP upload + extraction + student detection
4. File parsing (all types)
5. AI grading engine with rate limiter
6. Results dashboard with grade display
7. Code execution sandbox + test cases
8. Instructor override functionality  
9. Export (CSV + JSON)
10. Polish UI, add charts, add search/filter
11. Error handling sweep + edge case testing

---

Now build this system completely. Every file, every route, every template. Make it production-ready, well-commented, and immediately runnable.
