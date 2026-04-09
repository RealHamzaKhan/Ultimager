# GradeForge

> AI-powered automated grading platform for university CS instructors.  
> Upload student submissions — get back rubric-grounded, evidence-cited, teacher-reviewable grades in minutes.

![Version](https://img.shields.io/badge/version-5.0.0-blue)
![Python](https://img.shields.io/badge/python-3.9%2B-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## Overview

GradeForge is a full-stack grading assistant that combines a **FastAPI backend**, a **server-rendered Jinja2 UI**, and a **multi-agent LLM pipeline** to grade student assignments at scale. It handles code, Jupyter notebooks, PDFs, Word documents, images, and ZIP/RAR/7z archives — then surfaces checkpoint-level grades with cited evidence directly in the browser.

The system works with **any OpenAI-compatible LLM provider** (NVIDIA NIM, OpenAI, Groq, Together AI, Ollama) — just set three environment variables.

Instructors retain full control: every AI decision is transparent, every score is editable, and uncertain results are flagged for manual review.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│              Browser (Jinja2 + Tailwind UI)           │
│   Sessions · Grading Progress · Results · Exports     │
└─────────────────────┬────────────────────────────────┘
                      │  REST + SSE
┌─────────────────────▼────────────────────────────────┐
│              FastAPI Backend  (v5.0.0)                │
│  28 endpoints  ·  SQLite persistence  ·  SSE stream   │
└─────────────────────┬────────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────────┐
│           Multi-Agent Grading Pipeline                │
│                                                       │
│  Orchestrator                                         │
│      │                                                │
│      ├─▶  Domain Judge    (LLM — one call per crit.)  │
│      ├─▶  Verifier        (deterministic)             │
│      ├─▶  Scorer          (deterministic)             │
│      └─▶  Critic          (LLM — holistic review)    │
└─────────────────────┬────────────────────────────────┘
                      │
          Any OpenAI-compatible LLM API
   (NVIDIA NIM · OpenAI · Groq · Together · Ollama)
```

### Pipeline Stages

| Stage | Type | Role |
|-------|------|------|
| **Orchestrator** | Coordinator | Routes files to criteria, dispatches agents, aggregates results |
| **Domain Judge** | LLM | Evaluates each criterion with partial credit (0 / 25 / 50 / 75 / 100%) and cited evidence |
| **Verifier** | Deterministic | Confirms evidence quotes actually exist in the submission text (fuzzy + exact match) |
| **Scorer** | Deterministic | Applies criterion caps, detects un-evaluated criteria, computes final scores |
| **Critic** | LLM | Flags quality issues and suspicious results for teacher review |

---

## Features

### Grading
- **Checkpoint-based grading** — each rubric criterion is decomposed into granular checkpoints; the LLM awards partial credit per checkpoint with cited evidence from the submission
- **Per-criterion file routing** — ZIP archives are extracted and each file is intelligently routed to the most relevant rubric criteria (no all-files-to-all-criteria poisoning)
- **AI rubric generation** — paste an assignment description and the system generates a structured rubric with mark allocations
- **Multi-format support** — Python, Jupyter notebooks (`.ipynb`), PDFs (text + OCR vision), Word documents, Excel, PowerPoint, plain text, images
- **Archive support** — ZIP, RAR, 7z (with ZIP-bomb and path-traversal protection)
- **Parallel grading** — up to 8 students graded concurrently; one broken submission never stalls the batch
- **Multi-pass grading** — large submissions that exceed the model's context window are split into overlapping windows and merged
- **Vision pre-analysis** — extracts and transcribes diagrams, screenshots, and scanned pages before grading
- **Relevance gating** — off-topic submissions are flagged before wasting API calls

### Transparency & Safety
- **Evidence-cited scores** — every checkpoint result includes an exact quote from the submission
- **Unverified score flags** — checkpoints where the evidence could not be found in the submission text are flagged with an amber warning ("AI judgment only")
- **Truncation alerts** — submissions longer than `JUDGE_CONTENT_LIMIT` (default 60 000 chars) trigger a teacher banner
- **Routing fallback detection** — if file-to-criteria routing fails, the system skips rather than poisoning grades
- **Not-evaluated criteria** — rubric criteria that received no checkpoints are explicitly marked `not_evaluated`
- **Score capping transparency** — when checkpoint scores exceed a criterion maximum, capping is applied and flagged

### Teacher UX
- **Live grading stream** — SSE-based real-time progress with per-student status and ETA
- **Stop button** — halts backend grading within ~2 seconds
- **Session dashboard** — all students, scores, confidence levels, and review flags at a glance
- **Rubric breakdown panel** — per-criterion score with expandable checkpoints and evidence quotes
- **File browser** — view any student file inline (code with syntax highlighting, PDFs, text)
- **Manual override** — edit any AI score with a free-text justification; overrides tracked separately
- **Flag / unflag** — mark any submission for manual review with a reason
- **Regrade** — re-run AI grading per student or in bulk at any time
- **Export** — CSV and JSON export of complete gradebooks

### Advanced
- **ACMAG** (Anchor-Calibrated Multi-Agent Grading) — optional multi-rater calibration with inter-rater agreement tracking (Cohen's kappa). Off by default; enable with `ACMAG_ENABLED=1`.
- **Deterministic outputs** — `temperature=0, seed=42` on all judge calls so the same submission always gets the same grade
- **Rate-limit-aware** — smart async queuing respects your provider's RPM limit; no 429 errors

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.9+ | `python3 --version` |
| An LLM API key | — | See [Supported Providers](#supported-providers) below |

No Node.js or separate frontend build step required — the UI is server-rendered.

---

## Quick Start

### 1. Clone and set up

```bash
git clone https://github.com/RealHamzaKhan/Ultimager.git
cd Ultimager

# Create virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — at minimum set LLM_API_KEY
```

### 3. Run

```bash
python run.py
```

Open **http://localhost:8000** in your browser.

> Hot reload is off by default (to avoid interrupting grading jobs).  
> To enable it during development: `UVICORN_RELOAD=1 python run.py`

---

## Supported Providers

Switch providers by changing three lines in `.env`:

| Provider | `LLM_BASE_URL` | `LLM_MODEL` | Notes |
|----------|---------------|-------------|-------|
| **NVIDIA NIM** *(default)* | `https://integrate.api.nvidia.com/v1` | `meta/llama-4-maverick-17b-128e-instruct` | Free tier at [build.nvidia.com](https://build.nvidia.com) |
| **OpenAI** | `https://api.openai.com/v1` | `gpt-4o` | |
| **Groq** | `https://api.groq.com/openai/v1` | `llama3-70b-8192` | Free tier; set `RATE_LIMIT_RPM=30` |
| **Together AI** | `https://api.together.xyz/v1` | `meta-llama/Llama-3-70b-chat-hf` | |
| **Ollama** *(local)* | `http://localhost:11434/v1` | `llama3` | Set `LLM_API_KEY=ollama` |

Any other OpenAI-compatible endpoint works the same way.

---

## Configuration

All configuration lives in `.env`. Copy `.env.example` to get started. Only `LLM_API_KEY` is required.

```env
# ── Required ──────────────────────────────────────────────────────────────────
LLM_API_KEY=your_api_key_here

# ── LLM Provider (defaults to NVIDIA NIM + Llama 4 Maverick) ─────────────────
LLM_BASE_URL=https://integrate.api.nvidia.com/v1
LLM_MODEL=meta/llama-4-maverick-17b-128e-instruct

# Optional: cheaper/faster model just for rubric generation
# LLM_RUBRIC_MODEL=meta/llama-4-maverick-17b-128e-instruct

# ── Rate limiting ─────────────────────────────────────────────────────────────
# Match your provider's RPM limit. NVIDIA free=40, OpenAI tier-1=500, Groq free=30
RATE_LIMIT_RPM=40

# Number of students graded in parallel — lower if hitting rate limits
PARALLEL_GRADING_WORKERS=8

# ── Timeouts & context ────────────────────────────────────────────────────────
LLM_TIMEOUT=120.0           # Seconds per API call
MODEL_CONTEXT_TOKENS=128000 # Match your model (GPT-4o / Llama 4 = 128000, Groq = 8192)
JUDGE_CONTENT_LIMIT=60000   # Max chars of student content per grading call

# ── Vision ────────────────────────────────────────────────────────────────────
ENABLE_VISION_PREANALYSIS=1         # Transcribe diagrams/screenshots before grading
NVIDIA_MAX_IMAGES_PER_REQUEST=8     # Images sent per judge call

# ── ACMAG (advanced — leave off unless you need calibrated multi-rater grading)
ACMAG_ENABLED=0
```

---

## Usage

### Creating a session

1. Click **+ New Session**
2. Enter a title and assignment description
3. Either paste a rubric manually **or** click **Generate Rubric** — the AI will produce a structured rubric with mark allocations from your description
4. Upload the ZIP file containing all student submission folders
5. Click **Grade** — live progress appears via SSE stream

### Submission format

Students should be in individual folders inside the ZIP:

```
submissions.zip
├── 22P-0001_Ali/
│   ├── task1.py
│   └── task2.ipynb
├── 22P-0002_Sara/
│   └── assignment.zip        ← nested ZIPs are extracted automatically
└── 22P-0003_Ahmed/
    └── report.pdf
```

The system handles nested ZIPs, RAR and 7z archives, and mixed file types automatically.

### Reviewing results

Each student card shows:
- **Score badge** — colour-coded (green ≥ 80 %, amber 50–79 %, red < 50 %)
- **Confidence level** — High / Medium / Low based on verifier agreement
- **Review flags** — amber banners for anything needing teacher attention

Expand any rubric criterion to see:
- Individual checkpoint scores with evidence quotes from the submission
- ✅ Green check = evidence verified in submission text
- ⚠️ Amber triangle = AI judgment only (evidence not found in text)

### Stopping / restarting grading

Click **Stop** on the session page — grading halts within ~2 seconds.  
Click **Grade** again (or **Regrade All**) to resume or restart.

### Exporting

Click **CSV** or **JSON** in the results toolbar to download the full gradebook.

---

## Project Structure

```
gradeforge/
├── app/
│   ├── main.py                      # FastAPI app + all 28 REST/HTML endpoints
│   ├── config.py                    # All settings (env-configurable constants)
│   ├── models.py                    # SQLAlchemy ORM models
│   ├── schemas.py                   # Pydantic request/response schemas
│   ├── database.py                  # SQLite session management
│   ├── templates/                   # Jinja2 server-rendered pages
│   │   ├── base.html
│   │   ├── index.html               # Session list (home)
│   │   ├── new_session.html         # Create session + rubric generator
│   │   ├── session.html             # Grading dashboard + live progress
│   │   └── results.html             # Results analytics + student cards
│   ├── static/                      # CSS, JS, icons
│   └── services/
│       ├── agents/
│       │   ├── base.py              # CheckpointResult, GradingResult dataclasses
│       │   ├── orchestrator.py      # Main pipeline coordinator
│       │   ├── domain_judge.py      # LLM checkpoint evaluator (partial credit)
│       │   ├── verifier.py          # Deterministic evidence verifier
│       │   ├── scorer.py            # Score aggregation + capping
│       │   └── critic.py            # Quality flags + review recommendations
│       ├── ai_grader_fixed.py       # Core grading engine + rubric generation
│       ├── checkpoint_grader.py     # File routing + checkpoint generation
│       ├── parallel_grader.py       # Concurrent student grading + stop signal
│       ├── file_parser_enhanced.py  # Universal file ingestion pipeline
│       ├── zip_processor.py         # Archive extraction + security validation
│       ├── exporter.py              # CSV/JSON export
│       └── code_executor.py         # Sandboxed test execution
├── tests/
│   ├── unit/                        # 12 unit test files (pytest)
│   ├── integration/                 # 6 integration test files
│   ├── e2e/                         # End-to-end tests
│   └── conftest.py
├── test_datasets/                   # Sample submissions for local testing
├── requirements.txt
├── .env.example
├── pytest.ini
└── run.py                           # Uvicorn entry point (port 8000)
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check — `{"status":"healthy","version":"5.0.0"}` |
| `GET` | `/api/sessions` | List all sessions with summary stats (JSON) |
| `GET` | `/api/session/{id}/students` | List students with scores and flags (JSON) |
| `POST` | `/api/generate-rubric` | AI-generate a rubric from assignment description |
| `GET` | `/api/rubric-templates` | Return 5 built-in rubric templates |
| `GET` | `/session/new` | New session form (HTML) |
| `POST` | `/session/new` | Create a grading session |
| `POST` | `/session/{id}/upload` | Upload student ZIP archive |
| `POST` | `/session/{id}/grade` | Start grading all students |
| `POST` | `/session/{id}/stop-grading` | Halt grading in progress |
| `POST` | `/session/{id}/regrade-all` | Re-grade all students |
| `POST` | `/session/{id}/retry-failed` | Re-grade only errored submissions |
| `GET` | `/session/{id}/grade-stream` | SSE stream of live grading progress |
| `GET` | `/session/{id}/status` | Current session status (JSON) |
| `GET` | `/session/{id}/results` | Results dashboard (HTML) |
| `GET` | `/session/{id}/export/csv` | Download gradebook as CSV |
| `GET` | `/session/{id}/export/json` | Download gradebook as JSON |
| `POST` | `/session/{id}/student/{sid}/regrade` | Re-grade one student |
| `POST` | `/session/{id}/student/{sid}/override` | Manual score override |
| `POST` | `/session/{id}/student/{sid}/flag` | Flag submission for review |
| `POST` | `/session/{id}/student/{sid}/unflag` | Clear review flag |
| `GET` | `/session/{id}/student/{sid}/files` | List student's files with metadata |
| `GET` | `/session/{id}/student/{sid}/file/{path}` | Serve a student file inline |
| `GET` | `/session/{id}/student/{sid}/ingestion-report` | File parsing transparency report |
| `POST` | `/session/{id}/delete` | Delete session and all data |

---

## Result Format

Key fields in the JSON export per student:

```jsonc
{
  "student_identifier": "22P-0001",
  "status": "graded",
  "ai_score": 8.5,
  "ai_letter_grade": "A",
  "ai_confidence": "high",
  "final_score": 8.5,        // same as ai_score unless manually overridden
  "is_overridden": false,
  "ai_result": {
    "total_score": 8.5,
    "max_score": 10.0,
    "percentage": 85.0,
    "letter_grade": "A",
    "overall_feedback": "...",
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
            "verified": true,           // false = amber "AI judgment only" flag
            "evidence_quote": "def get_grade(marks): ...",
            "source_file": "task1.py",
            "reasoning": "The function correctly maps numerical marks...",
            "confidence": "high",
            "model_used": "meta/llama-4-maverick-17b-128e-instruct",
            "judge_truncated": false    // true if submission was trimmed
          }
        ]
      }
    ]
  }
}
```

---

## Supported File Types

| Category | Extensions |
|----------|-----------|
| **Code** | `.py` `.java` `.cpp` `.c` `.js` `.ts` `.cs` `.go` `.rb` `.php` `.swift` `.kt` `.scala` `.rs` `.sh` `.sql` `.html` `.css` `.r` `.m` |
| **Notebooks** | `.ipynb` (Jupyter) |
| **Documents** | `.pdf` `.docx` `.pptx` `.xlsx` `.xls` `.txt` `.md` |
| **Images** | `.png` `.jpg` `.jpeg` `.gif` `.bmp` `.webp` |
| **Archives** | `.zip` `.rar` `.7z` (including nested) |

---

## Running Tests

```bash
source venv/bin/activate

# Unit tests
pytest tests/unit/ -v

# Integration tests (requires running server)
pytest tests/integration/ -v

# All tests with coverage
pytest tests/ --cov=app --cov-report=term-missing
```

---

## Troubleshooting

**Server won't start**
```bash
# Make sure you're using the project's venv
source venv/bin/activate
python run.py
```

**Rate limit errors (429)**  
Lower `PARALLEL_GRADING_WORKERS` or `RATE_LIMIT_RPM` in `.env` to match your provider's free tier.

**Students scoring 0 with no error**  
Check `server.log` for `RELEVANCE` warnings — the submission may have been flagged as off-topic. Verify the rubric description matches what students actually submitted.

**Large submissions being truncated**  
Increase `JUDGE_CONTENT_LIMIT` in `.env`. Default is 60 000 chars; max safe value depends on your model's context window.

**Grading stalls / doesn't complete**  
Check `server.log` for timeout errors. Increase `LLM_TIMEOUT` or switch to a faster provider (Groq has very low latency).

---

## Contributing

1. Fork and create a feature branch from `main`
2. Make your changes with tests
3. Run `pytest tests/unit/ -v` — all tests must pass
4. Open a pull request with a clear description

---

## License

MIT — see [LICENSE](LICENSE) for details.
