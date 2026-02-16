# AI-Powered Student Submission Grading System

A **state-of-the-art**, production-ready local web application for university CS instructors to automatically grade student submissions using AI (Kimi K2.5 via NVIDIA NIM API). Upload a master ZIP of student submissions, and the system extracts, analyzes, optionally executes code against test cases, and produces detailed, consistent grades with full justification.

## Key Features

- **Complete Error Handling**: Every operation is wrapped in try/except blocks. One student's broken submission never crashes the entire batch.
- **Real-time Progress**: Server-Sent Events (SSE) provide live grading updates with progress bars, ETA calculations, and per-student status.
- **Multimodal AI Grading**: Supports text, code, PDFs (text extraction + vision for scanned pages), images, Word documents, and Jupyter notebooks.
- **Test Case Execution**: Sandbox code execution for Python, Java, C/C++, and JavaScript with configurable timeouts and resource limits.
- **Human-like Grading**: AI provides detailed rubric breakdowns, question mapping, strengths/weaknesses, critical errors, and improvement suggestions.
- **Instructor Override**: Full control to override scores, add comments, and mark submissions as reviewed.
- **Comprehensive Export**: Export results as CSV or JSON with full grading data.
- **Beautiful Dashboard**: Dark/light mode, grade distribution charts, filtering, and search functionality.

## Prerequisites

- **Python 3.11+** (verify: `python3 --version`)
- **pip** (usually bundled with Python)
- **NVIDIA API Key** for Kimi K2.5 access (get from [NVIDIA NIM](https://build.nvidia.com/explore/discover))

## Quick Setup

```bash
# 1. Navigate to project directory
cd /path/to/Grader

# 2. (Optional) Create a virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure API key
#    Copy .env.example to .env and set your NVIDIA API key:
cp .env.example .env
#    Edit .env and set: NVIDIA_API_KEY=your_api_key_here

# 5. Run the application
python run.py
```

Open **http://localhost:8000** in your browser.

## Usage Walkthrough

### 1. Create a Grading Session

Click **"+ New Session"** from the home page and fill in:

- **Assignment Title**: e.g., "Lab 4 - Linked List Implementation"
- **Description**: Detailed instructions about the assignment
- **Grading Rubric**: Point breakdown, e.g.:
  ```
  Correctness: 40 points
  Code Quality & Style: 20 points
  Edge Cases & Error Handling: 15 points
  Documentation/Comments: 15 points
  Output/Report Quality: 10 points
  Total: 100
  ```
- **Max Score**: Default is 100
- **Test Cases** (optional): JSON array of test inputs/outputs
- **Run Command** (optional): e.g., `python3 solution.py`

### 2. Upload Student Submissions

On the session page:

1. Click **"Upload Student Submissions"**
2. Drag & drop or select a **master ZIP** file
3. The system will:
   - Extract all student ZIPs
   - Parse all file types
   - Detect question mappings from filenames
   - Verify file integrity

### 3. Start AI Grading

Click **"Start AI Grading"** to begin:

- Real-time progress bar with ETA
- Live status updates for each student
- Automatic rate limiting (40 req/min to NVIDIA API)
- Failed students don't block others
- Click any student card to see live detailed feedback

### 4. Review Results

The results dashboard shows:

- **Statistics**: Average, median, std dev, min, max
- **Grade Distribution**: Visual histogram (Chart.js)
- **Student Cards**: Expandable with full details:
  - Overall feedback
  - Rubric breakdown with progress bars
  - Question-by-question analysis
  - Strengths and areas for improvement
  - Critical errors
  - Submitted files

### 5. Override Grades

For any student:

1. Expand their card
2. Edit the score (0-max_score)
3. Add optional comments
4. Check "Mark as Reviewed"
5. Click **"Save Override"**

Overridden grades are highlighted and stored separately from AI grades.

### 6. Export Results

- **CSV**: One row per student with all scores, grades, and feedback
- **JSON**: Full structured data including all AI analysis

Click **"CSV"** or **"JSON"** buttons, or visit:
- `/session/{id}/export/csv`
- `/session/{id}/export/json`

## Master ZIP Structure

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

**Rules:**
- Each student's submission is a **ZIP file inside the master ZIP**
- The **ZIP filename** (without `.zip`) is the student identifier
- OS artifacts (`__MACOSX/`, `.DS_Store`, `__pycache__/`, `.git/`) are ignored
- Folder submissions (instead of ZIP) are handled gracefully

## Supported File Types

| Type | Extensions | Processing |
|------|-----------|-----------|
| **Code** | `.py`, `.java`, `.cpp`, `.c`, `.js`, `.ts`, `.cs`, `.go`, `.rb`, `.php`, `.swift`, `.kt`, `.scala`, `.rs`, `.sh`, `.sql`, `.html`, `.css`, `.r`, `.m` | Read as UTF-8, sent to AI with syntax highlighting |
| **PDF** | `.pdf` | Text extraction via PyMuPDF; scanned PDFs converted to images for vision analysis |
| **Word** | `.docx` | Text extraction via python-docx (paragraphs + tables) |
| **Images** | `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.webp` | Base64-encoded, sent as vision input |
| **Notebooks** | `.ipynb` | Converted to structured text (code + markdown cells) |
| **Other** | `.*` | Logged as skipped, never crashes |

## Test Case Format

Test cases are defined as a JSON array:

```json
[
  {"input": "5\n3\n", "expected_output": "8", "description": "Basic addition", "points": 10},
  {"input": "0\n0\n", "expected_output": "0", "description": "Zero case", "points": 5},
  {"input": "-5\n3\n", "expected_output": "-2", "description": "Negative number", "points": 5}
]
```

**Fields:**
- `input`: String sent to stdin (use `\n` for newlines)
- `expected_output`: Expected stdout (whitespace is trimmed for comparison)
- `description`: Human-readable test description
- `points`: Points awarded for passing this test

## Grading Output Format

The AI provides structured JSON output:

```json
{
  "total_score": 85.5,
  "max_score": 100,
  "letter_grade": "B",
  "percentage": 85.5,
  "rubric_breakdown": [
    {
      "criterion": "Correctness",
      "score": 35,
      "max": 40,
      "justification": "Good logic but missing edge case handling"
    }
  ],
  "question_mapping": [
    {
      "question_number": 1,
      "question_text": "Implement factorial",
      "correctness": "correct",
      "score": 45,
      "max_points": 50,
      "feedback": "Correct implementation with good error handling"
    }
  ],
  "overall_feedback": "Comprehensive assessment of the submission...",
  "strengths": ["Clear variable naming", "Good documentation"],
  "weaknesses": ["Missing input validation", "Could optimize loop"],
  "critical_errors": [],
  "suggestions_for_improvement": "Consider adding type hints...",
  "confidence": "high"
}
```

## Troubleshooting

| Issue | Solution |
|-------|---------|
| **Rate limit errors** | The system auto-throttles to 40 req/min. If you still hit limits, wait a minute and retry. |
| **PDF parsing fails** | Ensure PyMuPDF (fitz) is installed: `pip install PyMuPDF` |
| **File encoding errors** | Non-UTF-8 files are read with `errors="replace"` — they won't crash but may show garbled text |
| **Code execution timeout** | Default timeout is 10 seconds. Infinite loops are killed automatically. |
| **"Grading Error" for a student** | Expand the student card to see error details. Usually an AI API issue — click "Start Grading" again to retry failed students. |
| **Port 8000 already in use** | Edit `run.py` and change the port number |
| **Database locked** | SQLite doesn't support concurrent writes. Wait for grading to complete before exporting. |

## Testing

Run the comprehensive test suite:

```bash
python test_system.py
```

This tests:
- Database connection and models
- File parsing (code, text, images, PDFs)
- ZIP extraction and processing
- Export functions (CSV, JSON)
- Code execution sandbox
- FastAPI routes
- Complete end-to-end workflow

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Home page - list all sessions |
| `/health` | GET | Health check |
| `/session/new` | GET/POST | Create new session |
| `/session/{id}` | GET | Session detail page |
| `/session/{id}/upload` | POST | Upload master ZIP |
| `/session/{id}/grade` | POST | Start AI grading |
| `/session/{id}/grade-stream` | GET | SSE for real-time progress |
| `/session/{id}/status` | GET | Get grading status |
| `/session/{id}/results` | GET | Full results dashboard |
| `/session/{id}/verification` | GET | Extraction verification report |
| `/session/{id}/export/csv` | GET | Export as CSV |
| `/session/{id}/export/json` | GET | Export as JSON |
| `/session/{id}/student/{sid}/result` | GET | Get single student result |
| `/session/{id}/student/{sid}/override` | POST | Override student grade |
| `/session/{id}/delete` | POST | Delete session |

## Tech Stack

- **Backend**: Python 3.11+ / FastAPI
- **Frontend**: Jinja2 + Tailwind CSS (CDN) + Alpine.js + Chart.js
- **Database**: SQLite via SQLAlchemy
- **AI**: Kimi K2.5 via NVIDIA NIM (OpenAI-compatible)
- **File Processing**: python-docx, PyMuPDF, Pillow, nbconvert
- **Code Execution**: subprocess with timeout and resource limits

## Architecture

```
Grader/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, routes
│   ├── config.py             # Settings, env vars
│   ├── database.py           # SQLAlchemy setup
│   ├── models.py             # Database models
│   ├── schemas.py            # Pydantic schemas
│   ├── services/
│   │   ├── zip_processor.py  # Archive extraction
│   │   ├── file_parser.py    # File parsing (PDF, images, etc.)
│   │   ├── ai_grader.py      # AI grading with rate limiting
│   │   ├── code_executor.py  # Sandbox code execution
│   │   └── exporter.py       # CSV/JSON export
│   ├── templates/            # Jinja2 templates
│   └── static/               # Static assets
├── uploads/                  # Temporary upload storage
├── grading.db               # SQLite database
├── test_system.py           # Comprehensive test suite
├── .env                     # API keys (gitignored)
├── requirements.txt
├── README.md
└── run.py                   # Entry point
```

## Performance

- **Rate Limiting**: 40 requests/minute to NVIDIA API (configurable)
- **Concurrency**: Grading is async and non-blocking
- **File Size**: Supports large ZIP files (tested up to 500MB)
- **Students**: No hard limit, tested with 100+ students per session
- **Memory**: Efficient streaming for large files

## Security

- **Local-only**: No authentication required (designed for local use)
- **Sandboxed execution**: Code runs in isolated temp directories
- **Resource limits**: Execution timeout (10s) and memory limits (256MB)
- **No secrets in code**: API keys stored in `.env` (gitignored)

## Contributing

This is a production-ready system. When contributing:

1. Run tests: `python test_system.py`
2. Ensure all error paths are handled
3. Add logging for debugging
4. Follow existing code style
5. Update documentation

## License

MIT License - See LICENSE file for details

## Support

For issues or questions:
- Check the Troubleshooting section
- Review the logs in `server.log`
- Run the test suite to verify setup

---

**Version**: 3.0.0  
**Last Updated**: 2026-02-15  
**Status**: Production Ready ✅
