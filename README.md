```markdown
# GradeForge 🎓⚡

> **AI-powered automated grading platform for university CS instructors**  
> Upload student submissions — get back rubric-grounded, evidence-cited, teacher-reviewable grades in minutes.

[![Version](https://img.shields.io/badge/version-5.0.0-blue?style=flat-square)](https://github.com/yourorg/gradeforge/releases)
[![Python](https://img.shields.io/badge/python-3.9%2B-green?style=flat-square&logo=python)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey?style=flat-square)](LICENSE)
[![Tests](https://img.shields.io/github/actions/workflow/status/yourorg/gradeforge/tests.yml?branch=main&label=tests&style=flat-square)](https://github.com/yourorg/gradeforge/actions)
[![Code Style](https://img.shields.io/badge/code%20style-black-000000?style=flat-square)](https://github.com/psf/black)

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Key Features](#-key-features)
- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [Supported LLM Providers](#-supported-llm-providers)
- [Usage Guide](#-usage-guide)
- [API Reference](#-api-reference)
- [Project Structure](#-project-structure)
- [Testing](#-testing)
- [Security & Privacy](#-security--privacy)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [Roadmap](#-roadmap)
- [License](#-license)

---

## 🌟 Overview

**GradeForge** is a production-ready, full-stack grading assistant designed for university computer science instructors. It combines:

- 🔧 **FastAPI backend** with 28+ REST endpoints
- 🎨 **Server-rendered Jinja2 UI** + optional Next.js SPA frontend
- 🤖 **Multi-agent LLM pipeline** for transparent, auditable grading

GradeForge handles diverse submission formats — code files, Jupyter notebooks, PDFs, Word documents, images, and compressed archives — then surfaces checkpoint-level grades with cited evidence directly in your browser.

### Why GradeForge?

| Challenge | GradeForge Solution |
|-----------|-------------------|
| ⏱️ Time-consuming manual grading | Grade 100+ submissions in minutes with parallel processing |
| 🔍 Inconsistent rubric application | Deterministic, evidence-cited scoring per criterion |
| 📦 Complex submission formats | Universal parser for 30+ file types + nested archives |
| 🤔 Black-box AI decisions | Full transparency: every score includes verifiable evidence |
| 🔐 Student privacy concerns | Local deployment option; no data leaves your infrastructure |

> 💡 **Instructor Control First**: Every AI decision is transparent, every score is editable, and uncertain results are automatically flagged for manual review.

---

## ✨ Key Features

### 🎯 Intelligent Grading
- **Checkpoint-based evaluation**: Rubric criteria decomposed into granular checkpoints with partial credit (0/25/50/75/100%)
- **Smart file routing**: ZIP archives intelligently parsed; files routed to relevant criteria (no "all-files-to-all-criteria" noise)
- **AI rubric generation**: Paste an assignment description → receive a structured, mark-allocated rubric
- **Multi-format support**: Python, Java, C++, Jupyter (`.ipynb`), PDFs (text + OCR), Office docs, images, plain text
- **Archive handling**: ZIP, RAR, 7z with ZIP-bomb and path-traversal protection
- **Parallel processing**: Grade up to 8 students concurrently; isolated failures never stall the batch
- **Context-aware splitting**: Large submissions auto-split into overlapping windows for models with limited context

### 🔍 Transparency & Trust
- **Evidence-cited scoring**: Every checkpoint includes an exact quote from the student submission
- **Verification flags**: Amber warnings when AI-cited evidence cannot be programmatically verified
- **Truncation alerts**: Banner notifications when submissions exceed `JUDGE_CONTENT_LIMIT`
- **Fallback detection**: Graceful degradation when file routing fails — skips rather than corrupts grades
- **Explicit "not evaluated" markers**: Rubric criteria receiving no checkpoints are clearly labeled
- **Score capping transparency**: Visual indicators when checkpoint totals exceed criterion maximums

### 👩‍🏫 Instructor Experience
- **Live progress streaming**: SSE-based real-time updates with per-student status and ETA
- **One-click stop**: Halt grading within ~2 seconds via UI or API
- **Session dashboard**: Overview of all students with scores, confidence levels, and review flags
- **Rubric breakdown panel**: Expandable criteria showing checkpoints, evidence quotes, and verification status
- **Inline file viewer**: Syntax-highlighted code, PDFs, and text files viewable without download
- **Manual overrides**: Edit any AI score with justification; overrides tracked separately in exports
- **Flagging system**: Mark submissions for manual review with customizable reasons
- **Bulk regrading**: Re-run AI grading per student or across entire sessions
- **Export flexibility**: Download complete gradebooks as CSV or JSON

### 🚀 Advanced Capabilities
- **ACMAG** *(Anchor-Calibrated Multi-Agent Grading)*: Optional multi-rater calibration with Cohen's kappa tracking. Enable via `ACMAG_ENABLED=1`.
- **Deterministic outputs**: `temperature=0, seed=42` ensures reproducible grades for identical submissions
- **Rate-limit awareness**: Async queuing respects provider RPM limits; automatic retry with backoff
- **Vision pre-analysis**: Extracts and transcribes diagrams, screenshots, and scanned pages before grading
- **Relevance gating**: Off-topic submissions flagged early to conserve API credits

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────┐
│              Browser (Jinja2 + Tailwind UI)           │
│   Sessions · Grading Progress · Results · Exports     │
└─────────────────────┬────────────────────────────────┘
                      │  REST + Server-Sent Events (SSE)
┌─────────────────────▼────────────────────────────────┐
│              FastAPI Backend  (v5.0.0)                │
│  28 endpoints  ·  SQLite persistence  ·  Async I/O    │
└─────────────────────┬────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────┐
│           Multi-Agent Grading Pipeline                │
│                                                       │
│  Orchestrator                                         │
│      │                                                │
│      ├─▶  Domain Judge    (LLM — per-criterion eval)  │
│      ├─▶  Verifier        (deterministic quote check) │
│      ├─▶  Scorer          (deterministic aggregation) │
│      └─▶  Critic          (LLM — holistic QA)         │
└─────────────────────┬────────────────────────────────┘
                      │
          Any OpenAI-compatible LLM API
   (NVIDIA NIM · OpenAI · Groq · Together · Ollama)
```

### Pipeline Stages

| Stage | Type | Responsibility |
|-------|------|---------------|
| **Orchestrator** | Coordinator | Routes files to criteria, dispatches agents, aggregates results, handles errors |
| **Domain Judge** | LLM | Evaluates each criterion with partial credit; returns cited evidence + reasoning |
| **Verifier** | Deterministic | Confirms evidence quotes exist in submission text (fuzzy + exact match) |
| **Scorer** | Deterministic | Applies criterion caps, detects unevaluated criteria, computes final scores |
| **Critic** | LLM | Flags quality issues, suspicious patterns, and recommendations for teacher review |

---

## 🚀 Quick Start

GradeForge runs as **two optional services**:
- **Backend** (required): FastAPI API + legacy Jinja2 UI on `:8000`
- **Frontend** (optional): Modern Next.js SPA on `:3000`

> ✅ **Recommendation**: Start with backend-only for testing; add frontend for production use.

### 1️⃣ Backend Setup (Required)

```bash
# Clone repository
git clone https://github.com/yourorg/gradeforge.git
cd gradeforge

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2️⃣ Configure Environment

```bash
# Copy example configuration
cp .env.example .env

# Edit .env with your settings (minimum required: LLM_API_KEY)
# See Configuration section below for all options
```

### 3️⃣ Frontend Setup (Optional)

```bash
cd frontend

# Install Node.js dependencies
npm install

# Build for production (optional for development)
npm run build
```

### 4️⃣ Launch Services

**Terminal 1 — Backend (API + Legacy UI):**
```bash
# From project root
python run.py
# → API: http://localhost:8000
# → Legacy UI: http://localhost:8000/session/new
```

**Terminal 2 — Frontend (Modern UI, optional):**
```bash
cd frontend
npm run dev
# → Modern UI: http://localhost:3000 ⭐ Recommended
```

### 5️⃣ Verify Installation

```bash
# Health check
curl http://localhost:8000/health
# Expected: {"status":"healthy","version":"5.0.0"}
```

### 🏭 Production Deployment

```bash
# Backend (with Uvicorn workers)
python run.py --host 0.0.0.0 --port 8000 --workers 4

# Frontend (production build)
cd frontend
npm run build && npm start

# Reverse proxy example (nginx)
# Proxy /api/* → backend:8000; serve frontend static files
```

---

## ⚙️ Configuration

All settings are managed via `.env`. Copy `.env.example` to begin. Only `LLM_API_KEY` is required.

```env
# ── Required ──────────────────────────────────────────────────────────────────
LLM_API_KEY=your_api_key_here

# ── LLM Provider Configuration (defaults to NVIDIA NIM) ───────────────────────
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=meta/llama-4-maverick-17b-128e-instruct

# Optional: Use a cheaper/faster model just for rubric generation
# LLM_RUBRIC_MODEL=meta/llama-4-maverick-17b-128e-instruct

# ── Rate Limiting & Concurrency ───────────────────────────────────────────────
# Match your provider's RPM: NVIDIA free=40, OpenAI tier-1=500, Groq free=30
RATE_LIMIT_RPM=40

# Parallel student grading workers (reduce if hitting rate limits)
PARALLEL_GRADING_WORKERS=8

# ── Timeouts & Context Management ─────────────────────────────────────────────
LLM_TIMEOUT=120.0              # Seconds per API call
MODEL_CONTEXT_TOKENS=128000    # Match your model's context window
JUDGE_CONTENT_LIMIT=60000      # Max chars of student content per grading call

# ── Vision & Multimedia ───────────────────────────────────────────────────────
ENABLE_VISION_PREANALYSIS=1          # Transcribe diagrams/screenshots before grading
NVIDIA_MAX_IMAGES_PER_REQUEST=8      # Images sent per judge call

# ── Advanced: ACMAG Calibration ───────────────────────────────────────────────
# Enable multi-rater calibration with inter-rater agreement tracking
ACMAG_ENABLED=0  # Leave disabled unless you need calibrated multi-rater grading
```

> 🔐 **Security Note**: Never commit `.env` to version control. Use environment variable injection in production deployments.

---

## 🤖 Supported LLM Providers

Switch providers by updating three variables in `.env`. All OpenAI-compatible endpoints are supported.

| Provider | `LLM_BASE_URL` | `LLM_MODEL` | Notes |
|----------|---------------|-------------|-------|
| **NVIDIA NIM** *(default)* | `https://integrate.api.nvidia.com/v1` | `meta/llama-4-maverick-17b-128e-instruct` | Free tier at [build.nvidia.com](https://build.nvidia.com) |
| **OpenAI** | `https://api.openai.com/v1` | `gpt-4o` | Requires paid account; excellent reasoning |
| **Groq** | `https://api.groq.com/openai/v1` | `llama3-70b-8192` | Ultra-low latency; free tier available |
| **Together AI** | `https://api.together.xyz/v1` | `meta-llama/Llama-3-70b-chat-hf` | Good balance of cost/performance |
| **Ollama** *(local)* | `http://localhost:11434/v1` | `llama3` | Fully offline; set `LLM_API_KEY=ollama` |

> 💡 **Tip**: Start with NVIDIA NIM's free tier for testing. Switch to Groq for faster grading in production.

---

## 📚 Usage Guide

### Creating a Grading Session

1. Navigate to **+ New Session** in the UI
2. Enter session title and assignment description
3. Choose:  
   - ✍️ **Paste rubric manually**, or  
   - ✨ **Generate Rubric** (AI creates structured rubric from description)
4. Upload ZIP file containing student submission folders
5. Click **Grade** — watch live progress via SSE stream

### Expected Submission Format

Students should be organized in individual folders within the ZIP:

```
submissions.zip
├── 22P-0001_Ali/
│   ├── task1.py
│   ├── task2.ipynb
│   └── report.pdf
├── 22P-0002_Sara/
│   └── assignment.zip        ← Nested archives auto-extracted
└── 22P-0003_Ahmed/
    ├── solution.java
    └── screenshot.png        ← Vision pre-analysis enabled
```

✅ Supports nested ZIP/RAR/7z archives  
✅ Handles mixed file types per student  
✅ Auto-detects and parses 30+ file extensions

### Reviewing Results

Each student card displays:

| Element | Meaning |
|---------|---------|
| 🟢🟡🔴 **Score badge** | Color-coded: ≥80% (green), 50–79% (amber), <50% (red) |
| 🎯 **Confidence level** | High/Medium/Low based on verifier agreement |
| ⚠️ **Review flags** | Amber banners highlight items needing teacher attention |

**Expand any rubric criterion** to see:
- Individual checkpoint scores with evidence quotes
- ✅ Green check = evidence verified in submission text
- ⚠️ Amber triangle = AI judgment only (evidence not programmatically verifiable)

### Managing Sessions

| Action | How-To |
|--------|--------|
| ⏹️ **Stop grading** | Click **Stop** on session page; halts within ~2 seconds |
| 🔁 **Regrade** | Click **Regrade All** or regrade individual students |
| ✏️ **Override score** | Edit score directly + add justification; tracked separately |
| 🚩 **Flag for review** | Mark submission with reason; appears in filtered views |
| 📤 **Export** | Download CSV/JSON gradebook from results toolbar |

---

## 🔌 API Reference

### Core Endpoints

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| `GET` | `/health` | Health check — `{"status":"healthy","version":"5.0.0"}` | None |
| `GET` | `/api/sessions` | List all sessions with summary stats | API Key |
| `GET` | `/api/session/{id}/students` | List students with scores and flags | API Key |
| `POST` | `/api/generate-rubric` | AI-generate rubric from assignment description | API Key |
| `GET` | `/api/rubric-templates` | Return 5 built-in rubric templates | API Key |

### Session Management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/session/new` | New session form (HTML) |
| `POST` | `/session/new` | Create a grading session |
| `POST` | `/session/{id}/upload` | Upload student ZIP archive |
| `POST` | `/session/{id}/grade` | Start grading all students |
| `POST` | `/session/{id}/stop-grading` | Halt grading in progress |
| `POST` | `/session/{id}/regrade-all` | Re-grade all students |
| `POST` | `/session/{id}/retry-failed` | Re-grade only errored submissions |
| `POST` | `/session/{id}/delete` | Delete session and all data |

### Real-Time & Export

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/session/{id}/grade-stream` | SSE stream of live grading progress |
| `GET` | `/session/{id}/status` | Current session status (JSON) |
| `GET` | `/session/{id}/export/csv` | Download gradebook as CSV |
| `GET` | `/session/{id}/export/json` | Download gradebook as JSON |

### Student-Level Operations

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/session/{id}/student/{sid}/regrade` | Re-grade one student |
| `POST` | `/session/{id}/student/{sid}/override` | Manual score override with justification |
| `POST` | `/session/{id}/student/{sid}/flag` | Flag submission for review |
| `POST` | `/session/{id}/student/{sid}/unflag` | Clear review flag |
| `GET` | `/session/{id}/student/{sid}/files` | List student's files with metadata |
| `GET` | `/session/{id}/student/{sid}/file/{path}` | Serve a student file inline |
| `GET` | `/session/{id}/student/{sid}/ingestion-report` | File parsing transparency report |

> 🔐 All `/api/*` endpoints require authentication via `X-API-Key` header. HTML endpoints use session cookies.

---

## 📁 Project Structure

```
gradeforge/
├── app/
│   ├── main.py                      # FastAPI app + REST/HTML endpoints
│   ├── config.py                    # Environment-configurable settings
│   ├── models.py                    # SQLAlchemy ORM models
│   ├── schemas.py                   # Pydantic request/response schemas
│   ├── database.py                  # SQLite session management
│   ├── templates/                   # Jinja2 server-rendered pages
│   │   ├── base.html
│   │   ├── index.html               # Session list (home)
│   │   ├── new_session.html         # Create session + rubric generator
│   │   ├── session.html             # Grading dashboard + live progress
│   │   └── results.html             # Results analytics + student cards
│   ├── static/                      # CSS, JS, icons, Tailwind build
│   └── services/
│       ├── agents/
│       │   ├── base.py              # CheckpointResult, GradingResult dataclasses
│       │   ├── orchestrator.py      # Main pipeline coordinator
│       │   ├── domain_judge.py      # LLM checkpoint evaluator (partial credit)
│       │   ├── verifier.py          # Deterministic evidence verifier
│       │   ├── scorer.py            # Score aggregation + capping logic
│       │   └── critic.py            # Quality flags + review recommendations
│       ├── ai_grader_fixed.py       # Core grading engine + rubric generation
│       ├── checkpoint_grader.py     # File routing + checkpoint generation
│       ├── parallel_grader.py       # Concurrent student grading + stop signal
│       ├── file_parser_enhanced.py  # Universal file ingestion pipeline
│       ├── zip_processor.py         # Archive extraction + security validation
│       ├── exporter.py              # CSV/JSON export utilities
│       └── code_executor.py         # Sandboxed test execution (future)
├── frontend/                        # Optional Next.js SPA (modern UI)
│   ├── pages/
│   ├── components/
│   ├── lib/
│   └── next.config.ts
├── tests/
│   ├── unit/                        # 12 unit test files (pytest)
│   ├── integration/                 # 6 integration test files
│   ├── e2e/                         # End-to-end Playwright tests
│   └── conftest.py                  # Test fixtures
├── test_datasets/                   # Sample submissions for local testing
├── requirements.txt
├── .env.example
├── pytest.ini
├── run.py                           # Uvicorn entry point
└── README.md
```

---

## 📊 Result Format (JSON Export)

Key fields per student in exported gradebook:

```jsonc
{
  "student_identifier": "22P-0001",
  "status": "graded",
  "ai_score": 8.5,
  "ai_letter_grade": "A",
  "ai_confidence": "high",
  "final_score": 8.5,
  "is_overridden": false,
  "override_justification": null,
  "ai_result": {
    "total_score": 8.5,
    "max_score": 10.0,
    "percentage": 85.0,
    "letter_grade": "A",
    "overall_feedback": "Strong implementation with minor edge-case gaps...",
    "rubric_breakdown": [
      {
        "criterion": "T1 - Transcript Generator",
        "score": 2.0,
        "max": 2.0,
        "checkpoints": [
          {
            "description": "Correctly converts marks to letter grades",
            "points_awarded": 1.0,
            "points_max": 1.0,
            "score_percent": 100,
            "pass": true,
            "verified": true,
            "evidence_quote": "def get_grade(marks): ...",
            "source_file": "task1.py",
            "source_line_range": [12, 18],
            "reasoning": "The function correctly maps numerical marks to letter grades per specification...",
            "confidence": "high",
            "model_used": "meta/llama-4-maverick-17b-128e-instruct",
            "judge_truncated": false
          }
        ]
      }
    ]
  },
  "flags": [],
  "ingestion_report": {
    "files_processed": 3,
    "files_failed": 0,
    "warnings": []
  }
}
```

---

## 📄 Supported File Types

| Category | Extensions | Notes |
|----------|-----------|-------|
| **Source Code** | `.py` `.java` `.cpp` `.c` `.js` `.ts` `.cs` `.go` `.rb` `.php` `.swift` `.kt` `.scala` `.rs` `.sh` `.sql` `.html` `.css` `.r` `.m` | Syntax highlighting in viewer |
| **Notebooks** | `.ipynb` | Code + markdown + outputs parsed |
| **Documents** | `.pdf` `.docx` `.pptx` `.xlsx` `.xls` `.txt` `.md` | PDFs support text extraction + OCR vision fallback |
| **Images** | `.png` `.jpg` `.jpeg` `.gif` `.bmp` `.webp` | Vision pre-analysis transcribes diagrams/screenshots |
| **Archives** | `.zip` `.rar` `.7z` | Nested archives auto-extracted; security validation enabled |

> 🔒 All uploads undergo security scanning: ZIP-bomb detection, path traversal prevention, and file-type validation.

---

## 🧪 Testing

```bash
# Activate virtual environment
source venv/bin/activate

# Run unit tests
pytest tests/unit/ -v

# Run integration tests (requires backend running)
pytest tests/integration/ -v

# Run all tests with coverage report
pytest tests/ --cov=app --cov-report=term-missing --cov-report=html

# Run end-to-end tests (requires both backend + frontend)
pytest tests/e2e/ -v
```

### Test Coverage Goals
- ✅ Unit tests: 90%+ coverage on core services
- ✅ Integration tests: All API endpoints validated
- ✅ E2E tests: Critical user journeys (session creation → grading → export)

---

## 🔒 Security & Privacy

### Data Handling
- 🗄️ **Local-first architecture**: All data stored in local SQLite; no cloud sync unless explicitly configured
- 🔐 **No student data leaves your server**: LLM calls send only necessary content; no persistent storage on provider side
- 🧹 **Automatic cleanup**: Sessions can be purged with one click; exports are instructor-controlled

### Submission Security
- 🛡️ **Archive validation**: ZIP-bomb detection, path traversal prevention, max extraction limits
- 📁 **File-type enforcement**: Only whitelisted extensions processed; others logged and skipped
- 🧪 **Sandboxed execution** *(future)*: Code execution isolated via Docker/Firecracker (planned)

### Access Control
- 🔑 **API authentication**: All `/api/*` endpoints require `X-API-Key` header
- 🍪 **Session management**: HTML endpoints use secure, HttpOnly cookies
- 🌐 **CORS configuration**: Restrict frontend origins in production via `ALLOWED_ORIGINS`

> ⚠️ **Production Recommendation**: Deploy behind reverse proxy (nginx/Caddy) with TLS termination and rate limiting.

---

## 🛠️ Troubleshooting

| Issue | Solution |
|-------|----------|
| **Server won't start** | Ensure virtual environment is activated: `source venv/bin/activate`; verify dependencies: `pip install -r requirements.txt` |
| **Rate limit errors (429)** | Lower `PARALLEL_GRADING_WORKERS` or `RATE_LIMIT_RPM` in `.env` to match your provider's tier |
| **Students scoring 0 with no error** | Check `server.log` for `RELEVANCE` warnings — submission may be off-topic. Verify rubric matches assignment expectations |
| **Large submissions truncated** | Increase `JUDGE_CONTENT_LIMIT` in `.env`. Default: 60,000 chars. Max safe value depends on model context window |
| **Grading stalls / timeouts** | Check logs for timeout errors. Increase `LLM_TIMEOUT` or switch to lower-latency provider (Groq recommended) |
| **Vision analysis failing** | Ensure `ENABLE_VISION_PREANALYSIS=1` and provider supports multimodal endpoints |
| **Frontend API calls failing** | Verify proxy configuration in `next.config.ts`; ensure backend is running on expected port |

### Debug Mode
```bash
# Enable verbose logging
export LOG_LEVEL=DEBUG
python run.py

# View real-time logs
tail -f server.log
```

---

## 🤝 Contributing

We welcome contributions! Please follow these steps:

1. **Fork** the repository and create your feature branch:
   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **Make changes** with accompanying tests:
   - Add unit tests for new logic
   - Update integration tests for API changes
   - Document new configuration options

3. **Validate**:
   ```bash
   # Run tests
   pytest tests/ -v

   # Check code style
   black app/ tests/
   flake8 app/

   # Verify type hints
   mypy app/
   ```

4. **Submit a pull request** with:
   - Clear description of changes
   - Reference to related issue (if applicable)
   - Screenshots for UI changes

### Development Guidelines
- 🐍 **Python**: 3.9+, type hints required, Black formatting
- 🧪 **Testing**: pytest for unit/integration; Playwright for E2E
- 📝 **Documentation**: Update README and docstrings for public APIs
- 🔐 **Security**: No secrets in code; use environment variables

---

## 🗓️ Roadmap

### ✅ Completed (v5.0)
- Multi-agent grading pipeline with verification
- Universal file parser + archive handling
- Live SSE progress streaming
- CSV/JSON export + manual overrides

### 🚧 In Progress (v5.1)
- [ ] Docker Compose deployment template
- [ ] PostgreSQL backend option
- [ ] Bulk session operations (archive/delete)
- [ ] Instructor analytics dashboard

### 🔮 Planned (v6.0)
- [ ] Sandboxed code execution for test-case validation
- [ ] Plagiarism detection integration
- [ ] LMS sync (Canvas, Moodle, Blackboard)
- [ ] Multi-language rubric support

> 💡 Have a feature request? [Open an issue](https://github.com/yourorg/gradeforge/issues) or start a discussion!

---

## 📜 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for details.

```text
MIT License

Copyright (c) 2024 GradeForge Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## 🙏 Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/), [Jinja2](https://jinja.palletsprojects.com/), and [Tailwind CSS](https://tailwindcss.com/)
- LLM orchestration inspired by research in [automated assessment](https://dl.acm.org/topic/ccs2012/10010405.10010406)
- Security practices informed by [OWASP guidelines](https://owasp.org/)
- Thank you to our beta testers at [University Name] for invaluable feedback

---

> 🎓 **GradeForge**: Empowering educators with transparent, efficient, and trustworthy AI-assisted grading.  
> *Built by instructors, for instructors.*

[⬆ Back to Top](#-table-of-contents)
```
